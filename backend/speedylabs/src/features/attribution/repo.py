"""Attribution SQL for the read side. The only place it lives (§2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: The headline. Two things about it are easy to get wrong and were:
#:
#: * The denominator is **absolute** variance. `date_cutoff` moves money between periods and nets to
#:   zero, so a net denominator understates the work and can be `0/0`.
#: * The numerator is `absolute_variance - unexplained`, **not** the sum of link magnitudes. A
#:   relocation is written as two links naming one line, so summing magnitudes counts those dollars
#:   twice - which produced an "explained" total of 10,965.83 against a 9,539.64 whole, rendered on
#:   screen as a share larger than the thing it was a share of.
_SUMMARY = text(
    """
    select
      (select count(*) from variance_line_links where ingest_run_id = :run_id) as link_count,
      (select count(*) from variances where ingest_run_id = :run_id) as variance_count,
      (select coalesce(sum(abs(variance_amount)), 0) from variances where ingest_run_id = :run_id)
        as absolute_variance,
      (select coalesce(sum(abs(contribution_amount)), 0) from variance_line_links
        where ingest_run_id = :run_id and root_cause = 'unexplained') as unexplained_amount,
      -- Kept for the drill-down's own arithmetic, and deliberately **not** the numerator of the
      -- headline: `date_cutoff` writes two links per line, so summing link magnitudes counts a
      -- relocation twice and can exceed the total variance it is supposed to be a share of.
      (select coalesce(sum(abs(contribution_amount)), 0) from variance_line_links
        where ingest_run_id = :run_id and root_cause <> 'unexplained') as link_magnitude
    """
)

#: Grouped on the **link**, never on `variances.root_cause`. Measured: `duplicate_line` is the
#: dominant cause of zero variances and appears in fourteen, so a chart grouped on the variance
#: would lose a $470.93 cause entirely.
_BY_CAUSE = text(
    """
    select root_cause, count(*) as link_count,
           count(distinct canonical_line_id) as line_count,
           sum(contribution_amount) as net_amount,
           sum(abs(contribution_amount)) as absolute_amount
    from variance_line_links
    where ingest_run_id = :run_id
    group by root_cause
    order by sum(abs(contribution_amount)) desc
    """
)

_LINES = text(
    """
    select k.id as link_id, k.root_cause, k.contribution_amount, k.evidence,
           l.id as canonical_line_id, l.source_row_number, l.invoice_no, l.invoice_date,
           l.dist_item_id, l.qty, l.uom, l.catch_weight_lbs
    from variance_line_links k
    left join canonical_lines l on l.id = k.canonical_line_id
    where k.variance_id = :variance_id
      and (cast(:cause as text) is null or k.root_cause = cast(:cause as text))
      and (cast(:cursor_row as integer) is null
           or coalesce(l.source_row_number, 0) > cast(:cursor_row as integer))
    order by coalesce(l.source_row_number, 0), k.root_cause
    limit :limit
    """
)

_VARIANCE_OWNER = text(
    "select ingest_run_id, program_id, period_start from variances where id = :variance_id"
)


#: Any rule that failed on this run, read back off one variance's `detail` - the job writes the
#: same map to every variance, so one row answers it.
_RULE_FAILURES = text(
    """
    select detail -> 'ruleFailures' as rule_failures
    from variances where ingest_run_id = :run_id limit 1
    """
)


async def rule_failures(session: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    row = (await session.execute(_RULE_FAILURES, {"run_id": run_id})).mappings().first()
    if row is None or row["rule_failures"] is None:
        return []
    failures: list[dict[str, Any]] = row["rule_failures"]
    return failures


async def summary(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    return dict((await session.execute(_SUMMARY, {"run_id": run_id})).mappings().one())


async def by_cause(session: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    rows = (await session.execute(_BY_CAUSE, {"run_id": run_id})).mappings().all()
    return [dict(row) for row in rows]


async def variance_owner(session: AsyncSession, variance_id: UUID) -> dict[str, Any] | None:
    row = (
        (await session.execute(_VARIANCE_OWNER, {"variance_id": variance_id}))
        .mappings()
        .one_or_none()
    )
    return None if row is None else dict(row)


async def lines(
    session: AsyncSession,
    variance_id: UUID,
    *,
    limit: int,
    cause: str | None,
    cursor_row: int | None,
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                _LINES,
                {
                    "variance_id": variance_id,
                    "limit": limit,
                    "cause": cause,
                    "cursor_row": cursor_row,
                },
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


#: Everything the Glass export needs, in two queries rather than one per variance.
_EXPORT_HEAD = text(
    """
    select c.name as customer_name, f.original_filename as legacy_source_file,
           min(v.period_start) as period_start, max(v.period_end) as period_end,
           coalesce(sum(v.variance_amount), 0) as net_variance,
           coalesce(sum(abs(v.variance_amount)), 0) as absolute_variance
    from variances v
    join ingest_runs r on r.id = v.ingest_run_id
    join customers c on c.id = r.customer_id
    left join source_files f on f.id = v.legacy_source_file_id
    where v.ingest_run_id = :run_id
    group by c.name, f.original_filename
    """
)

_EXPORT_VARIANCES = text(
    """
    select v.id as variance_id, p.external_ref, p.name as program_name,
           v.period_start, v.period_end, v.legacy_amount, v.calculated_amount,
           v.variance_amount, v.presence, v.root_cause, v.detail
    from variances v
    join rebate_programs p on p.id = v.program_id
    where v.ingest_run_id = :run_id
    order by v.period_start, p.external_ref
    """
)

_EXPORT_LINES = text(
    """
    select k.variance_id, k.root_cause, k.contribution_amount, k.evidence,
           l.invoice_no, l.invoice_date, l.dist_item_id, l.source_row_number
    from variance_line_links k
    left join canonical_lines l on l.id = k.canonical_line_id
    where k.ingest_run_id = :run_id
    order by k.variance_id, coalesce(l.source_row_number, 0)
    """
)


async def export_head(session: AsyncSession, run_id: UUID) -> dict[str, Any] | None:
    row = (await session.execute(_EXPORT_HEAD, {"run_id": run_id})).mappings().first()
    return None if row is None else dict(row)


async def export_variances(session: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    rows = (await session.execute(_EXPORT_VARIANCES, {"run_id": run_id})).mappings().all()
    return [dict(row) for row in rows]


async def export_lines(session: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    rows = (await session.execute(_EXPORT_LINES, {"run_id": run_id})).mappings().all()
    return [dict(row) for row in rows]
