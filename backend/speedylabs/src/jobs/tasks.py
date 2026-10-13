"""Background tasks.

`BACKEND.md` §7: jobs are safe to run twice, retry with growing waits, and land in a
failed state after the last try. Both of those are properties of `parse_source_file`
rather than aspirations:

- **Idempotent on `ingest_run_id`.** The status transition is a conditional `UPDATE ...
  WHERE status = 'queued'`, so a second delivery of the same job finds nothing to do and
  returns. A row count is therefore written once no matter how many times the job runs.
- **Terminal failure is recorded on the run**, not only in the queue. A run that failed
  shows *why* in the UI, because a job table nobody looks at is not an answer to "what
  happened to my upload?".

The task is synchronous on purpose. Parsing is CPU-and-IO bound with no concurrency to
win, procrastinate runs sync tasks in a thread pool, and a sync body keeps the parsers
free of async plumbing - which is what lets them stay framework-free for the portability
claim in `docs/decisions.md`.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from uuid import UUID

import psycopg
from procrastinate import JobContext
from procrastinate.exceptions import AlreadyEnqueued
from psycopg.types.json import Json

from src import config
from src.features.attribution.attribute import run_attribution
from src.features.canonical.materialize import materialize
from src.features.drift.run import DriftOutcome, run_drift
from src.features.layouts.document import (
    KIND_FIXED_WIDTH,
    LayoutDocument,
    build_csv_document,
    build_fixed_width_document,
)
from src.features.legacy.importer import run_import as run_legacy_import
from src.features.readiness.run import run_readiness
from src.features.rebates.reference_engine import ReferenceRebateEngine
from src.features.rebates.run import run_calculation
from src.features.specs.apply import check_fit, layout_fields_from_spec
from src.features.variance.compute import run_computation as run_variance_computation
from src.jobs.app import app, to_libpq_dsn
from src.lib import canonjson, db, logger
from src.lib.errors import AppError, DependencyUnavailable
from src.lib.ids import uuid7
from src.lib.parsers.csv_reader import iter_csv_rows, read_csv_shape
from src.lib.parsers.detect import FORMAT_FIXED_WIDTH
from src.lib.parsers.fwf_infer import infer_layout
from src.lib.parsers.fwf_reader import (
    FixedWidthShape,
    SliceField,
    iter_fixed_width_rows,
    looks_like_trailer,
    read_fixed_width_shape,
)
from src.lib.parsers.quirks import FieldQuirks, detect_quirks
from src.lib.profiling.accumulator import ColumnProfile, profile_rows
from src.lib.storage import LocalStorage
from src.lib.timeutil import now, to_iso
from src.lib.timing import MERGE_SQL, StageTimer, stage_timing_param

log = logger.get_logger(__name__)


_queue_open = False


async def ensure_queue_open() -> None:
    """Open the queue's pool once, on first use.

    Not at startup: that made the app's boot depend on the database, which §7 assigns
    to `/readyz` instead, and cost every test a connection attempt against whatever URL
    was configured - 53s to 172s on a 90s budget.
    """
    global _queue_open
    if _queue_open:
        return
    await app.open_async()
    _queue_open = True


async def close_queue() -> None:
    """Close the pool on shutdown, if it was ever opened."""
    global _queue_open
    if not _queue_open:
        return
    try:
        await app.close_async()
    finally:
        _queue_open = False


def _connect() -> psycopg.Connection[Any]:
    """A plain psycopg connection.

    The worker runs sync tasks, and reaching for the async SQLAlchemy engine from a
    thread would mean bridging event loops for three statements. One connection per job
    is cheap at this volume and keeps the task readable.
    """
    return psycopg.connect(to_libpq_dsn(config.load().database_url))


def ping_queueing_lock(token: str) -> str:
    return f"ping:{token}"


@app.task(name="ping")
def ping(token: str = "default") -> dict[str, Any]:
    """Proves the loop end to end. No side effects, so running twice is harmless."""
    log.info("ping", token=token, at=to_iso(now()))
    return {"token": token, "at": to_iso(now())}


def defer_ping(token: str) -> int:
    job = ping.configure(queueing_lock=ping_queueing_lock(token)).defer(token=token)
    return int(job)


#: The only file kind `parse_source_file` is about. Everything downstream of it - layout
#: inference, the spec lookup, profiling, drift, the canonical mapping - assumes invoice lines.
ERP_KIND: Final = "erp_invoice_lines"
#: The only file kind `import_legacy_rebates` is about.
LEGACY_KIND: Final = "legacy_rebate"


def _kind_of(conn: psycopg.Connection[Any], run_id: UUID) -> str | None:
    """This run's file kind, or `None` if the run or its file has gone."""
    with conn.cursor() as cur:
        cur.execute(
            "select f.kind from ingest_runs r join source_files f on f.id = r.source_file_id "
            "where r.id = %s",
            (run_id,),
        )
        row = cur.fetchone()
    return None if row is None else str(row[0])


