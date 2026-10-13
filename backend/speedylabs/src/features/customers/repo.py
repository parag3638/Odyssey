"""Customer SQL."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_customers(session: AsyncSession) -> list[dict[str, Any]]:
    """Every customer with its run count.

    A left join and a group-by rather than a count per row: §11 forbids queries in
    loops, and the N+1 version would issue one query per customer for a dropdown.
    """
    rows = await session.execute(
        text(
            """
            select c.id, c.name, c.slug, count(r.id) as run_count
            from customers c
            left join ingest_runs r on r.customer_id = c.id
            group by c.id, c.name, c.slug
            order by c.name
            """
        )
    )
    return [dict(row) for row in rows.mappings()]
