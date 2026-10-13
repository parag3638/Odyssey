"""Drift SQL for the read side, and the decision write. The only place it lives (§2).

The comparison itself is written worker-side in `run.py`, inside the profile job's transaction, for
the same reason the readiness pass is: it belongs to that transaction and not to a request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_RUN = text(
    """
    select r.id, r.status, r.source_file_id, r.customer_id, r.drift_baseline_spec_id,
           r.drift_baseline_source_file_id, r.drift_decision, r.drift_at,
           c.name as customer_name, f.original_filename, f.format, f.kind
    from ingest_runs r
    join customers c on c.id = r.customer_id
    join source_files f on f.id = r.source_file_id
    where r.id = :id
    """
)

_BASELINE = text(
    """
    select s.version, s.approved_at, s.approved_from_layout_version,
           f.id as source_file_id, f.original_filename, f.created_at as uploaded_at
    from mapping_specs s
    left join source_files f on f.id = s.approved_from_source_file_id
    where s.id = :spec_id
    """
)

_CHECKS = text(
    """
    select drift_code, outcome, reason, columns_examined
    from drift_checks where ingest_run_id = :run_id
    """
)

_FINDINGS = text(
    """
    select drift_code, severity, column_name, baseline_column_name, detail
    from drift_findings where ingest_run_id = :run_id
    """
)

#: Conditional on the decision still being `pending`, so two concurrent decisions cannot both win -
#: the loser sees no row and is told the first one stands.
_DECIDE = text(
    """
    update ingest_runs
       set drift_decision = :decision, status = :status, updated_at = now()
     where id = :id and drift_decision = 'pending'
    returning id
    """
)

_PROFILE_COLUMNS = text(
    """
    select column_index, column_name, type_guess, null_pct, distinct_count, value_counts,
           numeric_count, numeric_sum
    from column_profiles
    where source_file_id = :source_file_id and layout_version = :layout_version
    order by column_index
    """
)

_CURRENT_LAYOUT_VERSION = text(
    """
    select max(version) as version from file_layouts where source_file_id = :source_file_id
    """
)


async def run_row(session: AsyncSession, run_id: UUID) -> dict[str, Any] | None:
    row = (await session.execute(_RUN, {"id": run_id})).mappings().first()
    return dict(row) if row else None


async def baseline(session: AsyncSession, spec_id: UUID) -> dict[str, Any] | None:
    row = (await session.execute(_BASELINE, {"spec_id": spec_id})).mappings().first()
    return dict(row) if row else None


async def checks(session: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    rows = (await session.execute(_CHECKS, {"run_id": run_id})).mappings()
    return [dict(row) for row in rows]


async def findings(session: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    rows = (await session.execute(_FINDINGS, {"run_id": run_id})).mappings()
    return [dict(row) for row in rows]


async def profile_columns(
    session: AsyncSession, source_file_id: UUID, layout_version: int
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            _PROFILE_COLUMNS,
            {"source_file_id": source_file_id, "layout_version": layout_version},
        )
    ).mappings()
    return [dict(row) for row in rows]


async def current_layout_version(session: AsyncSession, source_file_id: UUID) -> int | None:
    row = (
        (await session.execute(_CURRENT_LAYOUT_VERSION, {"source_file_id": source_file_id}))
        .mappings()
        .first()
    )
    return None if row is None or row["version"] is None else int(row["version"])


async def decide(
    session: AsyncSession, run_id: UUID, *, decision: str, status: str, decided_at: datetime
) -> bool:
    """Record the decision, or report that one already stands."""
    del decided_at  # the run's `updated_at` carries it; `drift_at` records the comparison
    row = (
        await session.execute(_DECIDE, {"id": run_id, "decision": decision, "status": status})
    ).first()
    return row is not None


_SPEC_STANDING = text(
    """
    select
        (select min(s.version) from mapping_specs s
          where s.customer_id = :customer_id and s.approved_from_source_file_id = :source_file_id)
            as made_from_version,
        exists (select 1 from mapping_specs s
                 where s.customer_id = :customer_id and s.status = 'approved') as any_approved
    """
)


async def spec_standing(
    session: AsyncSession, customer_id: UUID, source_file_id: UUID
) -> dict[str, Any]:
    """Whether a spec was approved *from* this file, and whether the customer has one at all.

    Read when a run carries no drift checks, to say why rather than always "no approved file
    yet" - which was false on the very file the approved spec was made from.
    """
    row = (
        (
            await session.execute(
                _SPEC_STANDING, {"customer_id": customer_id, "source_file_id": source_file_id}
            )
        )
        .mappings()
        .one()
    )
    return dict(row)
