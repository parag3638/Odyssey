"""History: found by attacking `POST /v1/demo/reset` as the P14-16 round-2 handover asked
("it is new, it is the only destructive route, and it is the only async task in `tasks.py` ...
please attack this one").

`src/features/demo/service.py::run_reset` used to take `_RESET_LOCK` with `singleflight.try_acquire`
on its one `AsyncSession`, which commits at least twice more before it is done (`repo.clear`'s
commit, then `seed()`'s own commits), and release it in a `finally` on that same session.
`pg_advisory_lock`/`pg_try_advisory_lock` are tied to the physical backend connection, not to the
SQLAlchemy `Session` wrapper - `AsyncSession.commit()` ends the transaction and returns the
underlying DBAPI connection to the engine's pool, and the *next* statement on that same `Session`
checks a connection back out with no promise it is the same physical one. Under pool contention,
`run_reset`'s session could resume on a different connection than the one that acquired the lock;
`release()` would then call `pg_advisory_unlock` on a connection that never held it, which returns
`false` and raises nothing - a silent no-op. The real holder would sit idle in the pool, still
locked, and every later `POST /v1/demo/reset` would get `ResetInProgress` (409) until the worker
process restarted - the exact "wedged forever" failure the advisory lock was chosen over a
`completed_at is null` check to avoid, reached by a different door.

Proven at the time with two distinct, simultaneously-open connections on one pooled engine -
session A acquires on connection A, commits (returning A to the pool, not closing it), and session
B, bound to a separate connection, calls `release()`; the lock survived. That assertion is no
longer a useful regression test of *this codebase*, which is why it is not here any more: it was
asserting a true fact about Postgres advisory locks and SQLAlchemy's pooling (a session-scoped lock
can only be released by the backend connection that took it, and a pooled session does not promise
to keep one across commits) rather than anything this application controls, so no application fix
could ever make `still_held is False` pass for that exact setup - the two connections in that test
never shared an owner across the commit, by construction. See the control test below for the
version of that same claim that *is* about this codebase: whether a commit in between loses the
owning connection, which `hold()` exists to prevent.

**The fix:** `src/lib/singleflight.hold(engine, name)`, an async context manager that checks out
and owns one physical connection for its entire block - the lock is taken and released on that same
connection regardless of how many times some other session commits inside the block. `run_reset`
now wraps its whole body in it (`db.engine()` is the new accessor that gives it the engine rather
than a pooled session). The two tests below are the regression for that fix: the first mirrors
`run_reset`'s actual shape - a lock held via `hold()` while a *separate* session commits twice
inside the block - and confirms the lock is free afterwards; the second confirms the mutual
exclusion `hold()` is supposed to provide (a second attempt while the first is still inside the
block is refused) *and* that exiting the block actually frees it for a third attempt, which is the
"a second reset is not wedged forever" property the whole fix is about.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.lib import singleflight
from tests.conftest import LIVE_DATABASE_URL

LOCK_NAME = "test:demo_reset_lock_bug_report"


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@pytest.fixture
def _clean_lock(require_live_db: None) -> None:
    """Best-effort cleanup in case a previous failed run of this file left the probe lock held -
    by closing a throwaway connection that held it, the same way a real worker restart would."""
    del require_live_db

    async def _release() -> None:
        engine = create_async_engine(LIVE_DATABASE_URL)
        try:
            async with engine.connect() as conn:
                session = AsyncSession(bind=conn)
                await singleflight.release(session, LOCK_NAME)
                await session.commit()
        finally:
            await engine.dispose()

    _run(_release())


def test_hold_keeps_the_lock_through_a_run_reset_shaped_sequence_of_commits(
    _clean_lock: None,
) -> None:
    """Regression for the fix, mirroring `run_reset`'s real shape: the lock is taken via
    `hold()` on the engine, and *inside* that block a separate `AsyncSession` (standing in for
    `run_reset`'s own `session` parameter) commits twice - exactly the pattern that orphaned the
    lock before the fix, because each commit is a chance for that session to resume on a
    different pooled connection than the one `hold()` owns. The lock must still be free the
    moment the block exits, because `hold()` never depended on that session's connection in the
    first place.
    """
    del _clean_lock

    async def scenario() -> tuple[bool, bool]:
        engine = create_async_engine(LIVE_DATABASE_URL, pool_size=5, max_overflow=0)
        try:
            async with (
                singleflight.hold(engine, LOCK_NAME) as acquired,
                engine.connect() as work_conn,
            ):
                work_session = AsyncSession(bind=work_conn)
                # Two commits in the middle of the held block - `repo.clear`'s commit and
                # `seed()`'s, in `run_reset`'s real sequence.
                await work_session.execute(text("select 1"))
                await work_session.commit()
                await work_session.execute(text("select 2"))
                await work_session.commit()

            async with engine.connect() as observer_conn:
                observer = AsyncSession(bind=observer_conn)
                still_held = await singleflight.is_held(observer, LOCK_NAME)
            return acquired, still_held
        finally:
            await engine.dispose()

    acquired, still_held = _run(scenario())
    assert acquired is True
    assert still_held is False, (
        "the lock is still held after a run_reset-shaped sequence of commits inside a hold() "
        "block - hold() is supposed to own one connection for the whole span regardless of what "
        "any other session inside the block does, which is the entire point of the fix."
    )


def test_a_concurrent_hold_is_refused_then_succeeds_once_the_first_exits(
    _clean_lock: None,
) -> None:
    """The property the fix exists to guarantee, stated directly: while one `hold()` block is
    open, a second attempt on the same name must be refused (no two resets running at once) -
    and once the first block exits cleanly, a third attempt must succeed (a finished reset does
    not wedge the next one forever, which is the failure mode this whole module is about).
    """
    del _clean_lock

    async def scenario() -> tuple[bool, bool, bool]:
        engine = create_async_engine(LIVE_DATABASE_URL, pool_size=5, max_overflow=0)
        try:
            async with (
                singleflight.hold(engine, LOCK_NAME) as first,
                singleflight.hold(engine, LOCK_NAME) as second,
            ):
                pass
            async with singleflight.hold(engine, LOCK_NAME) as third:
                pass
            return first, second, third
        finally:
            await engine.dispose()

    first, second, third = _run(scenario())
    assert first is True, "the first hold() should acquire the lock"
    assert second is False, "a second hold() while the first is still open must be refused"
    assert third is True, (
        "a hold() after the first has exited must succeed - the lock must not be wedged"
    )


def test_control_releasing_from_the_same_connection_that_acquired_it_works(
    _clean_lock: None,
) -> None:
    """Negative control: the ordinary, intended case - acquire and release on the same
    connection, with a commit in between (as `run_reset` does around `repo.clear`) - releases
    cleanly every time. This isolates the fix above to "the session resumed on the wrong
    connection", not to anything wrong with `singleflight` or session-scoped advisory locks in
    general.
    """
    del _clean_lock

    async def scenario() -> bool:
        engine = create_async_engine(LIVE_DATABASE_URL, pool_size=5, max_overflow=0)
        try:
            async with engine.connect() as conn:
                session = AsyncSession(bind=conn)
                await singleflight.try_acquire(session, LOCK_NAME)
                await session.commit()
                await singleflight.release(session, LOCK_NAME)
                await session.commit()

            async with engine.connect() as observer_conn:
                observer = AsyncSession(bind=observer_conn)
                return await singleflight.is_held(observer, LOCK_NAME)
        finally:
            await engine.dispose()

    still_held = _run(scenario())
    assert still_held is False