def parse_source_file(ingest_run_id: str, *, attempt: int = 1) -> dict[str, Any]:
    """Count the rows in an uploaded file and move the run to `parsed`.

    **ERP invoice files only.** Nothing downstream of this task reads `source_files.kind`, and until
    this guard existed a `legacy_rebate` upload was parsed and profiled as though it were an invoice
    export: measured against the running stack, it settled at `profiled` with a layout inferred over
    the legacy file's five columns. Worse once the customer has an approved spec, because
    `_spec_for` keys on the *layout* format rather than the file's kind - so the legacy file matches
    the ERP spec and is stopped only by the header fit check, which files it as an ERP file that
    failed to fit, and drift then blocks the run over a "change" in a file that was never an invoice
    export. A user could reach all of this: `upload-card.tsx` has offered "Legacy rebate export" in
    its kind select since Phase 2.

    The kind is read **before the claim**, deliberately. A run deferred to the wrong task is then
    left at `queued` - recoverable by deferring the right one - rather than stranded at `parsing` or
    marked `failed` for something that is not the file's fault.
    """
    run_id = UUID(ingest_run_id)
    settings = config.load()
    elapsed = StageTimer()

    with _connect() as conn:
        kind = _kind_of(conn, run_id)
        if kind is not None and kind != ERP_KIND:
            log.warning(
                "parse_refused_wrong_kind",
                task="parse_source_file",
                ingest_run_id=str(run_id),
                file_kind=kind,
                expected_kind=ERP_KIND,
            )
            return {"ingestRunId": str(run_id), "skipped": True, "reason": "wrong_kind"}

        # Claim the run. Conditional on 'queued', so a redelivered job is a no-op
        # rather than a second parse.
        with conn.cursor() as cur:
            cur.execute(
                "update ingest_runs set status = 'parsing', updated_at = now() "
                "where id = %s and status = 'queued' returning source_file_id",
                (run_id,),
            )
            claimed = cur.fetchone()
        conn.commit()

        if claimed is None:
            log.info("parse_skipped_not_queued", ingest_run_id=str(run_id))
            return {"ingestRunId": str(run_id), "skipped": True}

        source_file_id = claimed[0]
        with conn.cursor() as cur:
            cur.execute(
                "select stored_name, format from source_files where id = %s",
                (source_file_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"source file {source_file_id} vanished")
        stored_name, file_format = row

        storage = LocalStorage(settings.upload_dir)
        path = storage.open_path(stored_name)

        try:
            fixed_width_shape: FixedWidthShape | None = None
            if file_format == FORMAT_FIXED_WIDTH:
                fixed_width_shape = read_fixed_width_shape(path, max_rows=settings.max_upload_rows)
                row_count = fixed_width_shape.row_count
                detail = {"recordWidth": fixed_width_shape.record_width}
            else:
                csv_shape = read_csv_shape(path, max_rows=settings.max_upload_rows)
                row_count = csv_shape.row_count
                detail = {"columns": len(csv_shape.header)}
            # Inside the same guard as the shape read: a file whose boundaries cannot be
            # worked out is a file the user must fix, and it should fail with that
            # message rather than parse cleanly and then break profiling.
            layout_document = _layout_for(path, file_format, fixed_width_shape)
        except AppError as exc:
            # A refusal the user can act on: record it on the run so the UI can say
            # what went wrong, and do not retry - the file will not parse next time
            # either.
            with conn.cursor() as cur:
                cur.execute(
                    "update ingest_runs set status = 'failed', error_code = %s, "
                    "error_message = %s, updated_at = now() where id = %s",
                    (exc.code, exc.message, run_id),
                )
            conn.commit()
            log.warning(
                "parse_failed", ingest_run_id=str(run_id), code=exc.code, reason=exc.message
            )
            return {"ingestRunId": str(run_id), "status": "failed", "errorCode": exc.code}

        # The approved spec, if this customer has one for this format. Consulted **here**,
        # before version 1 is written: for a fixed-width file the spec's boundaries have to
        # *become* version 1 rather than be compared with an inferred one, because inference
        # names fields positionally and a different month's data can merge two and renumber
        # every one after - see `features/specs/apply.py`.
        spec_id: UUID | None = None
        skip_code: str | None = None
        skip_detail: dict[str, Any] | None = None
        try:
            found = _spec_for(conn, source_file_id, layout_document.kind)
            if found is not None:
                spec_id, spec_document = found
                fit = check_fit(
                    spec_document,
                    kind=layout_document.kind,
                    header=[str(field["name"]) for field in layout_document.fields],
                    record_width=layout_document.record_width,
                )
                if fit.ok:
                    if layout_document.kind == KIND_FIXED_WIDTH:
                        layout_document = _apply_spec_to_layout(
                            layout_document, spec_document, spec_id
                        )
                    log.info(
                        "spec_applied",
                        ingest_run_id=str(run_id),
                        spec_id=str(spec_id),
                        kind=layout_document.kind,
                    )
                else:
                    skip_code, skip_detail = fit.code, fit.detail
                    log.info(
                        "spec_skipped",
                        ingest_run_id=str(run_id),
                        spec_id=str(spec_id),
                        code=skip_code,
                    )
        except Exception as error:
            # A spec is an optimisation, never a precondition for parsing. A lookup that fails
            # leaves the run looking exactly like a customer with no spec, which is a state the
            # product already handles, rather than failing a file that is perfectly readable.
            spec_id, skip_code, skip_detail = None, None, None
            log.warning("spec_lookup_failed", ingest_run_id=str(run_id), error=type(error).__name__)

        with conn.cursor() as cur:
            cur.execute(
                "update source_files set row_count = %s, updated_at = now() where id = %s",
                (row_count, source_file_id),
            )
            cur.execute(
                "update ingest_runs set considered_spec_id = %s, spec_skip_code = %s, "
                "spec_skip_detail = %s where id = %s",
                (spec_id, skip_code, Json(skip_detail) if skip_detail else None, run_id),
            )
            _write_layout_v1(cur, source_file_id, layout_document)
            cur.execute(
                f"update ingest_runs set status = 'parsed', parsed_at = %s, {MERGE_SQL}, "
                "updated_at = now() where id = %s",
                (
                    datetime.now(UTC),
                    stage_timing_param("parse", elapsed.ms, attempt),
                    run_id,
                ),
            )
        conn.commit()

    log.info(
        "parsed",
        ingest_run_id=str(run_id),
        row_count=row_count,
        layout_fields=len(layout_document.fields),
        **detail,
    )

    # Chain profiling. Deferred as a separate job rather than inlined so a slow profile
    # cannot make the parse look stuck, and so a profile failure leaves a `parsed` run
    # that a human can still read rather than reverting the parse.
    profile_source_file_task.configure(queueing_lock=f"profile:{run_id}").defer(
        ingest_run_id=str(run_id)
    )

    return {"ingestRunId": str(run_id), "status": "parsed", "rowCount": row_count, **detail}


async def defer_parse(ingest_run_id: UUID) -> int:
    """Queue a parse. One job per run, enforced by the queueing lock.

    Async because the caller is an async route: the sync `defer` reaches for
    procrastinate's *sync* connector, whose pool only the worker process opens, and
    fails with `AppNotOpen` inside the request.
    """
    try:
        await ensure_queue_open()
        job = await parse_source_file_task.configure(
            queueing_lock=f"parse:{ingest_run_id}"
        ).defer_async(ingest_run_id=str(ingest_run_id))
    except Exception as exc:
        # An unreachable queue is a 503 with a §9 message, not a 500: the caller's file
        # is fine and retrying later will work.
        raise DependencyUnavailable(
            "Uploads are briefly unavailable. Try again in a moment."
        ) from exc
    return int(job)


_UPSERT_PROFILE = """
    insert into column_profiles (
        id, source_file_id, layout_version, column_index, column_name, type_guess,
        row_count, null_count, null_pct, distinct_count, distinct_capped,
        min_text, max_text, date_format, sample_values, patterns,
        value_counts, numeric_count, numeric_sum
    ) values (
        %(id)s, %(source_file_id)s, %(layout_version)s, %(column_index)s, %(column_name)s,
        %(type_guess)s, %(row_count)s, %(null_count)s, %(null_pct)s, %(distinct_count)s,
        %(distinct_capped)s, %(min_text)s, %(max_text)s, %(date_format)s,
        %(sample_values)s, %(patterns)s,
        %(value_counts)s, %(numeric_count)s, %(numeric_sum)s
    )
    on conflict (source_file_id, layout_version, column_index) do update set
        column_name = excluded.column_name,
        type_guess = excluded.type_guess,
        row_count = excluded.row_count,
        null_count = excluded.null_count,
        null_pct = excluded.null_pct,
        distinct_count = excluded.distinct_count,
        distinct_capped = excluded.distinct_capped,
        min_text = excluded.min_text,
        max_text = excluded.max_text,
        date_format = excluded.date_format,
        sample_values = excluded.sample_values,
        patterns = excluded.patterns,
        value_counts = excluded.value_counts,
        numeric_count = excluded.numeric_count,
        numeric_sum = excluded.numeric_sum,
        updated_at = now()
"""


def profile_source_file(ingest_run_id: str, *, attempt: int = 1) -> dict[str, Any]:
    """Profile every column of a parsed file.

    Idempotent through the upsert on (source_file_id, layout_version, column_index):
    a redelivered job rewrites the same rows rather than duplicating them, so there is
    exactly one row per column however many times this runs.
    """
    elapsed = StageTimer()
    run_id = UUID(ingest_run_id)
    settings = config.load()

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select source_file_id, status from ingest_runs where id = %s", (run_id,))
            row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"ingest run {run_id} vanished")
        source_file_id, status = row

        # Only a parsed run can be profiled. Anything else means the job arrived early
        # or twice; returning is correct and silent.
        if status not in ("parsed", "profiled"):
            log.info("profile_skipped_wrong_status", ingest_run_id=str(run_id), status=status)
            return {"ingestRunId": str(run_id), "skipped": True, "status": status}

        with conn.cursor() as cur:
            cur.execute(
                "select stored_name, format from source_files where id = %s", (source_file_id,)
            )
            file_row = cur.fetchone()
        if file_row is None:
            raise RuntimeError(f"source file {source_file_id} vanished")
        stored_name, file_format = file_row

        storage = LocalStorage(settings.upload_dir)
        path = storage.open_path(stored_name)

        if not path.exists():
            # The message names no path: §3 forbids returning file paths, and the
            # operator can find it from the run id in the logs.
            with conn.cursor() as cur:
                cur.execute(
                    "update ingest_runs set status = 'failed', error_code = %s, "
                    "error_message = %s, updated_at = now() where id = %s",
                    (
                        "source_file_missing",
                        "The uploaded file is no longer available. Upload it again.",
                        run_id,
                    ),
                )
            conn.commit()
            log.error("source_file_missing", ingest_run_id=str(run_id))
            return {
                "ingestRunId": str(run_id),
                "status": "failed",
                "errorCode": "source_file_missing",
            }

        # What the parse job decided about this customer's spec. Read rather than re-derived: the
        # fit check ran against the file as parsed, and running it again here could reach a
        # different answer if anything changed in between - which would leave `considered_spec_id`
        # describing one decision and the draft another.
        with conn.cursor() as cur:
            cur.execute(
                "select r.considered_spec_id, r.spec_skip_code, s.spec, r.spec_skip_detail "
                "from ingest_runs r left join mapping_specs s on s.id = r.considered_spec_id "
                "where r.id = %s",
                (run_id,),
            )
            spec_row = cur.fetchone()
        considered_spec_id = None
        spec_skip_code: str | None = None
        spec_skip_detail: dict[str, Any] | None = None
        spec_mapping: dict[str, Any] = {}
        spec_schema_version = ""
        if spec_row is not None and spec_row[0] is not None:
            spec_skip_code = spec_row[1]
            spec_skip_detail = dict(spec_row[3]) if spec_row[3] else None
        if spec_row is not None and spec_row[0] is not None and spec_row[1] is None:
            considered_spec_id = spec_row[0]
            document = dict(spec_row[2] or {})
            # `origin: "spec"` on every field: the mapping screen shows where each came from, and
            # a field remembered from an approved document is neither a human's choice nor a
            # model's guess. A human patching one field flips that one to `manual`, which is the
            # behaviour `propose` already relies on.
            spec_mapping = {
                name: {**entry, "origin": "spec", "confidence": None, "reason": None}
                for name, entry in dict(document.get("mapping") or {}).items()
            }
            spec_schema_version = str(document.get("canonicalSchemaVersion") or "")

        # Which layout to profile under. The current (highest) version, so a re-profile
        # after a human edits boundaries describes the edited layout and stores its
        # statistics against that version rather than overwriting version 1's.
        with conn.cursor() as cur:
            cur.execute(
                "select version, kind, fields, record_width from file_layouts "
                "where source_file_id = %s order by version desc limit 1",
                (source_file_id,),
            )
            layout_row = cur.fetchone()

        if layout_row is None:
            # A file parsed before layouts existed. Work one out now and store it, so
            # this file is not permanently unprofilable and `GET .../layout` answers.
            # The shape is re-read here because this path has no parse to inherit it from;
            # one extra pass on a backfill is cheaper than a layout with no footer facts.
            document = _layout_for(
                path,
                file_format,
                read_fixed_width_shape(path, max_rows=settings.max_upload_rows)
                if file_format == FORMAT_FIXED_WIDTH
                else None,
            )
            with conn.cursor() as cur:
                _write_layout_v1(cur, source_file_id, document)
            conn.commit()
            log.info("layout_backfilled", ingest_run_id=str(run_id), kind=document.kind)
            layout_version, layout_kind, layout_fields = 1, document.kind, document.fields
            record_width = document.record_width
        else:
            layout_version, layout_kind, layout_fields, record_width = layout_row

        try:
            if layout_kind == KIND_FIXED_WIDTH:
                profiles = _profile_fixed_width(path, _slice_fields_from_layout(layout_fields))
            else:
                profiles = _profile_csv(path)
        except AppError as exc:
            with conn.cursor() as cur:
                cur.execute(
                    "update ingest_runs set status = 'failed', error_code = %s, "
                    "error_message = %s, updated_at = now() where id = %s",
                    (exc.code, exc.message, run_id),
                )
            conn.commit()
            log.warning("profile_failed", ingest_run_id=str(run_id), code=exc.code)
            return {"ingestRunId": str(run_id), "status": "failed", "errorCode": exc.code}

        with conn.cursor() as cur:
            for profile in profiles:
                cur.execute(
                    _UPSERT_PROFILE,
                    {
                        "id": uuid7(),
                        "source_file_id": source_file_id,
                        "layout_version": layout_version,
                        "column_index": profile.column_index,
                        "column_name": profile.column_name,
                        "type_guess": profile.type_guess,
                        "row_count": profile.row_count,
                        "null_count": profile.null_count,
                        "null_pct": profile.null_pct,
                        "distinct_count": profile.distinct_count,
                        "distinct_capped": profile.distinct_capped,
                        "min_text": profile.min_text,
                        "max_text": profile.max_text,
                        "date_format": profile.date_format,
                        "sample_values": Json(profile.sample_values),
                        "patterns": Json(profile.patterns),
                        "value_counts": (
                            Json(profile.value_counts) if profile.value_counts is not None else None
                        ),
                        "numeric_count": profile.numeric_count,
                        "numeric_sum": profile.numeric_sum,
                    },
                )
            # The spec's mapping becomes the draft, in the **same transaction** as the profiles
            # and the status: a run that says `mapped` with no draft behind it would be a lie
            # that survives a crash, and a draft with no status would never be noticed.
            #
            # `do nothing` on conflict: a human who mapped this file by hand before the profile
            # finished keeps their work. A spec is memory, not an override.
            # Drift, in this same transaction, after the profiles and before the status. One
            # transaction, so no run ever exists with its profiles written and its drift undecided.
            try:
                drift = run_drift(
                    conn,
                    run_id=str(run_id),
                    source_file_id=str(source_file_id),
                    layout_version=layout_version,
                    kind=layout_kind,
                    record_width=record_width,
                    spec_skip_code=spec_skip_code,
                    spec_skip_detail=spec_skip_detail,
                )
            except Exception as error:
                # **Fails closed.** A tool whose job is catching bad data has one safe failure
                # direction: a comparison that could not run blocks rather than waving the file
                # through. The run says so, and a human decides.
                log.error("drift_failed", ingest_run_id=str(run_id), error=type(error).__name__)
                drift = DriftOutcome(
                    compared=True,
                    blocking=1,
                    warnings=0,
                    reason="The drift comparison could not be completed for this file.",
                )

            mapped_from_spec = False
            if considered_spec_id is not None and spec_mapping:
                cur.execute(
                    _INSERT_DRAFT_FROM_SPEC,
                    {
                        "id": uuid7(),
                        "source_file_id": source_file_id,
                        "layout_version": layout_version,
                        "canonical_schema_version": spec_schema_version,
                        "mapping": Json(spec_mapping),
                    },
                )
                mapped_from_spec = cur.rowcount > 0

            # The baseline and the decision go on the run, once per comparison - the only way a
            # run with **zero** findings can still say what it was compared against.
            cur.execute(
                "update ingest_runs set drift_baseline_spec_id = %s, "
                "drift_baseline_source_file_id = %s, drift_decision = %s, drift_at = %s "
                "where id = %s",
                (
                    drift.baseline_spec_id,
                    drift.baseline_source_file_id,
                    "pending" if drift.blocking else None,
                    datetime.now(UTC) if drift.compared else None,
                    run_id,
                ),
            )

            if drift.blocking:
                # `drift_blocked` replaces whichever status this run would otherwise have reached.
                # The draft is still written when the spec fitted: a spec can fit and drift can
                # still block, and throwing away a correct mapping would make the human's decision
                # harder for no reason.
                cur.execute(
                    f"update ingest_runs set status = 'drift_blocked', profiled_at = %s, "
                    f"{MERGE_SQL}, updated_at = now() where id = %s",
                    (
                        datetime.now(UTC),
                        stage_timing_param("profile", elapsed.ms, attempt),
                        run_id,
                    ),
                )
            elif mapped_from_spec:
                cur.execute(
                    f"update ingest_runs set status = 'mapped', profiled_at = %s, "
                    f"{MERGE_SQL}, mapped_at = %s, updated_at = now() where id = %s",
                    (
                        datetime.now(UTC),
                        stage_timing_param("profile", elapsed.ms, attempt),
                        datetime.now(UTC),
                        run_id,
                    ),
                )
            else:
                cur.execute(
                    f"update ingest_runs set status = 'profiled', profiled_at = %s, "
                    f"{MERGE_SQL}, updated_at = now() where id = %s",
                    (
                        datetime.now(UTC),
                        stage_timing_param("profile", elapsed.ms, attempt),
                        run_id,
                    ),
                )
        conn.commit()

    log.info(
        "profiled",
        ingest_run_id=str(run_id),
        columns=len(profiles),
        layout_version=layout_version,
    )
    return {
        "ingestRunId": str(run_id),
        "status": "profiled",
        "columns": len(profiles),
        "layoutVersion": layout_version,
    }


