"""Decimal money handling. No float ever touches a monetary value.

BACKEND.md §3 sends money as decimal strings; §5 stores it as `numeric`; §11 forbids
floats outright. The spec's core principle says the same thing in one line: "Use Decimal
for money, never float."

The guards here are the enforcement point. A float passed in raises rather than being
coerced, because `0.1 + 0.2` on an invoice line is a defect nobody notices until a
customer reconciles a rebate statement — which, for this project, is the one failure that
would discredit every number in the UI.

**Rounding is ROUND_HALF_EVEN** (banker's rounding) at every scale. Chosen over
ROUND_HALF_UP because half-up is biased upward: across ~50k invoice lines a consistent
upward nudge accumulates into a real variance that the explainer would then have to
attribute to a rounding rule rather than to a root cause. Half-even has no systematic
bias. The two differ only on exact halves, which is why both are tested.

Scales: amounts 2dp, unit prices 6dp. Unit prices carry more because a per-each price
derived from a case price (case ÷ pack size) is frequently not exact at 2dp, and rounding
it before multiplying back out would manufacture a variance.

**Errors.** Wrong Python type raises `TypeError`; malformed or non-finite values raise
`ValueError`. One documented exception: a magnitude needing more than `Decimal`'s default
28-digit context precision to quantize raises `decimal.InvalidOperation` from the stdlib.
No rebate line reaches that scale, so it is recorded here rather than wrapped - a caller
wanting total coverage should catch `(TypeError, ValueError, InvalidOperation)`.

**Do not pre-round a unit price before calling `extend`.** `extend` rounds once, at the
end, which is correct - but `quantize_unit_price(p)` then `extend(q, p)` reintroduces the
exact double-rounding error `extend` exists to avoid, and at realistic volumes the
divergence is dollars, not cents: measured at $0.50 on a single line of ~980k units.
Pass the unquantized price.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Final

#: Monetary amounts: cents.
AMOUNT_SCALE: Final = 2
#: Unit prices: enough to survive a case-to-each division without losing cents.
UNIT_PRICE_SCALE: Final = 6
#: Quantities: cases, eaches and catch weights. The scale `canonical_lines.qty`,
#: `canonical_lines.catch_weight_lbs` and `rebate_calcs.basis_qty` are all stored at.
QTY_SCALE: Final = 4

#: The one rounding mode. Stated here so no caller has to choose.
ROUNDING: Final = ROUND_HALF_EVEN

_AMOUNT_EXPONENT: Final = Decimal(1).scaleb(-AMOUNT_SCALE)
_UNIT_PRICE_EXPONENT: Final = Decimal(1).scaleb(-UNIT_PRICE_SCALE)
_QTY_EXPONENT: Final = Decimal(1).scaleb(-QTY_SCALE)

DEFAULT_CURRENCY: Final = "USD"


def _reject_float_value(value: object, name: str) -> None:
    """Floats are refused at the boundary, never coerced."""
    if isinstance(value, float):
        raise TypeError(
            f"{name} must not be a float; pass a decimal string or Decimal "
            f"(got {value!r}, which may already have lost precision)"
        )


def _require_finite(value: Decimal, name: str) -> Decimal:
    """Reject NaN and the infinities.

    `Decimal("NaN")` and `Decimal("Infinity")` are legal Decimal constructions and
    never legal money values. They are the same defect class as a float arriving here,
    reached through a different legal-but-wrong input: NaN propagates silently through
    arithmetic (`NaN + x == NaN`), so one corrupted CSV cell can poison an entire
    rebate total and then serialise back out as the string `"NaN"` in an API `amount`.

    Raises `ValueError`, not `TypeError`: the type is right, the value is not.
    """
    if not value.is_finite():
        raise ValueError(
            f"{name} must be a finite decimal, got {value!s}; NaN and Infinity are "
            "legal Decimal values but never legal money values"
        )
    return value


def _as_decimal(value: str | int | Decimal, name: str) -> Decimal:
    """Accept the exact types only. `int` is exact, so it is allowed; `float` is not."""
    _reject_float_value(value, name)
    if isinstance(value, Decimal):
        return _require_finite(value, name)
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            parsed = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{name} is not a decimal number: {value!r}") from exc
        return _require_finite(parsed, name)
    raise TypeError(f"{name} must be a decimal string, int or Decimal, got {type(value).__name__}")


def parse(value: str) -> Decimal:
    """Parse a decimal string from the edge of the system.

    Strings only, deliberately: this is the function that reads untrusted input (a CSV
    cell, a JSON field), and accepting a `Decimal` here would let a caller skip the one
    place where malformed input is caught.
    """
    _reject_float_value(value, "money value")
    if not isinstance(value, str):
        raise TypeError(f"money values must be decimal strings, got {type(value).__name__}")
    try:
        parsed = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError(f"not a decimal amount: {value!r}") from exc
    return _require_finite(parsed, "money value")


def serialise(value: Decimal) -> str:
    """Serialise for the API. Fixed-point, never scientific notation."""
    _reject_float_value(value, "money value")
    if not isinstance(value, Decimal):
        raise TypeError(f"expected Decimal, got {type(value).__name__}")
    _require_finite(value, "money value")
    return f"{value:f}"


def quantize_amount(value: str | int | Decimal) -> Decimal:
    """Round to 2dp, half-even. Use for anything that is a sum of money."""
    return _as_decimal(value, "amount").quantize(_AMOUNT_EXPONENT, rounding=ROUNDING)


def quantize_unit_price(value: str | int | Decimal) -> Decimal:
    """Round to 6dp, half-even. Use for per-unit prices, not totals."""
    return _as_decimal(value, "unit price").quantize(_UNIT_PRICE_EXPONENT, rounding=ROUNDING)


def quantize_qty(value: str | int | Decimal) -> Decimal:
    """Round to 4dp, half-even. Use for a quantity being *stored*, never before multiplying.

    A rebate basis is a quantity: cases, or pounds. `qty / pack_size` can be non-terminating
    (10 eaches of a 3-pack), so the figure written to `numeric(18,4)` has to be rounded somewhere -
    but rounding it *before* multiplying by the rate is the mistake `extend` exists to forbid, and
    here it would move the rebate. Compute the money from the unquantised basis, then quantise the
    basis separately for the record.
    """
    return _as_decimal(value, "quantity").quantize(_QTY_EXPONENT, rounding=ROUNDING)


def extend(qty: str | int | Decimal, unit_price: str | int | Decimal) -> Decimal:
    """Line extension: `qty x unit_price`, rounded once at the end, to 2dp.

    Rounded **once**. Rounding the unit price first and then multiplying is the classic
    way to produce an extension that disagrees with the customer's own total by cents per
    line — which is exactly the `ext_amount != qty x unit_price` readiness check in the
    spec, and we should not be the ones generating it.
    """
    q = _as_decimal(qty, "qty")
    p = _as_decimal(unit_price, "unit price")
    return (q * p).quantize(_AMOUNT_EXPONENT, rounding=ROUNDING)


@dataclass(frozen=True)
class Money:
    """An amount with its currency, in the shape BACKEND.md §3 puts on the wire."""

    amount: Decimal
    currency: str = DEFAULT_CURRENCY

    def __post_init__(self) -> None:
        _reject_float_value(self.amount, "Money.amount")
        if not isinstance(self.amount, Decimal):
            raise TypeError(f"Money.amount must be Decimal, got {type(self.amount).__name__}")
        _require_finite(self.amount, "Money.amount")
        if not self.currency:
            raise ValueError("Money.currency must not be empty")

    @classmethod
    def parse(cls, amount: str, currency: str = DEFAULT_CURRENCY) -> Money:
        return cls(amount=quantize_amount(parse(amount)), currency=currency)

    def to_api(self) -> dict[str, str]:
        """`{"amount": "1234.50", "currency": "USD"}` - §3's money shape, verbatim."""
        return {"amount": serialise(quantize_amount(self.amount)), "currency": self.currency}

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"refusing to combine {self.currency} with {other.currency}; "
                "convert explicitly rather than silently"
            )
