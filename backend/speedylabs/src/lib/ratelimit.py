"""Per-IP fixed-window rate limiting, in Postgres.

No Redis: the queue already lives in Postgres and a second stateful service for a
counter is not worth running. A fixed window rather than a sliding one because the
limits here are coarse (20 uploads an hour) and a fixed window is one row and one
statement; a sliding window would need either a row per request or a Lua-style script.

Two properties the implementation has to have, both easy to get wrong:

**The increment is atomic.** `INSERT ... ON CONFLICT ... DO UPDATE SET count = count + 1
RETURNING count` is one statement, so two concurrent requests at the boundary cannot
both read 20 and both write 21. Read-then-write would let exactly that through, and the
window where it matters is the window an attacker aims for.

**It fails closed.** If the table is unreachable the request is refused with 503, not
allowed through. An open-on-failure limiter is worse than none, because it turns a
database blip into an unmetered endpoint while the dashboard still looks healthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.errors import DependencyUnavailable, RateLimited


@dataclass(frozen=True)
class RateLimitResult:
    count: int
    limit: int
    retry_after_seconds: int

    @property
    def exceeded(self) -> bool:
        return self.count > self.limit


def window_start(now: datetime, window_seconds: int) -> datetime:
    """Floor `now` to the window boundary, so every caller agrees on the bucket."""
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % window_seconds), tz=UTC)


_INCREMENT = text(
    """
    insert into rate_limits (key, window_start, count)
    values (:key, :window_start, 1)
    on conflict (key, window_start)
    do update set count = rate_limits.count + 1
    returning count
    """
)


async def hit(
    session: AsyncSession,
    *,
    key: str,
    limit: int,
    window_seconds: int,
    now: datetime | None = None,
) -> RateLimitResult:
    """Count this request against `key`. Returns the new count; never raises on limit."""
    moment = now or datetime.now(UTC)
    start = window_start(moment, window_seconds)

    try:
        row = (await session.execute(_INCREMENT, {"key": key, "window_start": start})).scalar_one()
        await session.commit()
    except Exception as exc:
        await session.rollback()
        # Fail closed. The message says nothing about the database (§3).
        raise DependencyUnavailable(
            "Uploads are briefly unavailable. Try again in a moment."
        ) from exc

    remaining = int((start + timedelta(seconds=window_seconds) - moment).total_seconds())
    return RateLimitResult(count=int(row), limit=limit, retry_after_seconds=max(remaining, 1))


async def enforce(
    session: AsyncSession,
    *,
    key: str,
    limit: int,
    window_seconds: int,
    now: datetime | None = None,
    what: str = "uploads",
) -> RateLimitResult:
    """`hit`, then refuse with 429 if the caller is over. Raises `RateLimited`.

    `what` names the action in the message, because a user who hit the proposal limit is
    told "too many uploads" otherwise - which sends them looking at the wrong thing. It
    defaults to the original wording so no existing caller changes behaviour.
    """
    result = await hit(session, key=key, limit=limit, window_seconds=window_seconds, now=now)
    if result.exceeded:
        minutes = max(result.retry_after_seconds // 60, 1)
        raise RateLimited(
            f"Too many {what} from this address. Try again in about {minutes} "
            f"{'minute' if minutes == 1 else 'minutes'}.",
            # §3: a 429 includes Retry-After. The prose is for a person; the header is
            # for a client that retries on its own.
            headers={"Retry-After": str(result.retry_after_seconds)},
        )
    return result