def _profile_csv(path: Path) -> list[ColumnProfile]:
    """One pass over the file, bounded memory (see lib/profiling/accumulator)."""
    for header, rows in iter_csv_rows(path):
        return profile_rows(header, rows)
    return []


#: Records sampled for quirk detection. Detection is a vote, and a thousand records
#: settles any vote this file can hold - while bounding the slice cache to roughly
#: `fields x 1000` strings even for a 200-field layout.
QUIRK_SAMPLE_RECORDS = 1_000

_INSERT_LAYOUT = """
    insert into file_layouts (
        id, source_file_id, version, kind, encoding, delimiter, quote_char, has_header,
        header_row_index, footer_row_count, footer_declared_rows, footer_total,
        record_width, fields, inference_evidence,
        sample_records, inferred, edited_by_human, fields_from_spec_id
    ) values (
        %(id)s, %(source_file_id)s, %(version)s, %(kind)s, %(encoding)s, %(delimiter)s,
        %(quote_char)s, %(has_header)s, %(header_row_index)s, %(footer_row_count)s,
        %(footer_declared_rows)s, %(footer_total)s,
        %(record_width)s, %(fields)s, %(inference_evidence)s, %(sample_records)s,
        %(inferred)s, %(edited_by_human)s, %(fields_from_spec_id)s
    )
    on conflict (source_file_id, version) do nothing
"""


