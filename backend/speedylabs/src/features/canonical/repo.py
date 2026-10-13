"""Canonical-output SQL. The only place it lives (BACKEND.md §2).

Pagination is keyset on `source_row_number`, which is unique per run, so no tie-break
column is needed and the `(ingest_run_id, source_row_number)` unique index serves the
query directly. Not OFFSET: these pages are read while nothing is being inserted, but an
offset over 50,000 rows still makes the last page scan the first 49,975.

Every statement is a literal string, for the reason `runs/repo.py` gives at length.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_RUN_FOR_OUTPUT = text(
    """
    select r.id, r.status, r.source_file_id, r.customer_id, r.materialized_at,
           c.name as customer_name,
           f.row_count, f.format, f.original_filename
    from ingest_runs r
    join customers c on c.id = r.customer_id
    join source_files f on f.id = r.source_file_id
    where r.id = :id
    """
)

#: What stops a run being materialised, and the human's decision. **Counts, not a status**
#: (`docs/decisions.md`, 2026-10-07): the status is for the badge, the rows are the authority, and a
#: run whose status was hand-edited is still refused.
#:
#: Two things count, not one. A blocking **finding** is a difference that was found; an **errored**
#: check is a comparison that could not be made, which is not the same as one that passed - and
#: counting only findings is how a failed comparison used to let a file straight through, the exact
#: opposite of the "fails closed" guarantee. The Phase 8-10 tester found that; this query and
#: `drift/run.py` now agree, which they must, or the status and the gate would disagree about the
#: same run.
_DRIFT_GATE = text(
    """
    select
      (select count(*) from drift_findings
        where ingest_run_id = :run_id and severity = 'blocking')
      + (select count(*) from drift_checks
        where ingest_run_id = :run_id and outcome = 'errored') as blocking,
      (select drift_decision from ingest_runs where id = :run_id) as decision
    """
)

_COUNTS = text(
    """
    select
      (select count(*) from canonical_lines where ingest_run_id = :run_id) as rows_written,
      (select count(*) from materialization_errors where ingest_run_id = :run_id)
        as rows_rejected
    """
)

_PAGE = text(
    """
    select source_row_number, invoice_no, invoice_date, customer_id, customer_name,
           dist_item_id, mfr_item_code, brand_manufacturer, invoice_vendor,
           qty, uom, pack_size, catch_weight_lbs, unit_price, ext_amount,
           is_deviated, deviated_price
    from canonical_lines
    where ingest_run_id = :run_id
      and (cast(:cursor_row as integer) is null
           or source_row_number > cast(:cursor_row as integer))
    order by source_row_number
    limit :limit
    """
)

#: The drill-down. `= any(:row_numbers)` takes a bound **array** - the row numbers are parsed
#: into integers before they reach here, so there is nothing to interpolate and nothing to
#: inject. `order by` so the caller gets them in file order rather than array order.
_ROWS_BY_NUMBER = text(
    """
    select source_row_number, invoice_no, invoice_date, customer_id, customer_name,
           dist_item_id, mfr_item_code, brand_manufacturer, invoice_vendor,
           qty, uom, pack_size, catch_weight_lbs, unit_price, ext_amount,
           is_deviated, deviated_price
    from canonical_lines
    where ingest_run_id = :run_id and source_row_number = any(:row_numbers)
    order by source_row_number
    """
)

_ERROR_PAGE = text(
    """
    select source_row_number, canonical_field, raw_value, error_code, message
    from materialization_errors
    where ingest_run_id = :run_id
      and (cast(:cursor_row as integer) is null
           or source_row_number > cast(:cursor_row as integer))
    order by source_row_number, canonical_field
    limit :limit
    """
)

_CLAIM_FOR_MATERIALIZE = text(
    """
    update ingest_runs set status = 'materializing', updated_at = now()
    where id = :id and status in ('profiled', 'mapped', 'materializing')
    returning source_file_id
    """
)


async def run_for_output(session: AsyncSession, run_id: UUID) -> dict[str, Any] | None:
    row = (await session.execute(_RUN_FOR_OUTPUT, {"id": run_id})).mappings().first()
    return dict(row) if row else None


async def drift_gate(session: AsyncSession, run_id: UUID) -> tuple[int, str | None]:
    """How many blocking drift findings this run has, and what a human decided about them."""
    row = (await session.execute(_DRIFT_GATE, {"run_id": run_id})).mappings().one()
    return int(row["blocking"]), (None if row["decision"] is None else str(row["decision"]))


async def counts(session: AsyncSession, run_id: UUID) -> tuple[int, int]:
    row = (await session.execute(_COUNTS, {"run_id": run_id})).mappings().one()
    return int(row["rows_written"]), int(row["rows_rejected"])


async def page(
    session: AsyncSession, run_id: UUID, *, limit: int, cursor_row: int | None
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(_PAGE, {"run_id": run_id, "limit": limit, "cursor_row": cursor_row})
    ).mappings()
    return [dict(row) for row in rows]


async def rows_by_number(
    session: AsyncSession, run_id: UUID, *, row_numbers: list[int]
) -> list[dict[str, Any]]:
    """Exactly these rows of this run. Scoped by run, so a number belonging to another run is
    simply absent rather than another customer's line."""
    rows = (
        await session.execute(_ROWS_BY_NUMBER, {"run_id": run_id, "row_numbers": row_numbers})
    ).mappings()
    return [dict(row) for row in rows]


async def error_page(
    session: AsyncSession, run_id: UUID, *, limit: int, cursor_row: int | None
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            _ERROR_PAGE, {"run_id": run_id, "limit": limit, "cursor_row": cursor_row}
        )
    ).mappings()
    return [dict(row) for row in rows]


async def claim_for_materialize(session: AsyncSession, run_id: UUID) -> UUID | None:
    """Move the run to `materializing` and hand back its file, or `None` if it is not in a
    state that may be materialised.

    Conditional on the status, so two simultaneous requests cannot both queue a job: the
    second sees no row. `materializing` is in the allowed set on purpose - a run whose
    worker died mid-job is stuck in that status, and the retry must be able to claim it.
    """
    row = (await session.execute(_CLAIM_FOR_MATERIALIZE, {"id": run_id})).first()
    return UUID(str(row[0])) if row else None
