"""Applying a mapping to the whole file and landing canonical rows.

Runs in the worker, over a plain psycopg connection, because the hot path is `COPY`: 50,000 ORM
inserts do not fit a 40-second budget, and `cursor.copy` streams rows into Postgres at a rate that
makes the budget a non-issue.

**One `COPY` per batch, and one transaction for the whole run.** The Phase 7 tester checked that
claim against the code and found an earlier version of this docstring saying "one transaction per
batch", which was wrong - there is a single `commit()`, after the delete, every batch and the
rejections. The single transaction is the *right* design and the reason the idempotency criterion
holds: a worker killed at row 30,000 leaves no durable rows at all, so the retry starts from a
clean slate rather than from a partial one. Per-batch commits would make a kill leave 30,000 rows
that the retry would then have to reconcile.

Two properties the rest of the system relies on:

* **Idempotent.** A re-run deletes this run's rows first, inside the same transaction as
  the insert, so a worker killed at row 30,000 and restarted produces 50,000 rows and not
  80,000. The unique key on `(ingest_run_id, source_row_number)` is the backstop.
* **`source_row_number` is a record position, not a byte-exact line number.** It counts
  data records from the first one (a CSV's header is not a record), which equals the
  physical line for every file in the fixtures. It can differ for a file with blank lines
  in the middle or a quoted field containing a newline, because both readers skip blanks
  and `csv.reader` folds an embedded newline into one record.

  Making it exact means carrying each reader's own line number through `apply_mapping` and
  its three call sites. P8 **declined** to do that, reversing what this docstring used to
  promise, and the reason is worth keeping: the drill-down does not need it, because findings
  and `canonical_lines` share one numbering produced by one function, while the refactor
  would change the profiler, the validator and the materialiser at the same moment A2 first
  depended on their numbers - so an A2 failure could have been the refactor rather than a
  check. What was missing was a **guard**, not the plumbing: a test asserting that every
  committed fixture's stored number is the record's physical line, which fails the day a file
  with a blank line appears and makes the plumbing its own change. The UI says "Source row",
  never "Line", so nothing claims more than is true.
* **Rejected rows are recorded, never dropped.** A row whose transform fails lands in
  `materialization_errors` with the field, the raw value and the reason. A quietly short
  output file is the worst failure a data-onboarding tool has: everything reconciles, and
  against the wrong total.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from pydantic import ValidationError
from pydantic_core import ErrorDetails

from src.features.canonical.model import (
    CANONICAL_FIELD_NAMES,
    REQUIRED_FIELD_NAMES,
    CanonicalInvoiceLine,
)
from src.features.mapping.apply import FieldMapping, apply_mapping, canonical_values
from src.features.mapping.transforms import parse as parse_transform
from src.lib import logger
from src.lib.ids import uuid7
from src.lib.parsers.csv_reader import iter_csv_rows
from src.lib.parsers.fwf_reader import SliceField, iter_fixed_width_rows
from src.lib.storage import LocalStorage

log = logger.get_logger(__name__)

#: Rows per `COPY`. Large enough that the per-batch overhead disappears. **Not** a memory bound -
#: an earlier comment claimed it was, and the tester was right to call that out: `_read_source`
#: reads the whole file into a list first, so peak memory is set by the file, not by this number.
#: The real bound is `MAX_UPLOAD_ROWS`. Making it a genuine streaming pass is a worthwhile change
#: and is not this one.
COPY_BATCH_ROWS = 10_000

#: The longest raw value stored against a rejected row. The point is to show what the cell
#: said, not to keep a copy of a 2 MB cell.
MAX_RAW_VALUE_CHARS = 200

#: Column order for the `COPY`. Must match `_row_tuple` exactly.
_COPY_COLUMNS = (
    "id",
    "ingest_run_id",
    "source_row_number",
    *CANONICAL_FIELD_NAMES,
)

#: Column order for the rejected-rows `COPY`. Must match `_write_rejections`.
_ERROR_COLUMNS = (
    "id",
    "ingest_run_id",
    "source_row_number",
    "canonical_field",
    "raw_value",
    "error_code",
    "message",
)


@dataclass
class MaterializeResult:
    rows_written: int
    rows_rejected: int


@dataclass
class RejectedRow:
    source_row_number: int
    canonical_field: str
    raw_value: str
    error_code: str
    message: str


def _validate(values: dict[str, Any]) -> CanonicalInvoiceLine | RejectedRow:
    """Turn transformed values into a canonical line, or say which field refused.

    The canonical model is the arbiter, not a coercion written here: it already owns which
    fields are required, which lengths are allowed, that `uom` is one of three codes and
    that `pack_size` is a positive integer. Validating through it means the rows this
    writes cannot disagree with the schema we publish, and the DB's own constraints become
    a backstop rather than the only check.

    A `ValidationError` is turned into one rejection naming pydantic's first error, because
    a person fixing a mapping fixes one field at a time and a row with six complaints is
    usually one wrong column repeated.
    """
    try:
        return CanonicalInvoiceLine.model_validate(values)
    except ValidationError as error:
        first = error.errors()[0]
        location = first["loc"]
        field = str(location[0]) if location else "row"
        raw = values.get(field)
        return RejectedRow(
            source_row_number=0,  # filled in by the caller, which knows the row
            canonical_field=field,
            raw_value="" if raw is None else str(raw)[:MAX_RAW_VALUE_CHARS],
            error_code="unusable_value",
            message=_readable(first, field),
        )


def _readable(error: ErrorDetails, field: str) -> str:
    """A pydantic error as something a person reading a rejection report can act on.

    pydantic's own messages are written for developers ("Input should be a valid
    integer"); BACKEND.md §3 wants what happened and how to fix it. Only the handful of
    error types this pipeline can actually produce are rewritten - anything else falls
    through to pydantic's message, which is still better than a bare code.
    """
    match error["type"]:
        case "missing" | "none_required":
            return f"{field} is required by the canonical schema and this row has no value"
        case "literal_error":
            return f"{field} is not one of the values the canonical schema allows"
        case "string_too_long":
            return f"{field} is longer than the canonical schema allows"
        case "greater_than_equal" | "greater_than":
            return f"{field} is outside the range the canonical schema allows"
        case _:
            return f"{field}: {error['msg'].lower()}"


def _row_tuple(run_id: str, row_number: int, line: CanonicalInvoiceLine) -> tuple[Any, ...]:
    return (
        uuid7(),
        run_id,
        row_number,
        *(getattr(line, name) for name in CANONICAL_FIELD_NAMES),
    )


def _reject(
    rejected: list[RejectedRow],
    *,
    row_number: int,
    field: str,
    raw: object,
    code: str,
    message: str,
) -> None:
    rejected.append(
        RejectedRow(
            source_row_number=row_number,
            canonical_field=field,
            raw_value=str(raw)[:MAX_RAW_VALUE_CHARS],
            error_code=code,
            message=message,
        )
    )


def materialize(
    connection: psycopg.Connection[tuple[Any, ...]],
    *,
    run_id: str,
    source_file_id: str,
    mapping_document: dict[str, Any],
    layout: dict[str, Any],
    upload_dir: Path,
) -> MaterializeResult:
    """Write this run's canonical rows. Replaces anything a previous attempt wrote."""
    missing = [name for name in REQUIRED_FIELD_NAMES if name not in mapping_document]
    if missing:
        # The route checks this first and returns 422; reaching here means a mapping was
        # emptied between the check and the job, so refusing loudly is right.
        raise ValueError(f"required canonical fields are unmapped: {', '.join(missing)}")

    parsed = {
        name: FieldMapping(
            source_column=str(entry["sourceColumn"]),
            transform=parse_transform(entry["transform"]),
        )
        for name, entry in mapping_document.items()
    }

    header, rows, first_row_number = _read_source(layout, source_file_id, upload_dir, connection)

    # Delete first, in the same transaction as the writes: a retry replaces rather than
    # appends, which is what makes a killed worker safe to restart.
    with connection.cursor() as cur:
        cur.execute("delete from canonical_lines where ingest_run_id = %s", (run_id,))
        cur.execute("delete from materialization_errors where ingest_run_id = %s", (run_id,))

    written = 0
    rejected: list[RejectedRow] = []
    batch: list[tuple[Any, ...]] = []

    for applied in apply_mapping(header, rows, parsed, first_row_number=first_row_number):
        if applied.failures:
            for field, message in applied.failures.items():
                _reject(
                    rejected,
                    row_number=applied.row_number,
                    field=field,
                    raw="",
                    code="transform_failed",
                    message=message,
                )
            continue

        outcome = _validate(canonical_values(applied))
        if isinstance(outcome, RejectedRow):
            rejected.append(replace(outcome, source_row_number=applied.row_number))
            continue

        if outcome.is_deviated and outcome.deviated_price is None:
            # The table's own check would abort the whole `COPY`; rejecting this one row
            # names the problem instead of failing 10,000 good rows with it.
            rejected.append(
                RejectedRow(
                    source_row_number=applied.row_number,
                    canonical_field="deviated_price",
                    raw_value="",
                    error_code="deviated_without_price",
                    message="this line is marked deviated but carries no deviated price",
                )
            )
            continue

        batch.append(_row_tuple(run_id, applied.row_number, outcome))
        if len(batch) >= COPY_BATCH_ROWS:
            written += _copy_batch(connection, batch)
            batch = []

    if batch:
        written += _copy_batch(connection, batch)
    if rejected:
        _write_rejections(connection, run_id, rejected)

    connection.commit()
    log.info(
        "materialized", ingest_run_id=run_id, rows_written=written, rows_rejected=len(rejected)
    )
    return MaterializeResult(rows_written=written, rows_rejected=len(rejected))