#: `do nothing`, so a draft a human created first is never overwritten by a remembered one.
#: The seeded demo's approved spec. Deliberately the same column list as `specs/repo.py`'s own
#: approval insert, including `version` as `max + 1`, so a seeded spec is indistinguishable from one
#: a person approved - the drift check reads it through the same query either way.
_INSERT_APPROVED_SPEC = """
    insert into mapping_specs (
        id, customer_id, kind, version, status, canonical_schema_version, spec, spec_sha256,
        approved_from_source_file_id, approved_from_layout_version, approved_note,
        approved_forced, approved_from_ip_hash, approved_at
    )
    select %(id)s, %(customer_id)s, %(kind)s, coalesce(max(version), 0) + 1, 'approved',
           %(canonical_schema_version)s, cast(%(spec)s as jsonb), %(spec_sha256)s,
           %(source_file_id)s, %(layout_version)s, %(note)s,
           false, %(ip_hash)s, now()
    from mapping_specs
    where customer_id = %(customer_id)s and kind = %(kind)s
    on conflict do nothing
"""


_INSERT_DRAFT_FROM_SPEC = """
    insert into mapping_drafts (
        id, source_file_id, layout_version, canonical_schema_version, mapping
    ) values (
        %(id)s, %(source_file_id)s, %(layout_version)s, %(canonical_schema_version)s,
        %(mapping)s
    )
    on conflict (source_file_id, layout_version) do nothing
"""


def _detect_field_quirks(path: Path, fields: list[SliceField]) -> dict[int, FieldQuirks]:
    """Slice a sample of records and classify each field's values.

    Values are collected per field rather than per record so `detect_quirks` sees a
    column, which is the unit a convention applies to. Padding is kept here - unlike the
    profiler's view - because trailing blanks are exactly the evidence `right_padded`
    votes on.
    """
    columns: list[list[str]] = [[] for _ in fields]
    with path.open("r", encoding="ascii", newline="") as handle:
        sampled = 0
        for raw in handle:
            record = raw.rstrip("\n").rstrip("\r")
            if not record.strip() or looks_like_trailer(record):
                continue
            for position, slice_field in enumerate(fields):
                columns[position].append(record[slice_field.start - 1 : slice_field.end])
            sampled += 1
            if sampled >= QUIRK_SAMPLE_RECORDS:
                break
    return {index: detect_quirks(values) for index, values in enumerate(columns)}


_SELECT_APPROVED_SPEC = """
    select s.id, s.spec, s.canonical_schema_version
    from mapping_specs s
    join source_files f on f.customer_id = s.customer_id
    where f.id = %(source_file_id)s and s.kind = %(kind)s and s.status = 'approved'
"""


def _spec_for(
    conn: psycopg.Connection[Any], source_file_id: UUID, kind: str
) -> tuple[UUID, dict[str, Any]] | None:
    """This customer's approved spec for this format, or `None`.

    Swallows nothing on purpose - the caller decides what a failure means - but a spec is an
    optimisation and never a precondition for parsing, so the caller treats an absent spec and an
    unreachable one the same way: carry on and infer.
    """
    with conn.cursor() as cur:
        cur.execute(_SELECT_APPROVED_SPEC, {"source_file_id": source_file_id, "kind": kind})
        row = cur.fetchone()
    if row is None:
        return None
    return UUID(str(row[0])), dict(row[1])


def _apply_spec_to_layout(
    document: LayoutDocument, spec_document: dict[str, Any], spec_id: UUID
) -> LayoutDocument:
    """Replace the inferred boundaries with the spec's, keeping every measured fact.

    What comes from the spec: the field list, and that alone. What stays measured on *this* file:
    the record width, the sample records the editor's ruler draws, and the footer facts - a month
    whose export carries no trailer must not inherit last month's declared total, which is the
    whole reason the spec document leaves those out.
    """
    return replace(
        document,
        fields=layout_fields_from_spec(spec_document),
        inferred=False,
        inference_evidence=None,
        fields_from_spec_id=spec_id,
    )


def _layout_for(
    path: Path, file_format: str, shape: FixedWidthShape | None = None
) -> LayoutDocument:
    """Work out version 1's layout from the file itself.

    `shape` is the whole-file read the caller has already done. Passed in rather than
    re-read: it is the only view that reaches the trailer, and reading a 48,151-line file
    twice to learn the same fact would be a second full pass for nothing.
    """
    if file_format == FORMAT_FIXED_WIDTH:
        inferred = infer_layout(path)
        slices = [
            SliceField(name=field.name, start=field.start, end=field.end)
            for field in inferred.fields
        ]
        return build_fixed_width_document(inferred, _detect_field_quirks(path, slices), shape=shape)
    csv_shape = read_csv_shape(path, max_rows=QUIRK_SAMPLE_RECORDS * 1_000)
    return build_csv_document(csv_shape.header, csv_shape.dialect)


def _write_layout_v1(
    cursor: psycopg.Cursor[Any], source_file_id: UUID, document: LayoutDocument
) -> None:
    """Insert version 1, or leave the existing one alone.

    `do nothing` rather than `do update`: version 1 is what inference said the first
    time, and a redelivered parse job must not rewrite it. If inference has since
    changed its mind, that is a new version, not a quiet correction of the old one -
    which is the whole point of versioning layouts additively.
    """
    cursor.execute(
        _INSERT_LAYOUT,
        {
            "id": uuid7(),
            "source_file_id": source_file_id,
            "version": 1,
            "kind": document.kind,
            "encoding": document.encoding,
            "delimiter": document.delimiter,
            "quote_char": document.quote_char,
            "has_header": document.has_header,
            "header_row_index": document.header_row_index,
            "footer_row_count": document.footer_row_count,
            "footer_declared_rows": document.footer_declared_rows,
            "footer_total": document.footer_total,
            "record_width": document.record_width,
            "fields": Json(document.fields),
            "inference_evidence": (
                Json(document.inference_evidence) if document.inference_evidence else None
            ),
            "sample_records": Json(document.sample_records),
            "inferred": document.inferred,
            "edited_by_human": False,
            "fields_from_spec_id": document.fields_from_spec_id,
        },
    )


