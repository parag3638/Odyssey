"""mvp.md 6.2: the reservation-amount floor at one micro-dollar.

`spend.estimate_cost_usd`'s docstring says plainly that an estimate of exactly zero
crashed the propose route with a `reserved_usd > 0` constraint violation before the
floor existed, and that zero prices are not a hypothetical: `.env.example` ships
`OPENAI_PRICE_*_PER_MTOK=0.00`, which is this project's *actual current configuration*
(no `OPENAI_API_KEY`, degraded by design per `docs/progress.md`). So this is not an
edge case, it is the configuration this app runs under right now, and that is exactly
why it earns its own adversarial test rather than trusting the docstring's word for it.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.features.mapping import spend
from src.features.mapping.pricing import Prices
from src.lib import db
from tests.conftest import LIVE_DATABASE_URL

ZERO_PRICES = Prices(
    input_per_mtok=Decimal("0.00"),
    cached_input_per_mtok=Decimal("0.00"),
    output_per_mtok=Decimal("0.00"),
)
BASE_DAY = date(2001, 3, 3)


@pytest.fixture
def ledger_day(require_live_db: None) -> date:
    del require_live_db
    return BASE_DAY


def test_an_all_zero_priced_estimate_is_floored_above_zero() -> None:
    """The literal case `.env.example` configures: every rate is 0.00."""
    estimate = spend.estimate_cost_usd(
        prompt_chars=50_000, max_output_tokens=4_000, prices=ZERO_PRICES
    )
    assert estimate > Decimal(0)
    assert estimate == Decimal("0.000001"), (
        "the floor is exactly one micro-dollar, not some other epsilon"
    )


def test_the_floor_applies_even_to_the_smallest_possible_prompt() -> None:
    estimate = spend.estimate_cost_usd(prompt_chars=0, max_output_tokens=0, prices=ZERO_PRICES)
    assert estimate == Decimal("0.000001")


def test_a_nonzero_cheap_prompt_is_never_floored_below_its_own_real_cost() -> None:
    """The floor must be a *lower bound*, not a value that overrides a real nonzero
    estimate - it only has to matter when the real estimate would be exactly zero."""
    cheap_but_real = Prices(
        input_per_mtok=Decimal("0.01"),
        cached_input_per_mtok=Decimal("0.01"),
        output_per_mtok=Decimal("0.01"),
    )
    estimate = spend.estimate_cost_usd(
        prompt_chars=3_000_000, max_output_tokens=0, prices=cheap_but_real
    )
    # 1,000,000 estimated tokens at 0.01/Mtok = 0.01 - comfortably above the floor, so
    # this proves max() picked the real value, not the floor.
    assert estimate == Decimal("0.010000")
    assert estimate > Decimal("0.000001")


def test_reserving_a_literal_zero_amount_is_rejected_by_the_database_not_silently_accepted(
    ledger_day: date,
) -> None:
    """Demonstrates *why* the floor exists: without it, `spend.reserve` would try to
    insert `reserved_usd = 0`, and the table's own `check (reserved_usd > 0)` constraint
    (BACKEND.md §5: constraints live in the database, not only in code) refuses it. A
    reservation of nothing is not a reservation - it would hold no budget back from a
    concurrent caller while still occupying a row, which is worse than not reserving at
    all. This is the failure mode `estimate_cost_usd`'s floor is the fix for.
    """
    day = ledger_day

    async def scenario() -> type[Exception] | None:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await session.execute(
                    text("delete from llm_spend_reservations where day = :day"), {"day": day}
                )
                await session.execute(text("delete from llm_spend where day = :day"), {"day": day})
                await session.commit()
                try:
                    await spend.reserve(
                        session, amount_usd=Decimal("0"), cap_usd=Decimal("5.00"), day=day
                    )
                except (IntegrityError, DBAPIError) as error:
                    await session.rollback()
                    return type(error)
                return None
        finally:
            await db.dispose()

    error_type = asyncio.run(scenario())
    assert error_type is not None, (
        "a zero-amount reservation was accepted by the database - the `reserved_usd > 0` "
        "check constraint this test exists to prove is either missing or not enforced"
    )


def test_an_actual_reservation_at_the_floored_estimate_succeeds_end_to_end(
    ledger_day: date,
) -> None:
    """The realistic end-to-end path with the project's actual current prices: estimate
    under zero-priced config, then reserve that exact estimate - must not raise."""
    day = ledger_day

    async def scenario() -> Decimal:
        db.init(LIVE_DATABASE_URL)
        try:
            async with db.session() as session:
                await session.execute(
                    text("delete from llm_spend_reservations where day = :day"), {"day": day}
                )
                await session.execute(text("delete from llm_spend where day = :day"), {"day": day})
                await session.commit()

                estimate = spend.estimate_cost_usd(
                    prompt_chars=12_000, max_output_tokens=4_000, prices=ZERO_PRICES
                )
                reservation = await spend.reserve(
                    session, amount_usd=estimate, cap_usd=Decimal("5.00"), day=day
                )
                await spend.settle(session, reservation, Decimal("0"))
                current = await spend.budget(session, Decimal("5.00"), day)
                return current.settled_usd
        finally:
            await db.dispose()

    settled = asyncio.run(scenario())
    assert settled == Decimal("0"), (
        "a genuinely free call settles at exactly zero, even though it was reserved above zero"
    )
