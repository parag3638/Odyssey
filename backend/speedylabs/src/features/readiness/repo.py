"""Readiness SQL for the read side. The only place it lives (BACKEND.md §2).

The write side is in `run.py`, on the worker's plain psycopg connection, for the same reason
the materialiser's is: the pass streams 48,150 rows and persists in one transaction. These
queries read at most sixteen rows and belong to the async routes.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_RUN = text(
    """
    select r.id, r.status, r.source_file_id, r.customer_id, r.readiness_at,
           r.materialized_at, c.name as customer_name
    from ingest_runs r
    join customers c on c.id = r.customer_id
    where r.id = :id
    """
)

_HAS_ROWS = text(
    "select exists (select 1 from canonical_lines where ingest_run_id = :run_id) as present"
)

_REJECTED = text(
    "select count(*) as rejected from materialization_errors where ingest_run_id = :run_id"
)

_FINDINGS = text(
    """
    select check_code, severity, affected_rows, affected_amount, detail, example_row_numbers
    from readiness_findings
    where ingest_run_id = :run_id
    """
)

_CHECK_RUNS = text(
    """
    select check_code, outcome, not_applicable_reason, errored_message, rows_examined, context
    from readiness_check_runs
    where ingest_run_id = :run_id
    """
)


async def run_row(session: AsyncSession, run_id: UUID) -> dict[str, Any] | None:
    row = (await session.execute(_RUN, {"id": run_id})).mappings().first()
    return dict(row) if row else None


async def has_canonical_rows(session: AsyncSession, run_id: UUID) -> bool:
    row = (await session.execute(_HAS_ROWS, {"run_id": run_id})).mappings().one()
    return bool(row["present"])


async def rejected_row_count(session: AsyncSession, run_id: UUID) -> int:
    """Rows that never became canonical lines. Stated on the checklist, because every count
    on it is a count over the rows that did."""
    row = (await session.execute(_REJECTED, {"run_id": run_id})).mappings().one()
    return int(row["rejected"])


async def findings(session: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    rows = (await session.execute(_FINDINGS, {"run_id": run_id})).mappings()
    return [dict(row) for row in rows]


async def check_runs(session: AsyncSession, run_id: UUID) -> list[dict[str, Any]]:
    rows = (await session.execute(_CHECK_RUNS, {"run_id": run_id})).mappings()
    return [dict(row) for row in rows]