def _slice_fields_from_layout(fields: list[dict[str, Any]]) -> list[SliceField]:
    """Read the stored field documents back into slices, in field order."""
    return [
        SliceField(name=entry["name"], start=int(entry["start"]), end=int(entry["end"]))
        for entry in sorted(fields, key=lambda entry: int(entry["index"]))
    ]


def _profile_fixed_width(path: Path, fields: list[SliceField]) -> list[ColumnProfile]:
    for names, rows in iter_fixed_width_rows(path, fields):
        return profile_rows(names, rows)
    return []


def reprofile_source_file(
    ingest_run_id: str, *, layout_version: int, attempt: int = 1
) -> dict[str, Any]:
    """Re-profile a file after a human edited its field boundaries.

    A separate task from `profile_source_file`, not a flag on it, for the queueing lock:
    the original profile holds `profile:<run>`, and an edit that reused that key would be
    discarded as a duplicate of a job that has already run. This one locks on the version
    too, so each edit gets its own job and a double-save of the same version does not.

    The work itself is the same code - `profile_source_file` already reads the current
    layout version and upserts against it, so re-profiling is profiling. `layout_version`
    is carried only to key the lock and to make the logs legible.
    """
    log.info("reprofile_requested", ingest_run_id=ingest_run_id, layout_version=layout_version)
    # The attempt is forwarded so a re-profile reports its own count rather than 1.
    return profile_source_file(ingest_run_id, attempt=attempt)


