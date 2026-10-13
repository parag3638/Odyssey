"""Adversarial concurrency pass on the spend ledger (mvp.md 6.2), beyond the N=20-vs-3
case and the sequential two-sweeper case already in `test_llm_pricing_and_spend.py`.

This tries to break the one property the whole design exists for: `cap - settled -
sum(held)` must never go negative, under every mix of caller the real system can throw
at it - a small race (N=2), a large one (N=50), reserve interleaved with settle and
release on *other* rows at the same time, two genuinely concurrent sweepers (not one
holding the lock by hand while the other calls in), and a reserve racing the sweeper.

The ledger's own comment explains why the naive `insert ... select ... where <check>`
design failed a first concurrency test (it did, mid-build, at N=20 against a cap of
0.30: 20 callers all read the same totals under READ COMMITTED and all passed). The
`select ... for update` row lock replaced it. These tests are an independent attempt to
break that fix with different shapes of the same race, not a re-run of the fix's own
test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from src.features.mapping import spend
from src.lib import db
from src.lib.errors import SpendCapReached
from tests.conftest import LIVE_DATABASE_URL

BASE_DAY = date(2001, 2, 2)  # a different day from test_llm_pricing_and_spend.py's
#: so the two files can run concurrently in a pytest-xdist split without colliding.


async def _clear(session: db.AsyncSession, day: date) -> None:
    await session.execute(text("delete from llm_spend_reservations where day = :day"), {"day": day})
    await session.execute(text("delete from llm_spend where day = :day"), {"day": day})
    await session.commit()


@pytest.fixture
def ledger_day(require_live_db: None) -> date:
    del require_live_db
    return BASE_DAY


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


# ----------------------------------------------------------------------------------------
# Varying concurrency: N=2 and N=50
# ----------------------------------------------------------------------------------------


def test_two_concurrent_reservations_against_room_for_one_grant_exactly_one(
    ledger_day: date,
) -> None:
    """The smallest possible race: if the lock does not actually serialise, this is the
    cheapest N at which a double-grant could slip through."""
    day = ledger_day
    cap = Decimal("0.10")
    each = Decimal("0.10")

    async def scenario() -> tuple[int, int, Decimal]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await _clear(session, day)

            async def attempt() -> bool:
                async with db.session() as session:
                    try:
                        await spend.reserve(session, amount_usd=each, cap_usd=cap, day=day)
                        return True
                    except SpendCapReached:
                        return False

            results = await asyncio.gather(attempt(), attempt())
            async with db.session() as session:
                current = await spend.budget(session, cap, day)
            return sum(results), len(results) - sum(results), current.held_usd
        finally:
            await db.dispose()

    granted, refused, held = _run(scenario())
    assert granted == 1
    assert refused == 1
    assert held == Decimal("0.100000")


def test_fifty_concurrent_reservations_against_room_for_seven_grant_exactly_seven(
    ledger_day: date,
) -> None:
    """A much larger N than the plan's own criterion (20 vs 3), with a concurrent
    *observer* sampling the budget throughout: every sample it sees must already be a
    committed, legal state - `available_usd` is never negative at any instant the
    observer could catch, not just in the final tally."""
    day = ledger_day
    cap = Decimal("0.70")
    each = Decimal("0.10")

    async def scenario() -> tuple[int, int, Decimal, list[Decimal]]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await _clear(session, day)

            stop = asyncio.Event()
            samples: list[Decimal] = []

            async def observe() -> None:
                async with db.session() as session:
                    while not stop.is_set():
                        current = await spend.budget(session, cap, day)
                        samples.append(current.available_usd)
                        await asyncio.sleep(0)

            async def attempt() -> bool:
                async with db.session() as session:
                    try:
                        await spend.reserve(session, amount_usd=each, cap_usd=cap, day=day)
                        return True
                    except SpendCapReached:
                        return False

            observer_task = asyncio.ensure_future(observe())
            results = await asyncio.gather(*(attempt() for _ in range(50)))
            stop.set()
            await observer_task

            async with db.session() as session:
                current = await spend.budget(session, cap, day)
            return sum(results), len(results) - sum(results), current.held_usd, samples
        finally:
            await db.dispose()

    granted, refused, held, samples = _run(scenario())
    assert granted == 7, f"{granted} granted; the budget fits exactly seven at 0.10 each"
    assert refused == 43
    assert held == Decimal("0.700000")
    assert held <= cap
    assert len(samples) > 0, "the observer must actually have run concurrently with the race"
    assert all(sample >= Decimal(0) for sample in samples), (
        f"the observer caught a negative available budget mid-race: {[s for s in samples if s < 0]}"
    )


# ----------------------------------------------------------------------------------------
# Reserve interleaved with settle and release on other rows, concurrently
# ----------------------------------------------------------------------------------------


def test_concurrent_settle_and_release_on_other_rows_never_lets_total_exceed_the_cap(
    ledger_day: date,
) -> None:
    """Ten reservations already held (0.90 of a 1.00 cap, 0.10 free). Resolve five by
    settling at a lower real cost and five by releasing, while *simultaneously* firing
    twenty new reservation attempts racing to grab whatever that frees up. No run of
    this must ever let `settled + held` exceed the cap, no matter how the resolutions
    and the new attempts interleave."""
    day = ledger_day
    cap = Decimal("1.00")
    held_each = Decimal("0.09")
    settle_cost = Decimal("0.05")  # less than held, so settling frees 0.04 per row
    new_each = Decimal("0.05")

    async def scenario() -> tuple[Decimal, Decimal, int]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await _clear(session, day)

            reservations: list[spend.Reservation] = []
            for _ in range(10):
                async with db.session() as session:
                    reservations.append(
                        await spend.reserve(session, amount_usd=held_each, cap_usd=cap, day=day)
                    )

            async def settle_one(reservation: spend.Reservation) -> None:
                async with db.session() as session:
                    await spend.settle(session, reservation, settle_cost)

            async def release_one(reservation: spend.Reservation) -> None:
                async with db.session() as session:
                    await spend.release(session, reservation)

            async def new_attempt() -> bool:
                async with db.session() as session:
                    try:
                        await spend.reserve(session, amount_usd=new_each, cap_usd=cap, day=day)
                        return True
                    except SpendCapReached:
                        return False

            resolvers = [settle_one(r) for r in reservations[:5]] + [
                release_one(r) for r in reservations[5:]
            ]
            newcomers = [new_attempt() for _ in range(20)]
            resolver_results, newcomer_results = await asyncio.gather(
                asyncio.gather(*resolvers), asyncio.gather(*newcomers)
            )
            del resolver_results

            async with db.session() as session:
                current = await spend.budget(session, cap, day)
            granted_new = sum(newcomer_results)
            return current.settled_usd, current.held_usd, granted_new
        finally:
            await db.dispose()

    settled, held, granted_new = _run(scenario())
    assert settled == Decimal("0.250000"), "exactly the five settled at 0.05 each"
    # Final held is whatever of the 20 newcomers got through, at 0.05 each.
    assert held == granted_new * new_each
    assert settled + held <= cap, "the invariant the whole ledger exists to protect"
    # Room freed: 0.10 pre-existing + 5*0.04 (settle gave back) + 5*0.09 (release gave
    # back fully) = 0.10 + 0.20 + 0.45 = 0.75 of headroom opens up over the race, enough
    # for at most 15 of the 20 newcomers at 0.05 each.
    assert 0 < granted_new <= 15


# ----------------------------------------------------------------------------------------
# Two *genuinely* concurrent sweepers (via asyncio.gather on the real function, not one
# session holding the advisory lock by hand while the other is invoked synchronously).
# ----------------------------------------------------------------------------------------


def test_two_real_concurrent_sweeps_release_every_stale_row_exactly_once(
    ledger_day: date,
) -> None:
    """Calls the real `spend.sweep_stale` from two sessions under `asyncio.gather`,
    rather than the existing suite's version (which proves lock exclusion
    deterministically by having one session hold the advisory lock *by hand* via a raw
    `pg_try_advisory_lock` while the other calls the real function in).

    Whether these two actually collide at the lock depends on OS/event-loop scheduling
    of two sub-millisecond local round trips - on this run the first finished its whole
    sweep-and-unlock sequence before the second ever sent its lock attempt, so it saw
    `[4, 0]` rather than `[4, -1]`. Both are safe outcomes. What must hold regardless of
    *how* they interleave is the safety property, not a specific interleaving: nothing
    is released twice, nothing is double-counted, and the lock mechanism never lets two
    sweeps actively run the UPDATE at once (which the existing sequential test already
    pins down deterministically for the contended case).
    """
    day = ledger_day

    async def scenario() -> tuple[list[int], dict[str, str]]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await _clear(session, day)
                for _ in range(4):
                    await spend.reserve(
                        session, amount_usd=Decimal("0.01"), cap_usd=Decimal("5.00"), day=day
                    )
                await session.execute(
                    text(
                        "update llm_spend_reservations set created_at = now() - "
                        "make_interval(secs => 100) where day = :day"
                    ),
                    {"day": day},
                )
                await session.commit()

            async def sweep() -> int:
                async with db.session() as session:
                    return await spend.sweep_stale(session, timeout_seconds=30)

            results = await asyncio.gather(sweep(), sweep())

            async with db.session() as session:
                statuses = {
                    str(row[0]): str(row[1])
                    for row in (
                        await session.execute(
                            text("select id, status from llm_spend_reservations where day = :day"),
                            {"day": day},
                        )
                    ).all()
                }
            return list(results), statuses
        finally:
            await db.dispose()

    results, statuses = _run(scenario())
    # Every result is either "I held the lock and released N" (>= 0) or "lock was
    # taken, did nothing" (-1). Never a third shape (no exception propagated), and the
    # four rows are released exactly once between the two calls - not zero, not eight.
    assert all(result >= -1 for result in results)
    assert sum(result for result in results if result > 0) == 4, (
        f"the four stale rows must be released exactly once in total across both calls: {results}"
    )
    assert all(status == "released" for status in statuses.values())
    assert len(statuses) == 4, "no row vanished and none was created twice"


def test_ten_concurrent_sweeps_over_four_stale_rows_never_double_release(
    ledger_day: date,
) -> None:
    """Widens the race with more contenders than the two-sweeper case, to make an
    accidental double-release more likely to surface if the lock were not real."""
    day = ledger_day

    async def scenario() -> tuple[list[int], int]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await _clear(session, day)
                for _ in range(4):
                    await spend.reserve(
                        session, amount_usd=Decimal("0.01"), cap_usd=Decimal("5.00"), day=day
                    )
                await session.execute(
                    text(
                        "update llm_spend_reservations set created_at = now() - "
                        "make_interval(secs => 100) where day = :day"
                    ),
                    {"day": day},
                )
                await session.commit()

            async def sweep() -> int:
                async with db.session() as session:
                    return await spend.sweep_stale(session, timeout_seconds=30)

            results = await asyncio.gather(*(sweep() for _ in range(10)))

            async with db.session() as session:
                released = (
                    await session.execute(
                        text(
                            "select count(*) from llm_spend_reservations "
                            "where day = :day and status = 'released'"
                        ),
                        {"day": day},
                    )
                ).scalar_one()
            return list(results), int(released)
        finally:
            await db.dispose()

    results, released = _run(scenario())
    assert sum(result for result in results if result > 0) == 4
    assert released == 4, "the table's own count must agree with the sweeps' own tally"


# ----------------------------------------------------------------------------------------
# A reserve racing the sweeper: the two use different lock primitives (a row lock vs an
# advisory lock) and must not deadlock or corrupt each other's work.
# ----------------------------------------------------------------------------------------


def test_fresh_reservations_and_concurrent_sweeps_do_not_deadlock_or_lose_budget(
    ledger_day: date,
) -> None:
    """20 brand-new reservation attempts and 5 concurrent sweeps, all at once, against a
    budget with no stale rows to sweep. The sweeps must find nothing (every reservation
    is fresh) and must not block or corrupt the concurrent reserves."""
    day = ledger_day
    cap = Decimal("5.00")
    each = Decimal("0.01")

    async def scenario() -> tuple[int, Decimal, list[int]]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await _clear(session, day)

            async def reserve_one() -> bool:
                async with db.session() as session:
                    try:
                        await spend.reserve(session, amount_usd=each, cap_usd=cap, day=day)
                        return True
                    except SpendCapReached:
                        return False

            async def sweep_once() -> int:
                async with db.session() as session:
                    return await spend.sweep_stale(session, timeout_seconds=30)

            reserves = [reserve_one() for _ in range(20)]
            sweeps = [sweep_once() for _ in range(5)]
            granted, swept = await asyncio.gather(
                asyncio.gather(*reserves), asyncio.gather(*sweeps)
            )

            async with db.session() as session:
                current = await spend.budget(session, cap, day)
            return sum(granted), current.held_usd, list(swept)
        finally:
            await db.dispose()

    granted, held, swept = _run(scenario())
    assert granted == 20, "nothing here is stale, so nothing should ever be swept away"
    assert held == Decimal("0.010000") * 20
    # Every sweep that actually got the lock must report 0 released (nothing stale
    # exists); the rest report -1 (lock taken). Never a positive count here.
    assert all(count in (0, -1) for count in swept), (
        f"a sweep released a fresh reservation out from under an in-flight call: {swept}"
    )