def _copy_statement(table: str, columns: tuple[str, ...]) -> sql.Composed:
    """`COPY` built from identifiers rather than an f-string.

    `cursor.copy` wants a composed statement, and composing identifiers is also the form
    that cannot become string-built SQL - which `semgrep` is right to object to and which
    this project has already been caught by twice.
    """
    return sql.SQL("copy {} ({}) from stdin").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
    )


def _copy_batch(
    connection: psycopg.Connection[tuple[Any, ...]], batch: list[tuple[Any, ...]]
) -> int:
    with (
        connection.cursor() as cur,
        cur.copy(_copy_statement("canonical_lines", _COPY_COLUMNS)) as copy,
    ):
        for row in batch:
            copy.write_row(row)
    return len(batch)


def _write_rejections(
    connection: psycopg.Connection[tuple[Any, ...]], run_id: str, rejected: list[RejectedRow]
) -> None:
    with (
        connection.cursor() as cur,
        cur.copy(_copy_statement("materialization_errors", _ERROR_COLUMNS)) as copy,
    ):
        for item in rejected:
            copy.write_row(
                (
                    uuid7(),
                    run_id,
                    item.source_row_number,
                    item.canonical_field,
                    item.raw_value,
                    item.error_code,
                    item.message,
                )
            )


def _read_source(
    layout: dict[str, Any],
    source_file_id: str,
    upload_dir: Path,
    connection: psycopg.Connection[tuple[Any, ...]],
) -> tuple[list[str], list[list[str]], int]:
    """Read the file the way its layout says to.

    The rows are materialised into a list rather than streamed, because `COPY` batches and
    the file is bounded by `MAX_UPLOAD_ROWS`. Streaming through the generator would be
    tidier but would hold the file handle open across every `COPY`, and the memory saving
    on a 250,000-row ceiling is not worth that.
    """
    with connection.cursor() as cur:
        cur.execute("select stored_name from source_files where id = %s", (source_file_id,))
        row = cur.fetchone()
    if row is None:
        raise ValueError("source file vanished")
    path = LocalStorage(upload_dir).open_path(str(row[0]))

    if layout["kind"] == "fixed_width":
        documents: list[dict[str, Any]] = list(layout["fields"])
        fields = [
            SliceField(name=str(entry["name"]), start=int(entry["start"]), end=int(entry["end"]))
            for entry in sorted(documents, key=lambda entry: int(entry["index"]))
        ]
        for names, row_iter in iter_fixed_width_rows(path, fields):
            return names, list(row_iter), 1
        return [], [], 1

    for header, row_iter in iter_csv_rows(path):
        return header, list(row_iter), 2
    return [], [], 2