async def defer_reprofile(source_file_id: UUID, layout_version: int) -> None:
    """Queue a re-profile for the newest run of this file.

    Looks the run up here rather than taking it from the caller because a layout belongs
    to a *file*, while profiling status lives on a *run* - and the caller holds a file id.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select id from ingest_runs where source_file_id = %s order by created_at desc limit 1",
            (source_file_id,),
        )
        row = cur.fetchone()
    if row is None:
        # No run means nothing has been profiled, so there is nothing to recompute. The
        # layout version is still stored; the next profile will pick it up.
        log.info("reprofile_skipped_no_run", source_file_id=str(source_file_id))
        return

    run_id = row[0]
    try:
        await ensure_queue_open()
        await reprofile_source_file_task.configure(
            queueing_lock=f"reprofile:{run_id}:{layout_version}"
        ).defer_async(ingest_run_id=str(run_id), layout_version=layout_version)
    except Exception as exc:
        raise DependencyUnavailable(
            "The layout was saved, but recomputing the profile is briefly unavailable. "
            "Try saving again in a moment."
        ) from exc


#: Attempts procrastinate makes before giving up on a materialise. The last one is the
#: attempt that writes the failure onto the run, so the number is needed in two places and
#: lives here rather than inline in the decorator.
MATERIALIZE_ATTEMPTS = 3


def materialize_run(ingest_run_id: str, *, attempt: int = 1) -> dict[str, Any]:
    """The task wrapper: decide whether this is the last attempt, then do the work.

    Thin on purpose. The work is a plain function so a test can call it twice to prove
    idempotency without constructing a `JobContext`, and so "is this the final attempt?" -
    the only thing the job context is consulted for - stays in one line.
    """
    return run_materialization(
        ingest_run_id,
        final_attempt=attempt >= MATERIALIZE_ATTEMPTS,
        chain_readiness=True,
        # The only stage with retries worth distinguishing: "slow" and "retried twice" are
        # different problems and `attempts` is what tells them apart.
        attempts=attempt,
    )


def run_materialization(
    ingest_run_id: str,
    *,
    final_attempt: bool = True,
    chain_readiness: bool = False,
    attempts: int = 1,
) -> dict[str, Any]:
    """Apply a run's mapping to the whole file and land canonical rows.

    The claim already happened in the route (`canonical/repo.claim_for_materialize`), which
    is why this does not try to claim again: the route must answer `409` for a run that is
    already materialised, and a job cannot answer anything. What this does guarantee is the
    second property - re-running replaces, so a redelivered or retried job converges on
    exactly one set of rows (see `canonical/materialize.py`).
    """
    elapsed = StageTimer()
    run_id = UUID(ingest_run_id)
    settings = config.load()

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select source_file_id, status from ingest_runs where id = %s", (run_id,))
            row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"ingest run {run_id} vanished")
        source_file_id, status = row
        if status == "materialized":
            # A redelivery after the job already finished. Doing the work again would be
            # harmless but pointless, and it would move `materialized_at`.
            log.info("materialize_skipped_done", ingest_run_id=str(run_id))
            return {"ingestRunId": str(run_id), "skipped": True}

        with conn.cursor() as cur:
            cur.execute(
                """
                select l.kind, l.fields, d.mapping
                from file_layouts l
                join mapping_drafts d
                  on d.source_file_id = l.source_file_id and d.layout_version = l.version
                where l.source_file_id = %s
                order by l.version desc
                limit 1
                """,
                (source_file_id,),
            )
            mapping_row = cur.fetchone()
        if mapping_row is None:
            raise RuntimeError(f"ingest run {run_id} has no mapping to materialize")
        kind, fields, mapping_document = mapping_row

        try:
            result = materialize(
                conn,
                run_id=str(run_id),
                source_file_id=str(source_file_id),
                mapping_document=mapping_document,
                layout={"kind": kind, "fields": fields},
                upload_dir=settings.upload_dir,
            )
        except AppError as exc:
            # A refusal the user can act on. Recorded on the run and not retried: the file
            # and the mapping will not have changed by the next attempt.
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    "update ingest_runs set status = 'failed', error_code = %s, "
                    "error_message = %s, updated_at = now() where id = %s",
                    (exc.code, exc.message, run_id),
                )
            conn.commit()
            log.warning(
                "materialize_failed", ingest_run_id=str(run_id), code=exc.code, reason=exc.message
            )
            return {"ingestRunId": str(run_id), "status": "failed", "errorCode": exc.code}
        except Exception as exc:
            # Something we did not anticipate. The run must not be left saying
            # `materializing` for ever, which is what the UI polls on - but only the last
            # attempt may say `failed`, or a retry that then succeeded would have been
            # preceded by a lie. Re-raised either way, so procrastinate still retries and
            # still records the job as failed at the end.
            conn.rollback()
            if final_attempt:
                with conn.cursor() as cur:
                    cur.execute(
                        "update ingest_runs set status = 'failed', error_code = %s, "
                        "error_message = %s, updated_at = now() where id = %s",
                        (
                            "materialize_failed",
                            "Building the canonical rows did not finish. Check the file's "
                            "mapping, then try again.",
                            run_id,
                        ),
                    )
                conn.commit()
            log.error(
                "materialize_errored",
                ingest_run_id=str(run_id),
                final_attempt=final_attempt,
                error=type(exc).__name__,
            )
            raise

        with conn.cursor() as cur:
            cur.execute(
                f"update ingest_runs set status = 'materialized', materialized_at = %s, "
                f"{MERGE_SQL}, "
                "mapped_at = coalesce(mapped_at, %s), updated_at = now() where id = %s",
                (
                    datetime.now(UTC),
                    stage_timing_param("materialize", elapsed.ms, attempts),
                    datetime.now(UTC),
                    run_id,
                ),
            )
        conn.commit()

    # Chain the readiness checks. Deferred rather than inlined for the same reason profiling
    # is: a slow or broken check must not make a good materialise look failed, and a run with
    # rows but no checklist is still useful to a human.
    #
    # Off by default, and the task turns it on. Deferring needs procrastinate's app to be
    # open, which it is in the worker and is not in a test or a script calling this function
    # directly - and that call is how idempotency is proved, so it must not need a queue.
    if chain_readiness:
        run_readiness_task.configure(queueing_lock=f"readiness:{run_id}").defer(
            ingest_run_id=str(run_id)
        )

    return {
        "ingestRunId": str(run_id),
        "status": "materialized",
        "rowsWritten": result.rows_written,
        "rowsRejected": result.rows_rejected,
    }


async def defer_materialize(ingest_run_id: UUID) -> int | None:
    """Queue a materialise. One job per run, enforced by the queueing lock.

    `AlreadyEnqueued` is a **success**, not a fault: it means a job for this run is
    already waiting, which is exactly what the caller asked for. A double-clicked
    Materialise button would otherwise be answered with a 503 about an unavailable
    dependency, when nothing is unavailable and the work is already queued. Returns `None`
    in that case, because there is no new job id to report.
    """
    try:
        await ensure_queue_open()
        job = await materialize_run_task.configure(
            queueing_lock=f"materialize:{ingest_run_id}"
        ).defer_async(ingest_run_id=str(ingest_run_id))
    except AlreadyEnqueued:
        log.info("materialize_already_queued", ingest_run_id=str(ingest_run_id))
        return None
    except Exception as exc:
        raise DependencyUnavailable(
            "Materialising is briefly unavailable. Try again in a moment."
        ) from exc
    return int(job)


@app.task(name="run_readiness", retry=3, pass_context=True)
def run_readiness_task(context: JobContext, ingest_run_id: str) -> dict[str, Any]:
    """Check a materialised run's canonical rows and record what was found.

    A separate job from `materialize_run`, chained after it, rather than the same one: a slow
    or broken readiness pass must not make a successful materialise look failed, and a run
    that has rows but no checklist is still useful to a human. The same reasoning put
    profiling in its own job after parsing.
    """
    run_id = UUID(ingest_run_id)

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select source_file_id, customer_id from ingest_runs where id = %s", (run_id,)
            )
            row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"ingest run {run_id} vanished")
        source_file_id, customer_id = row

        outcome = run_readiness(
            conn,
            run_id=str(run_id),
            source_file_id=str(source_file_id),
            customer_id=str(customer_id),
            attempt=context.job.attempts + 1,
        )

    return {
        "ingestRunId": str(run_id),
        "status": outcome.status,
        "blocking": outcome.blocking,
        "warnings": outcome.warnings,
        "checksRun": outcome.checks_run,
    }


async def defer_readiness(ingest_run_id: UUID) -> int | None:
    """Queue a readiness pass. One job per run, enforced by the queueing lock."""
    try:
        await ensure_queue_open()
        job = await run_readiness_task.configure(
            queueing_lock=f"readiness:{ingest_run_id}"
        ).defer_async(ingest_run_id=str(ingest_run_id))
    except AlreadyEnqueued:
        log.info("readiness_already_queued", ingest_run_id=str(ingest_run_id))
        return None
    except Exception as exc:
        raise DependencyUnavailable(
            "The readiness checks are briefly unavailable. Try again in a moment."
        ) from exc
    return int(job)


@app.task(name="calculate_rebates", retry=3, pass_context=True)
def calculate_rebates_task(context: JobContext, ingest_run_id: str) -> dict[str, Any]:
    """Calculate a materialised run's rebates with the reference engine.

    A separate job, triggered by a human POST rather than chained after anything: calculating is a
    decision, not a consequence, which is also why `ready` and `blocked` stay terminal statuses.
    """
    run_id = UUID(ingest_run_id)

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select customer_id from ingest_runs where id = %s", (run_id,))
            row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"ingest run {run_id} vanished")
        (customer_id,) = row

        outcome = run_calculation(
            conn,
            run_id=str(run_id),
            customer_id=str(customer_id),
            engine=ReferenceRebateEngine(),
            attempt=context.job.attempts + 1,
        )

    return {
        "ingestRunId": str(run_id),
        "programs": outcome.programs,
        "periodsWritten": outcome.periods_written,
        "linesWritten": outcome.lines_written,
        "totalAmount": f"{outcome.total_amount:f}",
    }


async def defer_rebates(ingest_run_id: UUID) -> int | None:
    """Queue a rebate calculation. One job per run, enforced by the queueing lock."""
    try:
        await ensure_queue_open()
        job = await calculate_rebates_task.configure(
            queueing_lock=f"rebates:{ingest_run_id}"
        ).defer_async(ingest_run_id=str(ingest_run_id))
    except AlreadyEnqueued:
        log.info("rebates_already_queued", ingest_run_id=str(ingest_run_id))
        return None
    except Exception as exc:
        raise DependencyUnavailable(
            "Calculating is briefly unavailable. Try again in a moment."
        ) from exc
    return int(job)


@app.task(name="import_legacy_rebates", retry=3, pass_context=True)
def import_legacy_rebates(context: JobContext, ingest_run_id: str) -> dict[str, Any]:
    """Read a legacy rebate export and match its rows to the customer's programs.

    The sibling of `parse_source_file`, and it guards its kind the same way and for the same
    reason: the dispatcher and the worker must agree, which is the P10 remediation - drift's
    guarantee was false because the gate and the writer counted different things. Reading the kind
    **before the claim** leaves a misdirected run at `queued` and recoverable.
    """
    run_id = UUID(ingest_run_id)
    settings = config.load()

    with _connect() as conn:
        kind = _kind_of(conn, run_id)
        if kind is not None and kind != LEGACY_KIND:
            log.warning(
                "legacy_import_refused_wrong_kind",
                task="import_legacy_rebates",
                ingest_run_id=str(run_id),
                file_kind=kind,
                expected_kind=LEGACY_KIND,
            )
            return {"ingestRunId": str(run_id), "skipped": True, "reason": "wrong_kind"}

        with conn.cursor() as cur:
            cur.execute(
                "update ingest_runs set status = 'parsing', updated_at = now() "
                "where id = %s and status = 'queued' returning source_file_id, customer_id",
                (run_id,),
            )
            claimed = cur.fetchone()
        conn.commit()
        if claimed is None:
            log.info("legacy_import_skipped_not_queued", ingest_run_id=str(run_id))
            return {"ingestRunId": str(run_id), "skipped": True}

        source_file_id, customer_id = claimed
        with conn.cursor() as cur:
            cur.execute("select stored_name from source_files where id = %s", (source_file_id,))
            row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"source file {source_file_id} vanished")
        path = Path(settings.upload_dir) / str(row[0])

        try:
            outcome = run_legacy_import(
                conn,
                run_id=str(run_id),
                source_file_id=str(source_file_id),
                customer_id=str(customer_id),
                content=path.read_bytes(),
                attempt=context.job.attempts + 1,
            )
        except AppError as exc:
            # A refusal the user can act on. Recorded on the run and **not** retried - the file
            # will not read next time either, which is `parse_source_file`'s existing rule.
            with conn.cursor() as cur:
                cur.execute(
                    "update ingest_runs set status = 'failed', error_code = %s, "
                    "error_message = %s, updated_at = now() where id = %s",
                    (exc.code, exc.message, run_id),
                )
            conn.commit()
            log.warning(
                "legacy_import_failed",
                ingest_run_id=str(run_id),
                code=exc.code,
                reason=exc.message,
            )
            return {"ingestRunId": str(run_id), "status": "failed", "errorCode": exc.code}
        except Exception:
            # The run was already committed to `parsing` by the claim above, in its own
            # transaction, so an unexpected failure would otherwise strand it there: the retry's
            # conditional claim finds it no longer `queued`, reports success, and the run hangs at
            # `parsing` forever with no error anyone can see. Release it back to `queued` so the
            # retry can actually do the work, then re-raise so procrastinate retries.
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    "update ingest_runs set status = 'queued', updated_at = now() "
                    "where id = %s and status = 'parsing'",
                    (run_id,),
                )
            conn.commit()
            log.error("legacy_import_errored", ingest_run_id=str(run_id), exc_info=True)
            raise

    return {
        "ingestRunId": str(run_id),
        "status": "imported",
        "rowsImported": outcome.rows_imported,
        "rowsMatched": outcome.rows_matched,
        "rowsUnmatched": outcome.rows_unmatched,
    }


async def defer_legacy_import(ingest_run_id: UUID) -> int:
    """Queue a legacy import. One job per run, enforced by the queueing lock."""
    try:
        await ensure_queue_open()
        job = await import_legacy_rebates.configure(
            queueing_lock=f"legacy-import:{ingest_run_id}"
        ).defer_async(ingest_run_id=str(ingest_run_id))
    except AlreadyEnqueued:
        log.info("legacy_import_already_queued", ingest_run_id=str(ingest_run_id))
        return 0
    except Exception as exc:
        raise DependencyUnavailable(
            "Uploads are briefly unavailable. Try again in a moment."
        ) from exc
    return int(job)


async def defer_ingest(ingest_run_id: UUID, kind: str) -> int:
    """Queue whichever ingest this file kind needs.

    The branch lives here rather than in the upload route, because the route owns the caps, the
    sniff, the rate limit and the storage - that single edge is the whole reason there is one upload
    route - and queue knowledge belongs to the queue module (BACKEND.md §2: no rules in routes).
    """
    if kind == LEGACY_KIND:
        return await defer_legacy_import(ingest_run_id)
    return await defer_parse(ingest_run_id)


@app.task(name="compute_variances", retry=3, pass_context=True)
def compute_variances_task(
    context: JobContext, ingest_run_id: str, legacy_source_file_id: str
) -> dict[str, Any]:
    """Compare this run's calculated rebates against an imported legacy export.

    A human action against the **ERP** run, taking the legacy file as an input - which is why
    nothing chains into it and the run's status is untouched by it.
    """
    run_id = UUID(ingest_run_id)
    with _connect() as conn:
        outcome = run_variance_computation(
            conn,
            run_id=str(run_id),
            legacy_source_file_id=legacy_source_file_id,
            attempt=context.job.attempts + 1,
        )
    return {
        "ingestRunId": str(run_id),
        "legacySourceFileId": legacy_source_file_id,
        "rows": outcome.rows,
        "legacyTotal": f"{outcome.legacy_total:f}",
        "calculatedTotal": f"{outcome.calculated_total:f}",
        "netVariance": f"{outcome.net_variance:f}",
    }


async def defer_variances(ingest_run_id: UUID, legacy_source_file_id: UUID) -> int | None:
    """Queue a variance computation. One job per run, enforced by the queueing lock."""
    try:
        await ensure_queue_open()
        job = await compute_variances_task.configure(
            queueing_lock=f"variances:{ingest_run_id}"
        ).defer_async(
            ingest_run_id=str(ingest_run_id),
            legacy_source_file_id=str(legacy_source_file_id),
        )
    except AlreadyEnqueued:
        log.info("variances_already_queued", ingest_run_id=str(ingest_run_id))
        return None
    except Exception as exc:
        raise DependencyUnavailable(
            "Comparing is briefly unavailable. Try again in a moment."
        ) from exc
    return int(job)


@app.task(name="attribute_variances", retry=3, pass_context=True)
def attribute_variances_task(context: JobContext, ingest_run_id: str) -> dict[str, Any]:
    """Explain this run's variances, line by line."""
    run_id = UUID(ingest_run_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select customer_id from ingest_runs where id = %s", (run_id,))
            row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"ingest run {run_id} vanished")
        outcome = run_attribution(
            conn,
            run_id=str(run_id),
            customer_id=str(row[0]),
            attempt=context.job.attempts + 1,
        )
    return {
        "ingestRunId": str(run_id),
        "links": outcome.links,
        "variances": outcome.variances,
        "explainedAmount": f"{outcome.explained_amount:f}",
        "residueAmount": f"{outcome.residue_amount:f}",
    }


