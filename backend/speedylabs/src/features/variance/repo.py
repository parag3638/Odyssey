"""Variance SQL for the read side, and the gate P13 reads. The only place it lives (§2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Whether this run has a comparison at all, and whether it is older than the calculation it was
#: made from. P13 reads the same pair, so the two phases cannot count different things.
_GATE = text(
    """
    select
      (select count(*) from variances where ingest_run_id = :run_id) as variance_count,
      (select max(created_at) from variances where ingest_run_id = :run_id) as computed_at,
      (select calculated_at from ingest_runs where id = :run_id) as calculated_at
    """
)

_SUMMARY = text(
    """
    select
      (select count(*) from variances where ingest_run_id = :run_id) as variance_count,
      (select coalesce(sum(legacy_amount), 0) from variances where ingest_run_id = :run_id)
        as legacy_total,
      (select coalesce(sum(calculated_amount), 0) from variances where ingest_run_id = :run_id)
        as calculated_total,
      (select coalesce(sum(variance_amount), 0) from variances where ingest_run_id = :run_id)
        as net_variance,
      (select coalesce(sum(abs(variance_amount)), 0) from variances where ingest_run_id = :run_id)
        as absolute_variance,
      (select max(created_at) from variances where ingest_run_id = :run_id) as computed_at,
      (select calculated_at from ingest_runs where id = :run_id) as calculated_at,
      -- One comparison per run, so every row carries the same file. `limit 1` rather than an
      -- aggregate: Postgres has no `max(uuid)`, and "any row's value" is what is actually meant.
      (select legacy_source_file_id from variances where ingest_run_id = :run_id limit 1)
        as legacy_source_file_id
    """
)

_PAGE = text(
    """
    select v.id as variance_id, v.program_id, p.external_ref, p.name as program_name, p.kind,
           v.period_start, v.period_end, v.legacy_amount, v.calculated_amount, v.variance_amount,
           v.presence, v.root_cause, v.explained
    from variances v
    join rebate_programs p on p.id = v.program_id
    where v.ingest_run_id = :run_id
      and (cast(:cause as text) is null or v.root_cause = cast(:cause as text))
      and (cast(:presence as text) is null or v.presence = cast(:presence as text))
      -- `?cause=` asks "which variances does this cause *dominate*"; `?linkCause=` asks "which
      -- variances does it *touch*". They are different questions and the second is usually the
      -- one meant: `duplicate_line` dominates no variance and touches fourteen.
      and (cast(:link_cause as text) is null or exists (
            select 1 from variance_line_links k
            where k.variance_id = v.id and k.root_cause = cast(:link_cause as text)))
      and (cast(:cursor_period as date) is null
           or (v.period_start, v.program_id) > (cast(:cursor_period as date),
                                                cast(:cursor_program as uuid)))
    order by v.period_start, v.program_id
    limit :limit
    """
)

#: The legacy rows nobody could match, for the warning panel. Scoped to the file the comparison
#: actually used, so a second export's problems never appear beside this one's numbers.
_UNMATCHED = text(
    """
    select source_row_number, program_external_ref, program_name_text, period_start, amount
    from legacy_rebate_lines
    where source_file_id = :legacy_file_id and match_status = 'unmatched_program'
    order by source_row_number
    limit :limit
    """
)

_LEGACY_FILES = text(
    """
    select f.id as source_file_id, f.original_filename, f.created_at as uploaded_at,
           r.status, r.error_code,
           (select count(*) from legacy_rebate_lines l where l.source_file_id = f.id) as line_count,
           (select count(*) from legacy_rebate_lines l
             where l.source_file_id = f.id and l.match_status = 'matched') as matched_count
    from source_files f
    join ingest_runs r on r.source_file_id = f.id
    where f.customer_id = :customer_id and f.kind = 'legacy_rebate'
    order by f.created_at desc
    limit :limit
    """
)

#: Whether a *result set* exists, not just a timestamp. `calculated_at` can be set by hand; this
#: counts the rows a comparison would actually be against.
_CALCULATED_PERIOD_COUNT = text("select count(*) from rebate_calcs where ingest_run_id = :run_id")

_LEGACY_LINES = text(
    """
    select l.source_row_number, l.program_external_ref, l.program_name_text, l.program_id,
           p.name as program_name, l.period_start, l.period_end, l.amount, l.match_status
    from legacy_rebate_lines l
    left join rebate_programs p on p.id = l.program_id
    where l.source_file_id = :source_file_id
      and (cast(:match as text) is null or l.match_status = cast(:match as text))
      and (cast(:cursor_row as integer) is null
           or l.source_row_number > cast(:cursor_row as integer))
    order by l.source_row_number
    limit :limit
    """
)

_MATCHED_COUNT = text(
    """
    select count(*) from legacy_rebate_lines
    where source_file_id = :source_file_id and match_status = 'matched'
    """
)

_LEGACY_FILE_OWNER = text(
    """
    select f.customer_id, f.kind, r.status
    from source_files f join ingest_runs r on r.source_file_id = f.id
    where f.id = :source_file_id
    """
)


_ATTRIBUTION_FRESHNESS = text(
    """
    select
      (select max(created_at) from variance_line_links where ingest_run_id = :run_id)
        as attributed_at,
      (select max(created_at) from variances where ingest_run_id = :run_id) as computed_at
    """
)


async def attribution_freshness(
    session: AsyncSession, run_id: UUID
) -> tuple[datetime | None, datetime | None]:
    """`(when it was attributed, when the comparison it explains was computed)`."""
    row = (await session.execute(_ATTRIBUTION_FRESHNESS, {"run_id": run_id})).mappings().one()
    return row["attributed_at"], row["computed_at"]


async def variance_gate(
    session: AsyncSession, run_id: UUID
) -> tuple[int, datetime | None, datetime | None]:
    """`(variance count, when they were computed, when the rebates were calculated)`.

    The contract P13 reads. A zero count means there is nothing to attribute; a `computed_at`
    older than `calculated_at` means the comparison is stale.
    """
    row = (await session.execute(_GATE, {"run_id": run_id})).mappings().one()
    return int(row["variance_count"]), row["computed_at"], row["calculated_at"]


async def summary(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    return dict((await session.execute(_SUMMARY, {"run_id": run_id})).mappings().one())


async def page(
    session: AsyncSession,
    run_id: UUID,
    *,
    limit: int,
    cause: str | None,
    presence: str | None = None,
    link_cause: str | None = None,
    cursor_period: Any = None,
    cursor_program: Any = None,
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                _PAGE,
                {
                    "run_id": run_id,
                    "limit": limit,
                    "cause": cause,
                    "presence": presence,
                    "link_cause": link_cause,
                    "cursor_period": cursor_period,
                    "cursor_program": cursor_program,
                },
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def unmatched(
    session: AsyncSession, legacy_file_id: UUID, *, limit: int
) -> list[dict[str, Any]]:
    rows = (
        (await session.execute(_UNMATCHED, {"legacy_file_id": legacy_file_id, "limit": limit}))
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def legacy_files(
    session: AsyncSession, customer_id: UUID, *, limit: int
) -> list[dict[str, Any]]:
    rows = (
        (await session.execute(_LEGACY_FILES, {"customer_id": customer_id, "limit": limit}))
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def calculated_period_count(session: AsyncSession, run_id: UUID) -> int:
    """How many calculated periods a comparison would be against. Zero means there is nothing."""
    row = (await session.execute(_CALCULATED_PERIOD_COUNT, {"run_id": run_id})).one()
    return int(row[0])


async def legacy_lines(
    session: AsyncSession,
    source_file_id: UUID,
    *,
    limit: int,
    match: str | None,
    cursor_row: int | None,
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                _LEGACY_LINES,
                {
                    "source_file_id": source_file_id,
                    "limit": limit,
                    "match": match,
                    "cursor_row": cursor_row,
                },
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def matched_count(session: AsyncSession, legacy_file_id: UUID) -> int:
    """How many of that export's rows found a program. Zero means there is nothing to compare."""
    row = (await session.execute(_MATCHED_COUNT, {"source_file_id": legacy_file_id})).one()
    return int(row[0])


async def legacy_file_owner(session: AsyncSession, source_file_id: UUID) -> dict[str, Any] | None:
    row = (
        (await session.execute(_LEGACY_FILE_OWNER, {"source_file_id": source_file_id}))
        .mappings()
        .one_or_none()
    )
    return None if row is None else dict(row)
