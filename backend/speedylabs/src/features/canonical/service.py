"""The canonical schema, and the output of applying a mapping to a whole file.

Reading the schema: no HTTP, no SQL.

Served from the file on disk rather than from the in-process model, deliberately. The
file is the published artifact: if it has drifted from the model, this route tells the
truth about what was published instead of papering over the drift, and `make check` is
what stops the drift from existing. A route that rebuilt the document from the model
would make the committed file decorative.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.canonical import repo
from src.features.canonical.model import CANONICAL_SCHEMA_VERSION, REQUIRED_FIELD_NAMES
from src.features.layouts import repo as layouts_repo
from src.features.mapping import repo as mapping_repo
from src.jobs.tasks import defer_materialize
from src.lib import cursor, logger
from src.lib.errors import (
    AlreadyMaterialized,
    DependencyUnavailable,
    DriftBlocked,
    FileRejected,
    MalformedRequest,
    MappingIncomplete,
    NotFound,
    NothingToDownload,
    ProfileNotReady,
    RuleBroken,
)

log = logger.get_logger(__name__)

#: `backend/canonical/invoice_line.schema.json`.
SCHEMA_PATH: Final = Path(__file__).resolve().parents[3] / "canonical" / "invoice_line.schema.json"


@lru_cache(maxsize=1)
def _document(path_text: str) -> dict[str, Any]:
    """Parsed once per path. Keyed by path so a test can point at a fixture and get a
    separate cache entry rather than a stale one."""
    path = Path(path_text)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        # The schema ships in the image; its absence is a broken deployment, not a
        # caller's problem. 503 rather than 500 so it reads as "this service is not
        # ready", which is what it is.
        raise DependencyUnavailable(
            "The canonical schema is unavailable. Try again shortly."
        ) from error
    parsed: dict[str, Any] = json.loads(raw)
    return parsed


def canonical_schema(path: Path | None = None) -> dict[str, Any]:
    """The version and the field list, in CSV column order."""
    document = _document(str(path or SCHEMA_PATH))
    properties: dict[str, Any] = document.get("properties", {})
    required: set[str] = set(document.get("required", []))
    order: list[str] = list(document.get("x-csvColumnOrder") or properties.keys())

    fields: list[dict[str, Any]] = []
    for name in order:
        spec: dict[str, Any] = properties.get(name, {})
        fields.append(
            {
                "name": name,
                "type": _published_type(spec),
                "required": name in required,
                "enum": list(spec["enum"]) if "enum" in spec else None,
                "pattern": spec.get("pattern"),
                "description": spec.get("description"),
            }
        )
    return {
        "version": document.get("x-canonicalSchemaVersion") or document.get("version", ""),
        "fields": fields,
    }


def _published_type(spec: dict[str, Any]) -> str:
    """The type as a client should read it.

    An optional field is `anyOf: [{...}, {"type": "null"}]` in the generated schema, so
    the useful answer is the non-null branch's type - a caller asking "what is
    `pack_size`?" wants "integer", not "anyOf".
    """
    if "type" in spec:
        published: str = spec["type"]
        return published
    # A cast rather than an isinstance narrow: this is our own generated schema, and
    # narrowing `Any` to `dict` leaves pyright with `dict[Unknown, Unknown]`, which it
    # then reports all the way up the return type.
    branches = cast("list[dict[str, Any]]", spec.get("anyOf") or [])
    for branch in branches:
        candidate = branch.get("type")
        if isinstance(candidate, str) and candidate != "null":
            return candidate
    return "string"


def clear_cache() -> None:
    """Drop the parsed-document cache. For tests that rewrite the file."""
    _document.cache_clear()


# --------------------------------------------------------------------------------------
# Materialising a run
# --------------------------------------------------------------------------------------

#: Preview page size, and its ceiling (BACKEND.md §3).
DEFAULT_PAGE_LIMIT: Final = 25
MAX_PAGE_LIMIT: Final = 100

#: Statuses from which materialising makes sense. A run that has not been profiled has no
#: layout to read the file with; one already `materialized` is told to start a new run.
MATERIALIZABLE_STATUSES: Final = frozenset({"profiled", "mapped", "materializing"})


async def request_materialization(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    """Check the run may be materialised, claim it, and queue the job.

    The completeness check happens here rather than in the job because the caller must be
    told `422` with the field names while it still has a request to answer - a job can only
    write a failure onto the run, which the user then has to go looking for.
    """
    run = await repo.run_for_output(session, run_id)
    if run is None:
        raise NotFound("That run does not exist.")

    status = str(run["status"])
    if run["materialized_at"] is not None or status == "materialized":
        raise AlreadyMaterialized(
            "This run has already been materialised. Start a new run to apply a changed mapping."
        )
    # The drift gate, **before** the status check, so the message names the actual obstacle. Counts
    # rather than the status: a run hand-edited to `mapped` is still refused, because the finding
    # rows are the authority and the status is only the badge.
    blocking_drift, drift_decision = await repo.drift_gate(session, run_id)
    if drift_decision == "rejected":
        raise FileRejected(
            "This file was rejected because it differs from the last approved one. Upload a "
            "corrected export, or accept the change to release it."
        )
    if blocking_drift and drift_decision != "accepted":
        # Deliberately says "change or unfinished comparison": the count includes comparisons that
        # could not be made, and telling a user they have N changes when one of them is "we could
        # not check" would send them looking for a difference that was never found.
        raise DriftBlocked(
            f"This file has {blocking_drift} change"
            f"{'s' if blocking_drift != 1 else ''} or unfinished comparison"
            f"{'s' if blocking_drift != 1 else ''} since the last approved file. Review them and "
            "accept or reject the file before materialising."
        )

    if status not in MATERIALIZABLE_STATUSES:
        raise RuleBroken(
            "This run is not ready to materialise. Wait for its file to finish profiling, "
            "then map the columns."
        )

    file_id = UUID(str(run["source_file_id"]))
    layout_version = await layouts_repo.current_layout(session, file_id)
    if layout_version is None:
        raise ProfileNotReady(
            "This file is still being read. It can be materialised once profiling finishes."
        )
    draft = await mapping_repo.draft(session, file_id, int(layout_version["version"]))
    mapping = dict(draft["mapping"]) if draft else {}
    unmapped = [name for name in REQUIRED_FIELD_NAMES if name not in mapping]
    if unmapped:
        raise MappingIncomplete(
            "Map every required canonical field before materialising this run.",
            fields=dict.fromkeys(unmapped, "This field is required by the canonical schema."),
        )

    # Claim, commit, then queue. If queueing fails the run is left saying `materializing`
    # with no job behind it - which is why `claim_for_materialize` accepts that status as a
    # starting state: the 503 tells the caller to try again, and the retry re-claims the
    # same run rather than being refused as already in flight.
    claimed = await repo.claim_for_materialize(session, run_id)
    if claimed is None:
        # Lost the race with another request, which has already queued the job. Nothing
        # went wrong and nothing more needs doing - report the same 202 the winner got.
        log.info("materialize_already_claimed", ingest_run_id=str(run_id))
    await session.commit()

    await defer_materialize(run_id)
    return {"ingest_run_id": run_id, "status": "materializing"}


async def output_summary(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    """Counts and identity for the output screen."""
    run = await repo.run_for_output(session, run_id)
    if run is None:
        raise NotFound("That run does not exist.")
    rows_written, rows_rejected = await repo.counts(session, run_id)
    return {
        "ingest_run_id": run_id,
        "status": str(run["status"]),
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        "rows_written": rows_written,
        "rows_rejected": rows_rejected,
        "materialized_at": run["materialized_at"],
        "customer_name": str(run["customer_name"]),
    }


async def download_target(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    """Check the run exists and has rows, and hand back what names the file.

    Checked before the response begins, because a `StreamingResponse` cannot change its
    status code once it has started: a 404 discovered mid-stream would reach the client as a
    `200` with an error message sitting inside a CSV.
    """
    run = await repo.run_for_output(session, run_id)
    if run is None:
        raise NotFound("That run does not exist.")
    rows_written, _ = await repo.counts(session, run_id)
    if rows_written == 0:
        raise NothingToDownload(
            "This run has no canonical rows yet. Materialise it, then download the file."
        )
    return {"customer_name": str(run["customer_name"]), "rows_written": rows_written}


#: Row numbers per drill-down request. A finding carries at most five examples, so one
#: hundred is already twenty findings' worth; the cap is what stops a caller asking for fifty
#: thousand rows in one response.
MAX_ROW_NUMBERS: Final = 100


def parse_row_numbers(raw: str) -> list[int]:
    """`"12,48,91"` as integers, or `400`.

    Parsed into `int` before any query sees it, which is the whole answer to
    `?rowNumbers=1;DROP TABLE canonical_lines`: there is no string left to interpolate. The
    cap is named in the message because a caller who hits it needs to know the number, not
    just that they were refused.
    """
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        raise MalformedRequest("rowNumbers needs at least one row number, like 12,48,91.")
    if len(parts) > MAX_ROW_NUMBERS:
        raise MalformedRequest(
            f"rowNumbers takes at most {MAX_ROW_NUMBERS} row numbers per request. Ask for "
            "them in batches."
        )
    numbers: list[int] = []
    for part in parts:
        if not part.isdigit():
            raise MalformedRequest(
                "rowNumbers must be whole numbers separated by commas, like 12,48,91."
            )
        numbers.append(int(part))
    return numbers


async def canonical_lines_by_number(
    session: AsyncSession, run_id: UUID, *, row_numbers: list[int]
) -> dict[str, Any]:
    """The drill-down: exactly these rows, in file order, and no cursor - there is no next
    page of an explicit list."""
    if await repo.run_for_output(session, run_id) is None:
        raise NotFound("That run does not exist.")
    rows = await repo.rows_by_number(session, run_id, row_numbers=row_numbers)
    return {"rows": rows, "next_cursor": None}


def clamp_limit(limit: int | None) -> int:
    """A caller's page size, inside the contract's ceiling."""
    if limit is None:
        return DEFAULT_PAGE_LIMIT
    return max(1, min(limit, MAX_PAGE_LIMIT))


async def canonical_lines(
    session: AsyncSession, run_id: UUID, *, limit: int | None, cursor: int | None
) -> dict[str, Any]:
    """One page of canonical rows, oldest source row first.

    **One query in the common case.** The existence check runs only when the page comes back
    empty, which is the only time the answer matters: rows prove the run exists, and asking first
    made every preview request two round trips to rule out a 404 that rows had already ruled out.
    The Phase 7 tester measured the second query against 7.2's "one query per request".
    """
    size = clamp_limit(limit)
    rows = await repo.page(session, run_id, limit=size, cursor_row=cursor)
    if not rows and await repo.run_for_output(session, run_id) is None:
        raise NotFound("That run does not exist.")
    return {"rows": rows, "next_cursor": _next_cursor(rows, size)}


async def materialization_errors(
    session: AsyncSession, run_id: UUID, *, limit: int | None, cursor: int | None
) -> dict[str, Any]:
    """One page of rejected rows.

    The existence check cannot be skipped on a non-empty page here, because an empty page is the
    *normal* answer - most runs reject nothing - so it stays unconditional. One query would be
    cheaper and would make "no such run" indistinguishable from "nothing rejected", which are
    different facts.
    """
    if await repo.run_for_output(session, run_id) is None:
        raise NotFound("That run does not exist.")
    size = clamp_limit(limit)
    rows = await repo.error_page(session, run_id, limit=size, cursor_row=cursor)
    return {"rows": rows, "next_cursor": _next_cursor(rows, size)}


def _next_cursor(rows: list[dict[str, Any]], size: int) -> str | None:
    """The last row number as an opaque token, or `None` when this was the final page.

    A short page means the end. A full page may also be the end, in which case the next
    request returns nothing and a `nextCursor` of null - one wasted request, versus a
    `count(*)` on every page to avoid it.
    """
    if len(rows) < size:
        return None
    return cursor.encode(str(rows[-1]["source_row_number"]))


def decode_cursor(value: str) -> int:
    """A preview cursor as a source row number.

    The key is the row number because `(ingest_run_id, source_row_number)` is unique, so a
    page boundary cannot land inside a tie and no second column is needed.
    """
    return cursor.decode(
        value,
        parse=lambda parts: int(parts[0]),
        message="That page link is not valid. Go back to the first page of rows.",
    )