async def defer_attribution(ingest_run_id: UUID) -> int | None:
    """Queue an attribution pass. One job per run, enforced by the queueing lock."""
    try:
        await ensure_queue_open()
        job = await attribute_variances_task.configure(
            queueing_lock=f"attribution:{ingest_run_id}"
        ).defer_async(ingest_run_id=str(ingest_run_id))
    except AlreadyEnqueued:
        log.info("attribution_already_queued", ingest_run_id=str(ingest_run_id))
        return None
    except Exception as exc:
        raise DependencyUnavailable(
            "Explaining is briefly unavailable. Try again in a moment."
        ) from exc
    return int(job)


#: How many times the finishing job will come back to check whether the run ahead of it is ready.
#: At three seconds apart that is five minutes, which is far more than a 48,150-row profile needs.
_SEED_READY_ATTEMPTS: Final = 100


@app.task(name="finish_demo_seed", retry=2)
def finish_demo_seed(
    ingest_run_id: str,
    legacy_source_file_id: str,
    month2_ingest_run_id: str | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """Drive one seeded run all the way to an attributed parallel run.

    **Without this the reset produces an empty demo.** Uploading three files leaves three runs at
    `profiled` and `imported`, so the dashboard shows no rows, no findings and a null explained
    percentage - which is exactly what a visitor would see after pressing Reset. A tester found it;
    the "verified after a clean reset" claim in `docs/decisions.md` had been checked by driving the
    pipeline by hand afterwards, which is not the same thing and is corrected there.

    **It re-defers rather than waiting.** The first version slept in a loop until the run was
    profiled, which deadlocked immediately: the worker runs one job at a time, so sleeping here
    held the slot the profile job needed, and the thing being waited for could never start. Coming
    back later costs one row in the queue and cannot deadlock.
    """
    run_id = UUID(ingest_run_id)

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select source_file_id, status from ingest_runs where id = %s", (run_id,))
            row = cur.fetchone()
        conn.commit()
        if row is None:
            # The run was deleted - another reset overtook this one. Not an error.
            log.info("finish_demo_seed_run_gone", ingest_run_id=str(run_id))
            return {"ingestRunId": str(run_id), "skipped": True}
        source_file_id, status = row
        if status == "failed":
            log.error("finish_demo_seed_run_failed", ingest_run_id=str(run_id))
            return {"ingestRunId": str(run_id), "skipped": True, "reason": "run_failed"}
        if status not in {"profiled", "mapped"}:
            if attempt >= _SEED_READY_ATTEMPTS:
                raise RuntimeError(f"seeded run {run_id} never reached profiled")
            finish_demo_seed.configure(schedule_in={"seconds": 3}).defer(
                ingest_run_id=ingest_run_id,
                legacy_source_file_id=legacy_source_file_id,
                month2_ingest_run_id=month2_ingest_run_id,
                attempt=attempt + 1,
            )
            return {"ingestRunId": str(run_id), "waiting": status, "attempt": attempt}

        reference = Path(__file__).resolve().parents[2] / "fixtures" / "reference"
        mapping = json.loads((reference / "mapping_csv.json").read_text(encoding="utf-8"))["csv"]
        spec_doc = json.loads((reference / "mapping_spec.json").read_text(encoding="utf-8"))
        with conn.cursor() as cur:
            cur.execute("select customer_id from ingest_runs where id = %s", (run_id,))
            owner = cur.fetchone()
            if owner is None:
                return {"ingestRunId": str(run_id), "skipped": True}
            spec_customer_id = owner[0]
            cur.execute(
                "select max(version) from file_layouts where source_file_id = %s",
                (source_file_id,),
            )
            layout_row = cur.fetchone()
            layout_version = 1 if layout_row is None or layout_row[0] is None else layout_row[0]
            cur.execute(
                _INSERT_DRAFT_FROM_SPEC,
                {
                    "id": str(uuid7()),
                    "source_file_id": source_file_id,
                    "layout_version": layout_version,
                    "canonical_schema_version": config.load().canonical_schema_version,
                    "mapping": Json(mapping),
                },
            )

            # The approved spec, from the committed fixture. This is what makes the demo show
            # *memory*: the month-2 file reaching `mapped` without anyone touching it, and the
            # drifted month-2 file being refused against a baseline. `spec_sha256` is computed
            # with `lib/canonjson` over the same document the fixture holds, so a seeded spec
            # hashes identically to one approved through the route.
            cur.execute(
                "select uploader_ip_hash from source_files where id = %s", (source_file_id,)
            )
            uploader = cur.fetchone()
            cur.execute(
                _INSERT_APPROVED_SPEC,
                {
                    "id": str(uuid7()),
                    "customer_id": str(spec_customer_id),
                    "kind": "csv",
                    "canonical_schema_version": spec_doc["canonicalSchemaVersion"],
                    "spec": Json(spec_doc),
                    "spec_sha256": canonjson.sha256(spec_doc),
                    "source_file_id": source_file_id,
                    "layout_version": layout_version,
                    "note": "Approved by the demo seeder from the committed reference spec.",
                    "ip_hash": "seed" if uploader is None else uploader[0],
                },
            )
        conn.commit()

    # Each stage's own function, in order - the same code a visitor's run takes.
    # `chain_readiness=False`: readiness is called directly below, so this job finishes only when
    # the whole chain has.
    run_materialization(str(run_id), chain_readiness=False)

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select source_file_id, customer_id from ingest_runs where id = %s", (run_id,)
            )
            ids = cur.fetchone()
        conn.commit()
        if ids is None:
            return {"ingestRunId": str(run_id), "skipped": True}
        source_file_id, customer_id = ids
        run_readiness(
            conn,
            run_id=str(run_id),
            source_file_id=str(source_file_id),
            customer_id=str(customer_id),
        )
        run_calculation(
            conn,
            run_id=str(run_id),
            customer_id=str(customer_id),
            engine=ReferenceRebateEngine(),
        )
        run_variance_computation(
            conn, run_id=str(run_id), legacy_source_file_id=legacy_source_file_id
        )
        outcome = run_attribution(conn, run_id=str(run_id), customer_id=str(customer_id))

    # Month 2 last. It needs the approved spec, which has existed since the top of this function,
    # and nothing above needs month 2 - so releasing it here rather than before the pipeline gets
    # the visitor-visible run finished sooner. It is **not** a fix for the attribution timing
    # anomaly below; one clean run after this move measured attribution at 357 ms and the next at
    # 75,468 ms, so the move is an ordering improvement and nothing more.
    if month2_ingest_run_id is not None:
        parse_source_file(month2_ingest_run_id)

    log.info("demo_seed_finished", ingest_run_id=str(run_id), links=outcome.links)
    return {"ingestRunId": str(run_id), "links": outcome.links}


