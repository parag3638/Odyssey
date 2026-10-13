"""What a call cost, in Decimal, from the token counts the provider reported.

Prices are configuration, not a table in code, because the model id is configuration
(`docs/decisions.md`, decision 14). A hardcoded price list goes stale silently and the
first sign is a spend cap that never trips - so `config.py` refuses to start when a model
is set without them.

Cached input is priced separately and must not be conflated with fresh input: a prefix
cache hit is the difference between a demo that costs pennies and one that costs nothing
worth mentioning, and reporting it at the full rate would hide the saving the design is
built around.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

#: Prices are quoted per million tokens, which is how every provider publishes them.
TOKENS_PER_MILLION: Final = Decimal(1_000_000)

#: Costs are carried at six decimal places. A single call can cost well under a cent, so
#: rounding to cents would record most of this demo's calls as free.
COST_SCALE: Final = Decimal("0.000001")


@dataclass(frozen=True)
class TokenUsage:
    """What the provider said it used. `cached_input_tokens` is a *subset* of
    `input_tokens`, not an addition to it - that is how OpenAI reports it, and treating
    it as extra would double-count every cached call."""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class Prices:
    input_per_mtok: Decimal
    cached_input_per_mtok: Decimal
    output_per_mtok: Decimal


def cost_usd(usage: TokenUsage, prices: Prices) -> Decimal:
    """The cost of one call, quantized to `COST_SCALE`.

    Decimal throughout. There is no float in this function and no float may be passed
    into it: `Prices` comes from `pydantic-settings` as `Decimal`, and token counts are
    integers.
    """
    cached = max(0, min(usage.cached_input_tokens, usage.input_tokens))
    fresh = usage.input_tokens - cached

    total = (
        Decimal(fresh) * prices.input_per_mtok
        + Decimal(cached) * prices.cached_input_per_mtok
        + Decimal(usage.output_tokens) * prices.output_per_mtok
    ) / TOKENS_PER_MILLION

    # ROUND_HALF_EVEN by way of the default context, matching `lib/money.py`: the same
    # rounding everywhere means a total never disagrees with the sum of its parts.
    return total.quantize(COST_SCALE)


def prices_from_settings(
    input_per_mtok: Decimal | None,
    cached_input_per_mtok: Decimal | None,
    output_per_mtok: Decimal | None,
) -> Prices | None:
    """`None` when any price is unset - which means the LLM feature is off, not that
    calls are free. `config.py` already refuses to start with a model and no prices, so
    this only returns `None` when there is no model either."""
    if input_per_mtok is None or cached_input_per_mtok is None or output_per_mtok is None:
        return None
    return Prices(
        input_per_mtok=input_per_mtok,
        cached_input_per_mtok=cached_input_per_mtok,
        output_per_mtok=output_per_mtok,
    )
