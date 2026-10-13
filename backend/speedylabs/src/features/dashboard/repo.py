"""Dashboard SQL. The only place it lives (§2).

Every figure here is **restated**, never recomputed. The explained percentage in particular is read
from the same links P13's summary reads; a second computation of the project's headline number is
the one duplication this phase must not introduce.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: **Scoped to the demo customer**, and ordered by how far the run got rather than by when it
#: arrived. Both halves were wrong: the first selected across every customer in the database - a
#: cross-tenant read with no sign-in in front of it, invisible today only because one customer
#: exists - and the second meant the newest *upload* won the headline card, so a drift-blocked
#: file uploaded after the finished run would have replaced it with nothing to show.
_LATEST = text(
    """
    select r.id as ingest_run_id, c.name as customer_name, f.original_filename, r.status,
           f.row_count, r.created_at, r.elapsed_ms_to_first_variance, r.stage_timings,
           r.first_variance_at
    from ingest_runs r
    join customers c on c.id = r.customer_id
    join source_files f on f.id = r.source_file_id
    where f.kind = 'erp_invoice_lines' and c.slug = :slug
    order by
      (r.first_variance_at is not null) desc,
      (r.calculated_at is not null) desc,
      (r.materialized_at is not null) desc,
      r.created_at desc
    limit 1
    """
)

_RUNS = text(
    """
    select r.id as ingest_run_id, f.original_filename, r.status, f.row_count, r.created_at,
           r.elapsed_ms_to_first_variance
    from ingest_runs r
    join source_files f on f.id = r.source_file_id
    join customers c on c.id = r.customer_id
    where c.slug = :slug
    order by r.created_at desc
    limit :limit
    """
)

#: The legacy run's own `legacy_import` timing sits on a *different* run - the legacy file's. The
#: machine total is not complete without it, so it is fetched by following the comparison back.
_LEGACY_STAGE = text(
    """
    select lr.stage_timings
    from variances v
    join source_files f on f.id = v.legacy_source_file_id
    join ingest_runs lr on lr.source_file_id = f.id
    where v.ingest_run_id = :run_id
    limit 1
    """
)

_TOTALS = text(
    """
    select
      (select count(*) from readiness_findings
        where ingest_run_id = :run_id and severity = 'blocking') as blocking_findings,
      (select coalesce(sum(abs(variance_amount)), 0) from variances
        where ingest_run_id = :run_id) as absolute_variance,
      (select coalesce(sum(abs(contribution_amount)), 0) from variance_line_links
        where ingest_run_id = :run_id and root_cause = 'unexplained') as unexplained_amount,
      (select count(*) from variance_line_links where ingest_run_id = :run_id) as link_count,
      -- Read off the stored rows, not from today's constant: the label has to name the engine that
      -- produced *these* numbers, which is the whole point of storing it per row.
      (select engine_name from rebate_calcs where ingest_run_id = :run_id limit 1) as engine_name,
      (select engine_version from rebate_calcs where ingest_run_id = :run_id limit 1)
        as engine_version
    """
)

#: The worker's own heartbeat. `procrastinate_jobs` carries **no timestamp column at all**, so the
#: queue's health cannot be read from the table mvp.md names; `procrastinate_workers` has one and
#: it is indexed.
_QUEUE = text(
    """
    select
      (select extract(epoch from (now() - max(last_heartbeat)))
        from procrastinate_workers) as worker_last_heartbeat_seconds,
      (select count(*) from procrastinate_jobs where status = 'todo') as waiting_count,
      (select count(*) from procrastinate_jobs where status = 'doing') as running_count
    """
)


async def latest_run(session: AsyncSession, *, slug: str) -> dict[str, Any] | None:
    row = (await session.execute(_LATEST, {"slug": slug})).mappings().first()
    return None if row is None else dict(row)


async def runs(session: AsyncSession, *, slug: str, limit: int) -> list[dict[str, Any]]:
    rows = (await session.execute(_RUNS, {"slug": slug, "limit": limit})).mappings().all()
    return [dict(row) for row in rows]


async def legacy_stage_timings(session: AsyncSession, run_id: UUID) -> dict[str, Any] | None:
    row = (await session.execute(_LEGACY_STAGE, {"run_id": run_id})).mappings().first()
    return None if row is None else dict(row["stage_timings"] or {})


async def totals(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    return dict((await session.execute(_TOTALS, {"run_id": run_id})).mappings().one())


async def queue(session: AsyncSession) -> dict[str, Any]:
    return dict((await session.execute(_QUEUE)).mappings().one())