async def defer_finish_demo_seed(
    ingest_run_id: UUID, legacy_source_file_id: UUID, month2_ingest_run_id: UUID | None = None
) -> int | None:
    try:
        await ensure_queue_open()
        job = await finish_demo_seed.configure().defer_async(
            ingest_run_id=str(ingest_run_id),
            legacy_source_file_id=str(legacy_source_file_id),
            month2_ingest_run_id=None
            if month2_ingest_run_id is None
            else str(month2_ingest_run_id),
        )
    except AlreadyEnqueued:
        return None
    except Exception as exc:
        raise DependencyUnavailable("Seeding is briefly unavailable.") from exc
    return int(job)


# ---------------------------------------------------------------------------------------------
# Worker registrations.
#
# Each is a thin wrapper: it unwraps the one thing the stage wants from the job context - which
# attempt this is, for `ingest_runs.stage_timings` - and calls the plain function above. The
# registered `name` is unchanged, so queued jobs and `queueing_lock` values keep working.
#
# They exist because `pass_context=True` is typed `Concatenate[JobContext, P]`, which makes the
# context a *required positional* on the task object's `__call__`. That is correct for a worker
# and wrong for everyone else: `reprofile_source_file` and any caller exercising a single stage
# have no `JobContext` to hand and no business fabricating a procrastinate internal to get one.
# ---------------------------------------------------------------------------------------------


@app.task(name="parse_source_file", retry=3, pass_context=True)
def parse_source_file_task(context: JobContext, ingest_run_id: str) -> dict[str, Any]:
    """Worker entry point for :func:`parse_source_file`."""
    return parse_source_file(ingest_run_id, attempt=context.job.attempts + 1)


@app.task(name="profile_source_file", retry=3, pass_context=True)
def profile_source_file_task(context: JobContext, ingest_run_id: str) -> dict[str, Any]:
    """Worker entry point for :func:`profile_source_file`."""
    return profile_source_file(ingest_run_id, attempt=context.job.attempts + 1)


@app.task(name="reprofile_source_file", retry=3, pass_context=True)
def reprofile_source_file_task(
    context: JobContext, ingest_run_id: str, layout_version: int
) -> dict[str, Any]:
    """Worker entry point for :func:`reprofile_source_file`."""
    return reprofile_source_file(
        ingest_run_id, layout_version=layout_version, attempt=context.job.attempts + 1
    )


@app.task(name="materialize_run", retry=MATERIALIZE_ATTEMPTS, pass_context=True)
def materialize_run_task(context: JobContext, ingest_run_id: str) -> dict[str, Any]:
    """Worker entry point for :func:`materialize_run`."""
    return materialize_run(ingest_run_id, attempt=context.job.attempts + 1)


# ---------------------------------------------------------------------------------------------
# The demo reset.
#
# The one **async** task in this module, deliberately. Every other task talks to Postgres over a
# plain psycopg connection, but the reset's rebuild goes through `accept_upload` - the real upload
# service, on an `AsyncSession` - because the P15 seeder's whole premise is that a seeded run is
# produced by exactly the path a visitor's own upload takes. Reimplementing that synchronously
# would be a second upload path to keep in step, which BACKEND.md §9 exists to prevent.
#
# procrastinate's worker is itself an asyncio worker, so an `async def` task runs on its loop with
# no bridging. `db.init()` is a no-op once an engine exists, so calling it here costs nothing in a
# worker that has already done so and is what makes the task work in one that has not.
# ---------------------------------------------------------------------------------------------


@app.task(name="reset_demo", retry=False, queueing_lock="demo:reset")
async def reset_demo_task(ip_hash: str) -> dict[str, Any]:
    """Clear the demo and rebuild it. `retry=False` on purpose.

    A destructive action that failed half way is not something to repeat unasked: the reset is
    idempotent by construction (delete then rebuild), but a *retry* would clear a demo somebody may
    already be reading because the first attempt died after its own clear. `demo_seed_state` records
    the interrupted attempt, `GET /v1/demo/state` reports it as not seeded, and a person can press
    the button again. That is the right amount of automation for this one.
    """
    from src.features.demo import service

    db.init(config.load().database_url)
    async with db.session() as session:
        return await service.run_reset(session, ip_hash=ip_hash)


async def defer_demo_reset(ip_hash: str) -> int | None:
    """Queue a reset. `None` means one is already enqueued or running.

    `queueing_lock` is what makes that true, and it is the same mechanism the rest of this module
    uses for "do not run two of these at once" - no second lock to disagree with it.
    """
    try:
        await ensure_queue_open()
        job = await reset_demo_task.configure(queueing_lock="demo:reset").defer_async(
            ip_hash=ip_hash
        )
    except AlreadyEnqueued:
        return None
    except Exception as exc:
        raise DependencyUnavailable(
            "Resetting is briefly unavailable. Try again in a moment."
        ) from exc
    return int(job)
