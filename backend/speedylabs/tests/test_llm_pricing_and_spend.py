"""mvp.md 6.1-6.2 - costing a call, and the reserve/settle/release/sweep ledger.

The ledger tests run against the real database because the property under test is what
Postgres does when two transactions overlap. A mock would assert that my own arithmetic
agrees with itself.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from src.features.mapping import spend
from src.features.mapping.pricing import Prices, TokenUsage, cost_usd, prices_from_settings
from src.lib import db
from src.lib.errors import SpendCapReached
from tests.conftest import LIVE_DATABASE_URL

# Deliberately distinct rates, so a test cannot pass by conflating two of them.
PRICES = Prices(
    input_per_mtok=Decimal("2.50"),
    cached_input_per_mtok=Decimal("0.25"),
    output_per_mtok=Decimal("10.00"),
)


# --------------------------------------------------------------------------------------
# 6.1 - pricing
# --------------------------------------------------------------------------------------


def test_a_call_costs_the_hand_calculated_decimal() -> None:
    """12,000 input at 2.50/Mtok = 0.03; 800 output at 10.00/Mtok = 0.008. Total 0.038."""
    usage = TokenUsage(input_tokens=12_000, output_tokens=800)
    assert cost_usd(usage, PRICES) == Decimal("0.038000")


def test_cached_input_is_priced_at_the_cached_rate_and_not_conflated() -> None:
    """9,000 of 12,000 input cached: 3,000 x 2.50 + 9,000 x 0.25 = 0.0075 + 0.00225.
    Plus 800 output x 10.00 = 0.008. Total 0.01775.

    With the rates conflated this would read 0.038 (all at full) or 0.011 (all at
    cached), so the three distinct prices are what makes this assertion mean something.
    """
    usage = TokenUsage(input_tokens=12_000, output_tokens=800, cached_input_tokens=9_000)
    assert cost_usd(usage, PRICES) == Decimal("0.017750")


def test_cached_tokens_are_a_subset_of_input_not_an_addition() -> None:
    """A cached count larger than the input count is clamped rather than charged twice."""
    usage = TokenUsage(input_tokens=1_000, output_tokens=0, cached_input_tokens=5_000)
    assert cost_usd(usage, PRICES) == Decimal(1_000) * PRICES.cached_input_per_mtok / 1_000_000


def test_no_float_appears_in_a_cost() -> None:
    cost = cost_usd(TokenUsage(input_tokens=12_345, output_tokens=678), PRICES)
    assert isinstance(cost, Decimal)
    assert not isinstance(cost, float)


def test_a_zero_usage_call_costs_nothing() -> None:
    assert cost_usd(TokenUsage(input_tokens=0, output_tokens=0), PRICES) == Decimal("0.000000")


def test_prices_are_none_when_any_rate_is_unset() -> None:
    """A missing price means the feature is off, not that calls are free."""
    assert prices_from_settings(Decimal("1"), Decimal("1"), None) is None
    assert prices_from_settings(None, Decimal("1"), Decimal("1")) is None
    assert prices_from_settings(Decimal("1"), Decimal("1"), Decimal("1")) is not None


# --------------------------------------------------------------------------------------
# 6.2 - the estimator over-counts, on purpose
# --------------------------------------------------------------------------------------


def test_the_estimator_over_counts_against_a_realistic_tokenisation() -> None:
    """`chars / 3` must be at least what a real tokeniser reports. English plus
    identifiers runs nearer `chars / 4`, so this is the safe direction."""
    for prompt_chars in (500, 5_000, 50_000):
        realistic = prompt_chars / 4
        assert spend.estimate_tokens(prompt_chars, 0) >= realistic


def test_the_estimate_includes_the_whole_output_allowance() -> None:
    """The output has not happened yet, so the only safe assumption is all of it."""
    assert spend.estimate_tokens(0, 4_000) == 4_000
    assert spend.estimate_tokens(300, 4_000) == 100 + 4_000


def test_the_largest_prompt_the_system_can_build_is_estimated_above_its_real_size() -> None:
    """200 columns at 64-character samples is the ceiling the prompt builder enforces.
    The estimate for it must exceed a realistic count for the same text."""
    # 200 columns x (5 samples x 64 chars + name, type, pattern overhead) ~ 80k chars.
    prompt_chars = 200 * (5 * 64 + 120)
    estimated = spend.estimate_tokens(prompt_chars, 4_000)
    realistic = prompt_chars / 4 + 4_000
    assert estimated >= realistic
    assert estimated >= prompt_chars / 3


def test_seconds_until_utc_midnight_is_never_zero() -> None:
    """A `Retry-After: 0` tells a client to retry straight back into the same refusal."""
    one_second_before = datetime(2026, 10, 6, 23, 59, 59, tzinfo=UTC)
    assert spend.seconds_until_utc_midnight(one_second_before) == 1
    assert spend.seconds_until_utc_midnight(datetime(2026, 10, 6, 23, 0, tzinfo=UTC)) == 3600
    assert spend.seconds_until_utc_midnight(datetime(2026, 10, 6, 0, 0, tzinfo=UTC)) == 86_400


# --------------------------------------------------------------------------------------
# 6.2 - the ledger, against the real database
# --------------------------------------------------------------------------------------

CAP = Decimal("5.00")
#: A day of its own per test, far in the past, so these never collide with real spend or
#: with each other.
BASE_DAY = date(2001, 1, 1)


async def _clear(session: db.AsyncSession, day: date) -> None:
    await session.execute(text("delete from llm_spend_reservations where day = :day"), {"day": day})
    await session.execute(text("delete from llm_spend where day = :day"), {"day": day})
    await session.commit()


@pytest.fixture
def ledger_day(require_live_db: None) -> date:
    del require_live_db
    return BASE_DAY


def test_a_reservation_is_granted_and_shows_in_available_budget(ledger_day: date) -> None:
    day = ledger_day

    async def scenario() -> tuple[Decimal, Decimal, Decimal]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await _clear(session, day)
                await session.execute(
                    text(
                        "insert into llm_spend (day, settled_usd, call_count, updated_at) "
                        "values (:day, 1.00, 1, now())"
                    ),
                    {"day": day},
                )
                await session.commit()

                await spend.reserve(session, amount_usd=Decimal("0.02"), cap_usd=CAP, day=day)
                current = await spend.budget(session, CAP, day)
                return current.settled_usd, current.held_usd, current.available_usd
        finally:
            await db.dispose()

    settled, held, available = asyncio.run(scenario())
    assert settled == Decimal("1.000000")
    assert held == Decimal("0.020000")
    assert available == Decimal("3.980000"), "5.00 - 1.00 settled - 0.02 held"


def test_settling_charges_the_real_cost_not_the_reservation(ledger_day: date) -> None:
    day = ledger_day

    async def scenario() -> tuple[Decimal, Decimal, str]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await _clear(session, day)
                reservation = await spend.reserve(
                    session, amount_usd=Decimal("0.02"), cap_usd=CAP, day=day
                )
                await spend.settle(session, reservation, Decimal("0.013"))
                current = await spend.budget(session, CAP, day)
                status = (
                    await session.execute(
                        text("select status from llm_spend_reservations where id = :id"),
                        {"id": reservation.id},
                    )
                ).scalar_one()
                return current.settled_usd, current.held_usd, str(status)
        finally:
            await db.dispose()

    settled, held, status = asyncio.run(scenario())
    assert settled == Decimal("0.013000"), "the real cost, not the 0.02 reserved"
    assert held == Decimal("0")
    assert status == "settled"


def test_releasing_restores_the_budget_completely(ledger_day: date) -> None:
    """A call that failed must not burn budget it never spent."""
    day = ledger_day

    async def scenario() -> tuple[Decimal, Decimal, str]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await _clear(session, day)
                reservation = await spend.reserve(
                    session, amount_usd=Decimal("0.02"), cap_usd=CAP, day=day
                )
                await spend.release(session, reservation)
                current = await spend.budget(session, CAP, day)
                status = (
                    await session.execute(
                        text("select status from llm_spend_reservations where id = :id"),
                        {"id": reservation.id},
                    )
                ).scalar_one()
                return current.settled_usd, current.available_usd, str(status)
        finally:
            await db.dispose()

    settled, available, status = asyncio.run(scenario())
    assert settled == Decimal("0")
    assert available == CAP
    assert status == "released"


def test_a_reservation_past_the_cap_is_refused_and_names_when_to_retry(
    ledger_day: date,
) -> None:
    day = ledger_day

    async def scenario() -> tuple[bool, str | None]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await _clear(session, day)
                await session.execute(
                    text(
                        "insert into llm_spend (day, settled_usd, call_count, updated_at) "
                        "values (:day, :settled, 1, now())"
                    ),
                    {"day": day, "settled": CAP},
                )
                await session.commit()
                try:
                    await spend.reserve(session, amount_usd=Decimal("0.01"), cap_usd=CAP, day=day)
                except SpendCapReached as error:
                    return True, error.headers.get("Retry-After")
                return False, None
        finally:
            await db.dispose()

    refused, retry_after = asyncio.run(scenario())
    assert refused is True
    assert retry_after is not None and int(retry_after) > 0


def test_twenty_concurrent_reservations_against_room_for_three_grant_exactly_three(
    ledger_day: date,
) -> None:
    """The property the whole table exists for. A counter cannot do this: twenty callers
    would all read the same total and all proceed."""
    day = ledger_day
    cap = Decimal("0.30")
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

            results = await asyncio.gather(*(attempt() for _ in range(20)))
            async with db.session() as session:
                current = await spend.budget(session, cap, day)
            return sum(results), len(results) - sum(results), current.held_usd
        finally:
            await db.dispose()

    granted, refused, held = asyncio.run(scenario())
    assert granted == 3, f"{granted} granted; the budget fits exactly three"
    assert refused == 17
    assert held == Decimal("0.300000")
    assert held <= cap, "the cap was never exceeded at any point"


def test_the_sweeper_releases_an_abandoned_hold_and_leaves_a_fresh_one(
    ledger_day: date,
) -> None:
    """One killed worker must not hold budget until UTC midnight - and a call still in
    flight must not have its budget pulled out from under it."""
    day = ledger_day

    async def scenario() -> tuple[int, str, str]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await _clear(session, day)
                stale = await spend.reserve(
                    session, amount_usd=Decimal("0.02"), cap_usd=CAP, day=day
                )
                fresh = await spend.reserve(
                    session, amount_usd=Decimal("0.02"), cap_usd=CAP, day=day
                )
                # Age one of them past any plausible call.
                await session.execute(
                    text(
                        "update llm_spend_reservations set created_at = now() - "
                        "make_interval(secs => :age) where id = :id"
                    ),
                    {"age": 30 + spend.SWEEP_GRACE_SECONDS + 10, "id": stale.id},
                )
                await session.commit()

                released = await spend.sweep_stale(session, timeout_seconds=30)
                statuses = {
                    str(row[0]): str(row[1])
                    for row in (
                        await session.execute(
                            text("select id, status from llm_spend_reservations where day = :day"),
                            {"day": day},
                        )
                    ).all()
                }
                return released, statuses[str(stale.id)], statuses[str(fresh.id)]
        finally:
            await db.dispose()

    released, stale_status, fresh_status = asyncio.run(scenario())
    assert released == 1
    assert stale_status == "released"
    assert fresh_status == "held", "a call still in flight keeps its budget"


def test_a_second_sweeper_finds_the_lock_taken_and_does_nothing(ledger_day: date) -> None:
    """Two instances must not release the same reservation twice (BACKEND.md §7)."""
    day = ledger_day

    async def scenario() -> list[int]:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as first, db.session() as second:
                await _clear(first, day)
                # Hold the lock on the first session by starting a sweep that finds
                # nothing, then ask the second to sweep while the first still holds it.
                acquired = (
                    await first.execute(
                        text("select pg_try_advisory_lock(:key)"),
                        {"key": spend.SWEEP_LOCK_KEY},
                    )
                ).scalar_one()
                assert acquired is True
                try:
                    return [await spend.sweep_stale(second, timeout_seconds=30)]
                finally:
                    await first.execute(
                        text("select pg_advisory_unlock(:key)"),
                        {"key": spend.SWEEP_LOCK_KEY},
                    )
                    await first.commit()
        finally:
            await db.dispose()

    outcomes = asyncio.run(scenario())
    assert outcomes == [-1], "-1 means 'another sweeper held the lock', not 'nothing to do'"


def test_the_spend_block_is_decimal_strings() -> None:
    """§3: the backend sends raw values; the frontend never computes money."""
    current = spend.Budget(
        cap_usd=Decimal("5.00"), settled_usd=Decimal("0.41"), held_usd=Decimal("0.02")
    )
    block = spend.spend_block(current, Decimal("0.0123"))
    assert block == {
        "callCostUsd": "0.0123",
        "todayUsd": "0.41",
        "heldUsd": "0.02",
        "capUsd": "5.00",
    }
    assert all(isinstance(value, str) for value in block.values())
