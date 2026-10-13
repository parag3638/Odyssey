"""One-at-a-time across processes, using Postgres' own advisory locks.

An `asyncio.Lock` only guards the process holding it. The API runs behind a worker and can run
behind replicas, so a per-process lock lets two resets run at once from two processes - which for a
destructive action means one deleting the data the other is rebuilding. Postgres sees every
connection, so the lock belongs there.

**Session-scoped, not transaction-scoped.** The transaction form looks safer and is wrong here: a
reset commits part-way through - the clear has to land before the rebuild reads it - and a
transaction lock would be released at that commit, leaving the rest of the destructive work
unguarded. A session lock spans those commits, and Postgres still releases it when the connection
ends, so a crashed process does not strand it either.

**"Session" means the Postgres backend connection, not a SQLAlchemy `Session`, and conflating the
two is a real bug this module shipped.** `hold` exists because of it. An `AsyncSession` returns its
connection to the pool on every `commit()` and checks one back out for the next statement, with no
promise it is the same physical connection; a lock taken through a session that then commits can
therefore be released against a connection that never held it. `pg_advisory_unlock` on a lock you
do not hold returns `false` and raises nothing, so this fails **silently**, leaving the real holder
idle in the pool still holding the lock - wedging the action until the process restarts, which is
precisely the failure the advisory lock was chosen over `completed_at is null` to avoid. A tester
proved the mechanism deterministically with two connections on one pooled engine
(`tests/test_demo_reset_lock_bug_report.py`). So anything guarding work that commits must hold the
lock on a connection **it owns for the whole span**, which is what `hold` gives it.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

_TRY_LOCK: Final = text("select pg_try_advisory_lock(:key) as acquired")
_UNLOCK: Final = text("select pg_advisory_unlock(:key)")
#: Is anyone holding it? `pg_locks` is the authority. A one-bigint advisory lock is stored split
#: across two int4 columns - `classid` the high half, `objid` the low - so the key is reassembled
#: rather than compared directly. Checked against a negative key (ours is a signed 64-bit hash, so
#: roughly half of all names produce one) and the round trip is exact:
#:   select pg_try_advisory_lock((-1234567890123)::bigint);
#:   select (classid::bigint << 32) | objid::bigint from pg_locks where locktype = 'advisory';
#:   -- -1234567890123
_IS_HELD: Final = text(
    """
    select exists (
      select 1 from pg_locks
      where locktype = 'advisory'
        and granted
        and ((classid::bigint << 32) | objid::bigint) = :key
    ) as held
    """
)


def lock_key(name: str) -> int:
    """A stable signed 64-bit key from a name, so callers name the lock rather than a number."""
    digest = hashlib.sha256(name.encode()).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


@asynccontextmanager
async def hold(engine: AsyncEngine, name: str) -> AsyncGenerator[bool, None]:
    """Hold the named lock on a connection owned for the whole block. The safe way to use this.

    Yields whether the lock was acquired. The connection is checked out of the pool for the
    duration and closed on exit, so the lock is released by `pg_advisory_unlock` on the connection
    that took it, and by Postgres itself if this process dies first. Neither is true of a lock
    taken through a pooled `AsyncSession` that commits - see the module docstring.

    `try_acquire`/`release` remain for a caller that genuinely holds one connection for the whole
    span and does not commit in between; prefer this.
    """
    async with engine.connect() as conn:
        row = (await conn.execute(_TRY_LOCK, {"key": lock_key(name)})).one()
        acquired = bool(row[0])
        try:
            yield acquired
        finally:
            if acquired:
                await conn.execute(_UNLOCK, {"key": lock_key(name)})


async def try_acquire(session: AsyncSession, name: str) -> bool:
    """True when this session now holds the named lock. Release it with `release`.

    **Only safe when the caller does not commit before releasing.** Use `hold` otherwise.
    """
    row = (await session.execute(_TRY_LOCK, {"key": lock_key(name)})).one()
    return bool(row[0])


async def is_held(session: AsyncSession | AsyncConnection, name: str) -> bool:
    """True when some session - any session, in any process - holds the named lock.

    This is how a caller answers "is one running right now?" without a staleness heuristic. The
    obvious alternative, reading a `completed_at is null` row, cannot tell a live run from one whose
    worker died: nothing clears that column, so one crash would wedge the action forever. An
    advisory lock has no such failure mode - Postgres drops it when the holding connection ends -
    so its absence genuinely means "nobody is doing this".

    Advisory, never authoritative: another process can take the lock between this answer and the
    caller acting on it. The `try_acquire` inside the work is still the thing that decides.
    """
    row = (await session.execute(_IS_HELD, {"key": lock_key(name)})).one()
    return bool(row[0])


async def release(session: AsyncSession, name: str) -> None:
    """Give the lock back. Safe to call when it is not held."""
    await session.execute(_UNLOCK, {"key": lock_key(name)})
