"""Runs SQL. The only place it lives (BACKEND.md §2).

Every statement here is a **literal** string. Nothing is interpolated, not even the
column list: semgrep's `avoid-sqlalchemy-text` blocks f-string-built SQL, and while the
interpolated parts were module constants with no user input anywhere near them, a rule
with a hand-verified exception costs a reviewer more than writing the query out. Same
call as the first migration's DROP statements.

The optional cursor is expressed as a null-guard inside one literal query rather than by
composing a WHERE clause. The customer filter gets its **own** query instead, because
`(:customer_id is null or r.customer_id = :customer_id)` would stop the planner using the
leading column of `(customer_id, created_at desc)` - and that index is the reason the
list route meets its 300 ms budget.

Casts are written `cast(:p as type)`, not `:p::type`. SQLAlchemy's `text()` scans for
`:name` parameters and the `::` shorthand confuses it: it bound one occurrence and left
the other as literal SQL, which Postgres then rejected with a syntax error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Keyset pagination on (created_at desc, id desc). Not OFFSET: an offset shifts when a
# run is inserted mid-pagination, which silently skips or repeats rows. The id is the
# tie-break, because two runs can share a timestamp and a page boundary landing inside a
# tie would drop whatever follows.
_LIST_BY_CUSTOMER = text(
    """
    select r.id, r.source_file_id, r.customer_id, r.status,
           r.error_code, r.error_message, r.created_at,
           r.parsed_at, r.profiled_at, r.mapped_at, r.materialized_at, r.readiness_at,
           r.calculated_at, r.first_variance_at, r.elapsed_ms_to_first_variance,
           r.considered_spec_id, r.spec_skip_code, r.spec_skip_detail,
           c.name as customer_name,
           f.kind, f.original_filename, f.format, f.byte_size, f.row_count
    from ingest_runs r
    join customers c on c.id = r.customer_id
    join source_files f on f.id = r.source_file_id
    where r.customer_id = :customer_id
      and (
        cast(:cursor_created_at as timestamptz) is null
        or (r.created_at, r.id)
             < (cast(:cursor_created_at as timestamptz), cast(:cursor_id as uuid))
      )
    order by r.created_at desc, r.id desc
    limit :limit
    """
)

_LIST_ALL = text(
    """
    select r.id, r.source_file_id, r.customer_id, r.status,
           r.error_code, r.error_message, r.created_at,
           r.parsed_at, r.profiled_at, r.mapped_at, r.materialized_at, r.readiness_at,
           r.calculated_at, r.first_variance_at, r.elapsed_ms_to_first_variance,
           r.considered_spec_id, r.spec_skip_code, r.spec_skip_detail,
           c.name as customer_name,
           f.kind, f.original_filename, f.format, f.byte_size, f.row_count
    from ingest_runs r
    join customers c on c.id = r.customer_id
    join source_files f on f.id = r.source_file_id
    where (
        cast(:cursor_created_at as timestamptz) is null
        or (r.created_at, r.id)
             < (cast(:cursor_created_at as timestamptz), cast(:cursor_id as uuid))
      )
    order by r.created_at desc, r.id desc
    limit :limit
    """
)

_GET_ONE = text(
    """
    select r.id, r.source_file_id, r.customer_id, r.status,
           r.error_code, r.error_message, r.created_at,
           r.parsed_at, r.profiled_at, r.mapped_at, r.materialized_at, r.readiness_at,
           r.calculated_at, r.first_variance_at, r.elapsed_ms_to_first_variance,
           r.considered_spec_id, r.spec_skip_code, r.spec_skip_detail,
           c.name as customer_name,
           f.kind, f.original_filename, f.format, f.byte_size, f.row_count
    from ingest_runs r
    join customers c on c.id = r.customer_id
    join source_files f on f.id = r.source_file_id
    where r.id = :id
    """
)


async def list_runs(
    session: AsyncSession,
    *,
    customer_id: UUID | None,
    limit: int,
    cursor_created_at: datetime | None,
    cursor_id: UUID | None,
) -> list[dict[str, Any]]:
    """One page of runs, newest first."""
    params: dict[str, Any] = {
        "limit": limit,
        "cursor_created_at": cursor_created_at,
        "cursor_id": cursor_id,
    }
    if customer_id is not None:
        params["customer_id"] = customer_id
        rows = await session.execute(_LIST_BY_CUSTOMER, params)
    else:
        rows = await session.execute(_LIST_ALL, params)
    return [dict(row) for row in rows.mappings()]


async def get_run(session: AsyncSession, run_id: UUID) -> dict[str, Any] | None:
    row = (await session.execute(_GET_ONE, {"id": run_id})).mappings().first()
    return dict(row) if row else None
