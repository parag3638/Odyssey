"""Run listing rules: limit clamping and this list's cursor key.

The key is `created_at|id`, which matches the index the list query reads. The encoding and
its refusal live in `lib/cursor.py`, because P7's preview is a second paginated list and
one opaque-token format is the point.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.runs import repo
from src.lib import cursor
from src.lib.errors import NotFound

#: §3: cursor pagination, default 25, max 100.
DEFAULT_LIMIT = 25
MAX_LIMIT = 100


@dataclass(frozen=True)
class Cursor:
    created_at: datetime
    run_id: UUID

    def encode(self) -> str:
        return cursor.encode(self.created_at.isoformat(), str(self.run_id))


def _parse(parts: list[str]) -> Cursor:
    return Cursor(created_at=datetime.fromisoformat(parts[0]), run_id=UUID(parts[1]))


def decode_cursor(value: str) -> Cursor:
    """Decode, or raise `MalformedRequest`. Nothing partially-trusted gets through."""
    return cursor.decode(
        value,
        parse=_parse,
        message="That page link is not valid. Go back to the first page of runs.",
    )


def clamp_limit(requested: int | None) -> int:
    """§3: default 25, max 100. Clamped rather than refused - a caller asking for 5000
    wants "as many as you'll give me", and a 400 would be pedantry."""
    if requested is None:
        return DEFAULT_LIMIT
    return max(1, min(requested, MAX_LIMIT))


async def list_runs(
    session: AsyncSession,
    *,
    customer_id: UUID | None,
    limit: int | None,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """A page of runs and the cursor for the next one, or None at the end."""
    size = clamp_limit(limit)
    decoded = decode_cursor(cursor) if cursor else None

    # Fetch one extra row to learn whether another page exists, without a second
    # COUNT query over the whole table.
    rows = await repo.list_runs(
        session,
        customer_id=customer_id,
        limit=size + 1,
        cursor_created_at=decoded.created_at if decoded else None,
        cursor_id=decoded.run_id if decoded else None,
    )

    has_more = len(rows) > size
    page = rows[:size]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = Cursor(created_at=last["created_at"], run_id=last["id"]).encode()
    return page, next_cursor


async def get_run(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    row = await repo.get_run(session, run_id)
    if row is None:
        # §3/§4: missing and not-yours both 404, and the message names no table.
        raise NotFound("That run does not exist. It may have been removed.")
    return row
