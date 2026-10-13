"""Demo state and the reset's own SQL. The only place it lives (§2).

**The reset is one scoped cascade plus four global statements.** Measured against the schema at
head: 23 of the 27 application tables reach `customers` by an unbroken `on delete cascade` chain, so
deleting the demo customer takes `canonical_lines`, `rebate_calc_lines`, `variances`,
`variance_line_links`, `source_files`, `ingest_runs` and seventeen others with it - ~145,000 rows in
163 ms.

**Scoped by slug, never `delete from customers`.** The unscoped form was used once, in a rolled-back
transaction, to measure the cascade. It must never appear in shipping code: a second customer's data
is not this demo's to destroy.

**The four that do not cascade** each clear for a stated reason:

* `idempotent_requests` - a replayed key would answer a reset-ago request about runs that no longer
  exist.
* `llm_spend` and `llm_spend_reservations` - the demo's budget should start full, and a reservation
  whose source file is gone is an orphan holding spend nothing will release.
* `rate_limits` - **except the reset's own keys**. Clearing those would let the next click through
  immediately, so the guard would defeat itself.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: The files this demo's rows point at, read **before** the cascade removes the rows that name
#: them. Without this the uploads volume accumulates every file any visitor ever sent: 3,349 of
#: them had piled up by the time this was measured, none reachable from any row.
_STORED_NAMES = text(
    """
    select f.stored_name from source_files f
    join customers c on c.id = f.customer_id
    where c.slug = :slug
    """
)

#: Deleting this one row cascades to 23 tables. Scoped by slug, deliberately.
_DELETE_CUSTOMER = text("delete from customers where slug = :slug")

_CLEAR_IDEMPOTENCY = text("delete from idempotent_requests")
_CLEAR_SPEND = text("delete from llm_spend")
_CLEAR_RESERVATIONS = text("delete from llm_spend_reservations")
#: Everything but the reset's own buckets - see the module docstring.
_CLEAR_RATE_LIMITS = text("delete from rate_limits where key not like 'demo_reset:%'")

_STATE = text(
    """
    select s.seed_version, s.seeded_at, s.completed_at,
           (select name from customers where slug = :slug) as customer_name,
           (select count(*) from ingest_runs r join customers c on c.id = r.customer_id
             where c.slug = :slug) as run_count
    from demo_seed_state s
    order by s.seeded_at desc
    limit 1
    """
)

_UPSERT_START = text(
    """
    insert into demo_seed_state (id, seed_version, seeded_at, completed_at)
    values (:id, :version, now(), null)
    on conflict (seed_version) do update set seeded_at = now(), completed_at = null,
      updated_at = now()
    """
)

_MARK_COMPLETE = text(
    "update demo_seed_state set completed_at = now(), updated_at = now() "
    "where seed_version = :version"
)


async def state(session: AsyncSession, *, slug: str) -> dict[str, Any] | None:
    row = (await session.execute(_STATE, {"slug": slug})).mappings().first()
    return None if row is None else dict(row)


async def stored_names(session: AsyncSession, *, slug: str) -> list[str]:
    """Must be read before `clear`, which removes the rows that name these files."""
    rows = (await session.execute(_STORED_NAMES, {"slug": slug})).all()
    return [str(row[0]) for row in rows]


async def clear(session: AsyncSession, *, slug: str) -> None:
    """The cascade, then the four that do not cascade."""
    await session.execute(_DELETE_CUSTOMER, {"slug": slug})
    await session.execute(_CLEAR_IDEMPOTENCY)
    await session.execute(_CLEAR_SPEND)
    await session.execute(_CLEAR_RESERVATIONS)
    await session.execute(_CLEAR_RATE_LIMITS)


async def mark_started(session: AsyncSession, *, seed_id: Any, version: str) -> None:
    await session.execute(_UPSERT_START, {"id": seed_id, "version": version})


async def mark_complete(session: AsyncSession, *, version: str) -> None:
    await session.execute(_MARK_COMPLETE, {"version": version})
