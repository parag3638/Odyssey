"""Rebate SQL for the read side, plus the gate P12 reads. The only place it lives (§2).

The calculation itself is written worker-side in `run.py` over plain psycopg, because its hot path
is `COPY`. Everything a request reads is here.

`rebate_gate` is written in this phase, under this name, deliberately
---------------------------------------------------------------------
P12 must refuse to compute variances against a calculation that failed or never happened. If it
wrote its own query it could count something subtly different from what `run.py` writes - which is
exactly how the drift gate ended up disagreeing with the drift engine and failing *open*
(`docs/progress.md`). One function, one pair of facts: the number of errored attempts, and
`calculated_at`. Both callers read the same two.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Errored attempts and the timestamp. A non-zero count or a null timestamp both mean "do not
#: compare against these numbers": the first because a program's figure is missing, the second
#: because nothing was computed at all.
_GATE = text(
    """
    select
      (select count(*) from rebate_calc_attempts
        where ingest_run_id = :run_id and outcome = 'errored') as errored,
      (select calculated_at from ingest_runs where id = :run_id) as calculated_at
    """
)

_SUMMARY = text(
    """
    select
      (select count(*) from rebate_calc_attempts where ingest_run_id = :run_id) as attempts,
      (select calculated_at from ingest_runs where id = :run_id) as calculated_at,
      (select coalesce(sum(amount), 0) from rebate_calcs where ingest_run_id = :run_id)
        as total_amount,
      (select coalesce(sum(contributed_line_count), 0) from rebate_calcs
        where ingest_run_id = :run_id) as contributed,
      (select coalesce(sum(skipped_line_count), 0) from rebate_calcs
        where ingest_run_id = :run_id) as skipped,
      (select coalesce(sum(out_of_scope_line_count), 0) from rebate_calcs
        where ingest_run_id = :run_id) as out_of_scope,
      (select min(engine_name) from rebate_calc_attempts where ingest_run_id = :run_id)
        as engine_name,
      (select min(engine_version) from rebate_calc_attempts where ingest_run_id = :run_id)
        as engine_version
    """
)

_ATTEMPTS = text(
    """
    select a.program_id, p.external_ref, p.name as program_name, p.kind, a.outcome, a.reason,
           a.lines_examined, a.lines_matched, a.periods_written,
           a.engine_name, a.engine_version,
           coalesce((select sum(c.amount) from rebate_calcs c
                     where c.ingest_run_id = a.ingest_run_id
                       and c.program_id = a.program_id), 0) as amount
    from rebate_calc_attempts a
    join rebate_programs p on p.id = a.program_id
    where a.ingest_run_id = :run_id
    order by p.external_ref
    """
)

#: Brand coverage: every distinct spelling in the run's canonical rows, its line count, and what
#: became of it. Derived on read and never stored, because a stored count can disagree with the rows
#: it summarises and a derived one cannot. The anti-join is run-scoped through
#: `rebate_calc_lines.ingest_run_id`, which is why that column is denormalised onto the table.
_BRAND_COVERAGE = text(
    """
    with spellings as (
      select brand_manufacturer as brand_value, count(*) as line_count
      from canonical_lines
      where ingest_run_id = :run_id
      group by brand_manufacturer
    ),
    claimed as (
      select distinct l.brand_manufacturer as brand_value
      from canonical_lines l
      join rebate_calc_lines r on r.canonical_line_id = l.id
      where r.ingest_run_id = :run_id
    )
    select s.brand_value, s.line_count,
           (s.brand_value in (select brand_value from claimed)) as claimed
    from spellings s
    order by s.line_count desc, s.brand_value
    limit :limit
    """
)

_CALCS_PAGE = text(
    """
    select c.id as rebate_calc_id, c.program_id, p.external_ref, p.name as program_name, p.kind,
           p.uom_basis, c.period_start, c.period_end, c.basis_qty, c.amount,
           c.contributed_line_count, c.skipped_line_count, c.out_of_scope_line_count,
           c.engine_name, c.engine_version
    from rebate_calcs c
    join rebate_programs p on p.id = c.program_id
    where c.ingest_run_id = :run_id
      and (cast(:cursor_period as date) is null
           or (c.period_start, c.program_id) > (cast(:cursor_period as date),
                                                cast(:cursor_program as uuid)))
    order by c.period_start, c.program_id
    limit :limit
    """
)

_CALC_OWNER = text(
    """
    select c.ingest_run_id, c.program_id, p.uom_basis, r.customer_id
    from rebate_calcs c
    join rebate_programs p on p.id = c.program_id
    join ingest_runs r on r.id = c.ingest_run_id
    where c.id = :calc_id
    """
)

_LINES_PAGE = text(
    """
    select l.id as canonical_line_id, l.source_row_number, l.invoice_no, l.invoice_date,
           l.dist_item_id, l.qty, l.uom, l.pack_size, l.catch_weight_lbs,
           r.line_status, r.skipped_reason, r.basis_contribution, r.amount_contribution
    from rebate_calc_lines r
    join canonical_lines l on l.id = r.canonical_line_id
    where r.rebate_calc_id = :calc_id
      and (cast(:status as text) is null or r.line_status = cast(:status as text))
      and (cast(:cursor_row as integer) is null
           or l.source_row_number > cast(:cursor_row as integer))
    order by l.source_row_number
    limit :limit
    """
)

#: A one-line existence query rather than an import of another feature's repo. `specs/repo` and
#: `uploads/repo` each have their own too: SQL belongs to the feature that runs it (§2), and
#: importing a sibling repo to save one line of SQL is worse coupling than the line.
_REFERENCE = text(
    """
    select name, brand_values, direct_vendor_values, redistributor_only
    from customer_manufacturers
    where customer_id = :customer_id
    order by name
    """
)

_CUSTOMER_EXISTS = text("select id from customers where id = :id")

_PROGRAMS = text(
    """
    select id as program_id, external_ref, name, kind, brand_manufacturer, rate, uom_basis,
           effective_from, effective_to
    from rebate_programs
    where customer_id = :customer_id
    order by external_ref
    """
)


async def rebate_gate(session: AsyncSession, run_id: UUID) -> tuple[int, datetime | None]:
    """`(errored attempt count, calculated_at)`. The contract P12 and P13 read."""
    row = (await session.execute(_GATE, {"run_id": run_id})).mappings().one()
    return int(row["errored"]), row["calculated_at"]


async def summary(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    return dict((await session.execute(_SUMMARY, {"run_id": run_id})).mappings().one())


async def attempts(session: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    rows = (await session.execute(_ATTEMPTS, {"run_id": run_id})).mappings().all()
    return [dict(row) for row in rows]


async def brand_coverage(
    session: AsyncSession, run_id: UUID, *, limit: int
) -> list[dict[str, Any]]:
    rows = (
        (await session.execute(_BRAND_COVERAGE, {"run_id": run_id, "limit": limit}))
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def calcs_page(
    session: AsyncSession,
    run_id: UUID,
    *,
    limit: int,
    cursor_period: Any = None,
    cursor_program: Any = None,
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                _CALCS_PAGE,
                {
                    "run_id": run_id,
                    "limit": limit,
                    "cursor_period": cursor_period,
                    "cursor_program": cursor_program,
                },
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def calc_owner(session: AsyncSession, calc_id: UUID) -> dict[str, Any] | None:
    row = (await session.execute(_CALC_OWNER, {"calc_id": calc_id})).mappings().one_or_none()
    return None if row is None else dict(row)


async def lines_page(
    session: AsyncSession,
    calc_id: UUID,
    *,
    limit: int,
    status: str | None,
    cursor_row: int | None,
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                _LINES_PAGE,
                {
                    "calc_id": calc_id,
                    "limit": limit,
                    "status": status,
                    "cursor_row": cursor_row,
                },
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def reference_rows(session: AsyncSession, customer_id: UUID) -> list[dict[str, Any]]:
    """`customer_manufacturers` rows, for building the same lookup the engine matched with."""
    rows = (await session.execute(_REFERENCE, {"customer_id": customer_id})).mappings().all()
    return [dict(row) for row in rows]


async def customer_exists(session: AsyncSession, customer_id: UUID) -> bool:
    return (await session.execute(_CUSTOMER_EXISTS, {"id": customer_id})).first() is not None


async def programs(session: AsyncSession, customer_id: UUID) -> list[dict[str, Any]]:
    rows = (await session.execute(_PROGRAMS, {"customer_id": customer_id})).mappings().all()
    return [dict(row) for row in rows]
