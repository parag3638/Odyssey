"""The spend ledger: reserve, settle, release, sweep.

The problem this solves, stated plainly: the cost of a model call is unknown until it
returns, and the budget has to hold when several calls start at once. A counter cannot do
that - N callers read the same total and all proceed. So:

1. **Reserve** a pessimistic estimate, with the check and the write serialised behind a
   row lock on the day's `llm_spend` row, so two concurrent callers cannot both see room
   for the last dollar.
2. **Call.**
3. **Settle** the real cost and resolve the reservation in one transaction. The
   over-estimate is given back, so pessimism costs a moment of headroom and nothing else.
4. **Release** on any failure path. A call that failed must not burn budget it never spent.
5. **Sweep** reservations a killed worker left `held`, or one dead process holds budget
   until UTC midnight.

Available budget is `cap - settled_usd - sum(reserved_usd where status = 'held')`.

Why the estimate over-counts on purpose
---------------------------------------
`ceil(prompt_chars / 3) + max_output_tokens`. English plus identifiers tokenises nearer
`chars / 4`, so dividing by 3 reserves more than the call can use. That direction is the
safe one: a reservation that is wrong high briefly withholds headroom, while one that is
wrong low lets a call exceed the cap. `tiktoken` was rejected for this job because its
model-to-encoding lookup raises on an id it does not know, and the whole point here is
that the model id is new - a cap that breaks when the model is new is worse than no cap,
because it looks like it works.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.features.mapping.pricing import COST_SCALE, Prices, TokenUsage, cost_usd
from src.lib import logger
from src.lib.errors import SpendCapReached
from src.lib.ids import uuid7
from src.lib.timeutil import today_utc

log = logger.get_logger(__name__)

#: Characters per token for the estimate. Deliberately low - see the module docstring.
CHARS_PER_TOKEN_PESSIMISTIC: Final = 3

#: How long after a call's own timeout a `held` reservation is considered abandoned.
SWEEP_GRACE_SECONDS: Final = 60

#: The advisory-lock key the sweeper takes, so two instances never sweep at once
#: (BACKEND.md §7: "scheduled tasks take a lock"). An arbitrary constant, but a *fixed*
#: one - the lock only works if every sweeper asks for the same number.
SWEEP_LOCK_KEY: Final = 0x5D_EE_D1


@dataclass(frozen=True)
class Budget:
    cap_usd: Decimal
    settled_usd: Decimal
    held_usd: Decimal

    @property
    def available_usd(self) -> Decimal:
        return self.cap_usd - self.settled_usd - self.held_usd


@dataclass(frozen=True)
class Reservation:
    id: UUID
    day: date
    reserved_usd: Decimal


def estimate_tokens(prompt_chars: int, max_output_tokens: int) -> int:
    """Pessimistic token count for a prompt we wrote and whose size we already bound."""
    return math.ceil(prompt_chars / CHARS_PER_TOKEN_PESSIMISTIC) + max_output_tokens


def estimate_cost_usd(prompt_chars: int, max_output_tokens: int, prices: Prices) -> Decimal:
    """What to reserve. Prices every estimated token at the *uncached* input rate and the
    whole output allowance at the output rate - both over-estimates, which is the point.

    Floored at one micro-dollar, because a reservation of exactly zero is not a
    reservation: the table's `reserved_usd > 0` check rejects it, and a hold of nothing
    would hold nothing back from a concurrent caller. Zero is reachable in practice - all
    three prices configured at `0.00`, which is how `.env.example` ships and is also what a
    genuinely free self-hosted model would look like - and it crashed the propose route
    with a constraint violation until this floor existed.
    """
    estimated_input = math.ceil(prompt_chars / CHARS_PER_TOKEN_PESSIMISTIC)
    estimate = cost_usd(
        TokenUsage(input_tokens=estimated_input, output_tokens=max_output_tokens),
        prices,
    )
    return max(estimate, COST_SCALE)


_SELECT_BUDGET = text(
    """
    select
        coalesce((select settled_usd from llm_spend where day = :day), 0) as settled_usd,
        coalesce((
            select sum(reserved_usd) from llm_spend_reservations
            where day = :day and status = 'held'
        ), 0) as held_usd
    """
)

# Reserving is three statements in one transaction, and the middle one is what makes it
# correct.
#
# The first version was a single `insert ... select ... where <budget check>`, on the
# belief that the `where` clause and the insert shared a snapshot and so could not both
# succeed. **That was wrong, and a concurrency test caught it:** under READ COMMITTED a
# transaction cannot see another's *uncommitted* `held` rows, so twenty callers all read
# the same totals, all passed the check, and the budget went to -0.50 against a cap of
# 0.30. A check that is invisible to its competitors is not a check.
#
# What works is an explicit mutex, and the natural one already exists: the day's row in
# `llm_spend`. Lock it, count the holds, insert if there is room, commit. The second
# caller blocks on the lock until the first commits and then sees its hold.
_ENSURE_DAY = text(
    """
    insert into llm_spend (day, settled_usd, call_count, updated_at)
    values (:day, 0, 0, now())
    on conflict (day) do nothing
    """
)

#: Takes the row lock for the rest of the transaction. Every reserver for this day queues
#: here, which is exactly the serialisation the budget needs.
_LOCK_DAY = text("select settled_usd from llm_spend where day = :day for update")

_HELD_SUM = text(
    """
    select coalesce(sum(reserved_usd), 0) as held_usd
    from llm_spend_reservations
    where day = :day and status = 'held'
    """
)

_INSERT_RESERVATION = text(
    """
    insert into llm_spend_reservations (id, day, reserved_usd, status, idempotency_key,
                                       source_file_id, created_at)
    values (:id, :day, :amount, 'held', :idempotency_key, :source_file_id, now())
    returning id, day, reserved_usd
    """
)

_SETTLE_SPEND = text(
    """
    insert into llm_spend (day, settled_usd, call_count, updated_at)
    values (:day, :cost, 1, now())
    on conflict (day) do update
        set settled_usd = llm_spend.settled_usd + :cost,
            call_count = llm_spend.call_count + 1,
            updated_at = now()
    """
)

_RESOLVE_RESERVATION = text(
    """
    update llm_spend_reservations
    set status = :status, resolved_at = now()
    where id = :id and status = 'held'
    returning id
    """
)

# `for update skip locked` so two sweepers never fight over the same row, and the grace
# period is a parameter rather than a literal so the test can prove both directions.
_SWEEP = text(
    """
    update llm_spend_reservations
    set status = 'released', resolved_at = now()
    where id in (
        select id from llm_spend_reservations
        where status = 'held'
          and created_at < now() - make_interval(secs => :grace_seconds)
        for update skip locked
    )
    returning id
    """
)

_TRY_ADVISORY_LOCK = text("select pg_try_advisory_lock(:key) as acquired")
_UNLOCK = text("select pg_advisory_unlock(:key)")


async def budget(session: AsyncSession, cap_usd: Decimal, day: date | None = None) -> Budget:
    row = (await session.execute(_SELECT_BUDGET, {"day": day or today_utc()})).mappings().one()
    return Budget(
        cap_usd=cap_usd,
        settled_usd=Decimal(row["settled_usd"]),
        held_usd=Decimal(row["held_usd"]),
    )


async def reserve(
    session: AsyncSession,
    *,
    amount_usd: Decimal,
    cap_usd: Decimal,
    source_file_id: UUID | None = None,
    idempotency_key: str | None = None,
    day: date | None = None,
) -> Reservation:
    """Hold `amount_usd` against today's budget, or raise `SpendCapReached`.

    Raises rather than returning `None` because there is exactly one thing a caller can
    do about it, and the route needs the `Retry-After` the error carries.
    """
    ledger_day = day or today_utc()

    await session.execute(_ENSURE_DAY, {"day": ledger_day})
    # The lock. Held until this transaction commits, so the count and the insert below
    # cannot interleave with another reserver's.
    settled = Decimal((await session.execute(_LOCK_DAY, {"day": ledger_day})).scalar_one())
    held = Decimal(
        (await session.execute(_HELD_SUM, {"day": ledger_day})).mappings().one()["held_usd"]
    )
    available = cap_usd - settled - held

    if amount_usd > available:
        await session.rollback()
        log.warning(
            "llm_spend_cap_reached",
            day=str(ledger_day),
            requested_usd=str(amount_usd),
            available_usd=str(available),
            cap_usd=str(cap_usd),
        )
        raise SpendCapReached(
            "Today's budget for mapping proposals is used up. Map the columns by hand, "
            "or try again after midnight UTC.",
            headers={"Retry-After": str(seconds_until_utc_midnight())},
        )

    row = (
        (
            await session.execute(
                _INSERT_RESERVATION,
                {
                    "id": uuid7(),
                    "day": ledger_day,
                    "amount": amount_usd,
                    "idempotency_key": idempotency_key,
                    "source_file_id": source_file_id,
                },
            )
        )
        .mappings()
        .one()
    )
    await session.commit()
    return Reservation(
        id=UUID(str(row["id"])), day=row["day"], reserved_usd=Decimal(row["reserved_usd"])
    )


async def settle(session: AsyncSession, reservation: Reservation, actual_cost_usd: Decimal) -> None:
    """Record the real cost and resolve the hold, in one transaction.

    `settled_usd` increases by the *actual* cost, never by the reserved amount - the
    difference is what pessimism gives back.
    """
    await session.execute(_SETTLE_SPEND, {"day": reservation.day, "cost": actual_cost_usd})
    await session.execute(_RESOLVE_RESERVATION, {"id": reservation.id, "status": "settled"})
    await session.commit()
    log.info(
        "llm_spend_settled",
        day=str(reservation.day),
        reserved_usd=str(reservation.reserved_usd),
        actual_usd=str(actual_cost_usd),
    )


async def release(session: AsyncSession, reservation: Reservation) -> None:
    """Give the hold back untouched. For any path where no cost was incurred."""
    await session.execute(_RESOLVE_RESERVATION, {"id": reservation.id, "status": "released"})
    await session.commit()
    log.info(
        "llm_spend_released",
        day=str(reservation.day),
        reserved_usd=str(reservation.reserved_usd),
    )


def seconds_until_utc_midnight(now: datetime | None = None) -> int:
    """For `Retry-After` on a spend-cap refusal: when the budget actually resets.

    At least one second, because a `Retry-After: 0` tells a client to retry immediately
    into the same refusal.
    """
    moment = now or datetime.now(UTC)
    midnight = datetime.combine(moment.date(), datetime.min.time(), tzinfo=UTC)
    return max(1, int((midnight + timedelta(days=1) - moment).total_seconds()))


async def sweep_stale(
    session: AsyncSession, *, timeout_seconds: int, grace_seconds: int = SWEEP_GRACE_SECONDS
) -> int:
    """Release `held` reservations older than a call could possibly still be running.

    Takes an advisory lock, so two instances cannot release the same row twice. Returns
    the number released, and `-1` when another sweeper held the lock - a distinct answer
    from "nothing to sweep", because the two mean different things to a log reader.
    """
    acquired = (
        (await session.execute(_TRY_ADVISORY_LOCK, {"key": SWEEP_LOCK_KEY}))
        .mappings()
        .one()["acquired"]
    )
    if not acquired:
        log.info("llm_spend_sweep_skipped_locked")
        return -1

    try:
        rows = await session.execute(_SWEEP, {"grace_seconds": timeout_seconds + grace_seconds})
        released = len(list(rows))
        await session.commit()
        if released:
            log.warning("llm_spend_swept", released=released)
        return released
    finally:
        await session.execute(_UNLOCK, {"key": SWEEP_LOCK_KEY})
        await session.commit()


def spend_block(current: Budget, call_cost_usd: Decimal) -> dict[str, Any]:
    """The `spend` object in a successful response. Decimal strings, computed here and
    never in the frontend (BACKEND.md §3: the backend sends raw values)."""
    return {
        "callCostUsd": f"{call_cost_usd:f}",
        "todayUsd": f"{current.settled_usd:f}",
        "heldUsd": f"{current.held_usd:f}",
        "capUsd": f"{current.cap_usd:f}",
    }
