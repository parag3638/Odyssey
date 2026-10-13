"""Decimal money handling (BACKEND.md §3, §5, §11): no float may ever touch an amount,
and the API boundary (`parse`) must refuse one rather than quietly coercing it.

Step 0.5 rewrote this module: `quantize_amount`/`quantize_unit_price` (2dp/6dp,
ROUND_HALF_EVEN), `extend` (qty x unit_price, rounded once), and a frozen `Money`
dataclass. Half-even is the one rounding mode stated in the module docstring, so
every test that checks a rounding result names it explicitly.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import pytest

from src.lib import money

# --- parse / serialise (unchanged from 0.1, re-verified against the rewrite) ---------


@pytest.mark.parametrize("bad", [1.5, 0.1, -3.0, 0.0])
def test_parse_refuses_a_float(bad: float) -> None:
    with pytest.raises(TypeError):
        money.parse(bad)  # type: ignore[arg-type]


def test_parse_refuses_an_already_parsed_decimal() -> None:
    """`parse` is the wire boundary: it takes the JSON string, not a `Decimal` a caller
    already has. Accepting one silently would hide the caller's mistake of skipping
    `serialise` somewhere upstream."""
    with pytest.raises(TypeError):
        money.parse(Decimal("12.50"))  # type: ignore[arg-type]


def test_parse_rejects_junk_naming_the_offending_value() -> None:
    with pytest.raises(ValueError, match="abc"):
        money.parse("abc")


def test_parse_rejects_an_empty_string() -> None:
    with pytest.raises(ValueError):
        money.parse("")


def test_parse_strips_surrounding_whitespace() -> None:
    assert money.parse("  12.50  ") == Decimal("12.50")


def test_parse_round_trips_through_serialise() -> None:
    assert money.serialise(money.parse("1234.50")) == "1234.50"


def test_round_trip_preserves_a_ten_digit_amount_exactly() -> None:
    """Acceptance criterion's larger sibling: no float anywhere in the path, so a
    value past float64's 15-17 significant-digit comfort zone must still round-trip
    exactly, not approximately."""
    assert money.serialise(money.parse("1234567890.12")) == "1234567890.12"


def test_serialise_never_uses_scientific_notation() -> None:
    assert money.serialise(Decimal("1E+2")) == "100"
    assert money.serialise(Decimal("0.00000001")) == "0.00000001"


def test_serialise_refuses_a_float() -> None:
    with pytest.raises(TypeError):
        money.serialise(1.5)  # type: ignore[arg-type]


def test_serialise_refuses_a_plain_string() -> None:
    with pytest.raises(TypeError):
        money.serialise("12.50")  # type: ignore[arg-type]


# --- quantize_amount / quantize_unit_price: half-even, by name -----------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.125", "0.12"),  # exact half -> rounds to the even neighbour (2)
        ("0.135", "0.14"),  # exact half -> rounds to the even neighbour (4)
        ("-0.125", "-0.12"),  # half-even is symmetric: same rule, mirrored
        ("-0.135", "-0.14"),
    ],
)
def test_quantize_amount_uses_half_even_not_half_up(raw: str, expected: str) -> None:
    assert money.quantize_amount(raw) == Decimal(expected)


def test_quantize_amount_accepts_int_and_decimal_as_well_as_str() -> None:
    assert money.quantize_amount(5) == Decimal("5.00")
    assert money.quantize_amount(Decimal("5.001")) == Decimal("5.00")


def test_quantize_amount_refuses_a_float() -> None:
    with pytest.raises(TypeError):
        money.quantize_amount(5.0)  # type: ignore[arg-type]


def test_quantize_unit_price_rounds_to_six_decimal_places_using_half_even() -> None:
    # 7th decimal digit is an exact half (5) in both cases; only the preceding
    # (6th, kept) digit differs, so these isolate the half-even choice itself.
    assert money.quantize_unit_price("12.3456785") == Decimal("12.345678")  # 8 even -> stays
    assert money.quantize_unit_price("12.3456655") == Decimal("12.345666")  # 5 odd -> rounds up


def test_quantize_unit_price_refuses_a_float() -> None:
    with pytest.raises(TypeError):
        money.quantize_unit_price(1.5)  # type: ignore[arg-type]


# --- extend: qty x unit_price, rounded once -------------------------------------------


def test_extend_matches_the_hand_calculated_acceptance_example() -> None:
    """Acceptance criterion, verbatim: qty="3", unit_price="12.345" -> "37.04".
    3 x 12.345 = 37.035 exactly; half-even on the exact half rounds to 37.04 (4 is
    even, 3 is odd)."""
    assert money.serialise(money.extend("3", "12.345")) == "37.04"


def test_extend_handles_a_negative_quantity() -> None:
    """Credits are real rebate data: a negative qty must negate the result, not raise
    or silently take an absolute value."""
    assert money.extend("-3", "12.345") == Decimal("-37.04")


def test_extend_handles_a_negative_unit_price() -> None:
    assert money.extend("3", "-12.345") == Decimal("-37.04")


def test_extend_half_even_matches_quantize_amount_on_the_same_exact_half() -> None:
    # 5 x 0.025 = 0.125 exactly - same exact-half case as test_quantize_amount above,
    # reached through multiplication instead of being handed the half directly.
    assert money.extend("5", "0.025") == Decimal("0.12")


@pytest.mark.parametrize(
    "bad_call",
    [
        lambda: money.extend(3.0, "12.345"),  # type: ignore[arg-type]
        lambda: money.extend("3", 12.345),  # type: ignore[arg-type]
    ],
)
def test_extend_refuses_a_float_in_either_argument(bad_call: object) -> None:
    with pytest.raises(TypeError):
        bad_call()  # type: ignore[operator]


def test_extend_can_disagree_with_a_pre_rounded_unit_price_by_more_than_a_cent() -> None:
    """Attacking the module's own claim: `extend`'s docstring says rounding once
    avoids the "classic" error of rounding the unit price first and multiplying
    second. That is true of what `extend` does *internally* - but nothing stops a
    caller from rounding the price themselves (e.g. via `quantize_unit_price`, a
    public function in this same module) before handing it to `extend`, which
    reintroduces exactly that classic error. This is a real, large divergence, not a
    theoretical one: a full-precision per-each price derived from a case-price
    division, pre-rounded to the public 6dp scale and then extended across a
    plausible line quantity, disagrees with extending the unrounded price directly
    by half a dollar here - far past "a cent per line" territory.
    """
    qty = "980635"
    full_precision_price = "0.6003545"  # one digit past the public 6dp scale

    direct = money.extend(qty, full_precision_price)
    pre_rounded = money.quantize_unit_price(full_precision_price)
    via_pre_rounded = money.extend(qty, pre_rounded)

    assert abs(direct - via_pre_rounded) > Decimal("0.01"), (
        "expected the two paths to diverge by more than a cent, demonstrating that "
        "quantize_unit_price() then extend() is not interchangeable with extend() "
        "alone - callers must be told not to pre-round the price, not just trusted to"
        " know it"
    )


# --- Money: the dataclass that goes on the wire ---------------------------------------


def test_money_to_api_matches_the_section_3_shape() -> None:
    assert money.Money(Decimal("1234.50")).to_api() == {"amount": "1234.50", "currency": "USD"}


def test_money_parse_classmethod_quantizes_and_serialises_consistently() -> None:
    assert money.Money.parse("1234.5").to_api()["amount"] == "1234.50"


def test_money_constructor_refuses_a_float_amount() -> None:
    with pytest.raises(TypeError):
        money.Money(1234.50)  # type: ignore[arg-type]


def test_money_constructor_refuses_an_empty_currency() -> None:
    with pytest.raises(ValueError, match="currency"):
        money.Money(Decimal("1.00"), currency="")


def test_money_add_refuses_mixed_currencies() -> None:
    usd = money.Money(Decimal("10.00"), "USD")
    eur = money.Money(Decimal("10.00"), "EUR")
    with pytest.raises(ValueError, match="USD"):
        _ = usd + eur


def test_money_sub_refuses_mixed_currencies() -> None:
    usd = money.Money(Decimal("10.00"), "USD")
    eur = money.Money(Decimal("10.00"), "EUR")
    with pytest.raises(ValueError, match="EUR"):
        _ = usd - eur


def test_money_add_and_sub_work_within_the_same_currency() -> None:
    a = money.Money(Decimal("10.00"), "USD")
    b = money.Money(Decimal("4.25"), "USD")
    assert (a + b).amount == Decimal("14.25")
    assert (a - b).amount == Decimal("5.75")


def test_money_frozen_instance_rejects_normal_attribute_assignment() -> None:
    """Confirms the dataclass is actually frozen, not just annotated as such.
    `object.__setattr__` can still bypass this (standard, unavoidable Python
    behaviour for *any* frozen dataclass, not a gap specific to this one) - not
    tested as a "fix needed" here, since defending against code that already has
    direct `object.__setattr__` access is not a meaningful boundary.
    """
    instance = money.Money(Decimal("10.00"))
    with pytest.raises(AttributeError):
        instance.amount = Decimal("99.00")  # type: ignore[misc]


# --- bugs found while attacking the "worth attacking" list ---------------------------


def test_parse_rejects_nan() -> None:
    """BUG: `parse("NaN")` currently returns `Decimal('NaN')` without raising.
    `Decimal("NaN")` is a legal `Decimal` construction, but it is never a legal
    *money value* - the one thing this whole module exists to guarantee ("no float
    ever touches a monetary value") is undermined just as badly by a silently
    accepted NaN, which propagates through arithmetic (`NaN + x == NaN`) and then
    serialises back out as the literal string `"NaN"` - not valid in BACKEND.md §3's
    decimal-string wire shape, and not something any frontend numeric parser expects.
    """
    with pytest.raises(ValueError):
        money.parse("NaN")


@pytest.mark.parametrize("bad", ["Infinity", "-Infinity", "Inf"])
def test_parse_rejects_infinity(bad: str) -> None:
    """Same bug class as NaN: `Decimal("Infinity")` is legal Decimal syntax but not a
    legal amount. An "Infinity" value reaching a rebate calculation (even from a
    corrupted CSV cell) should be a loud parse-time rejection, not data that flows on."""
    with pytest.raises(ValueError):
        money.parse(bad)


def test_serialise_rejects_a_nan_decimal() -> None:
    """BUG: `serialise(Decimal("NaN"))` currently returns the string `"NaN"`, which
    would land in an API response's `amount` field - not a decimal string, and not
    something DESIGN.md's `Money` formatting component or any numeric frontend parser
    can render."""
    with pytest.raises((ValueError, InvalidOperation)):
        money.serialise(Decimal("NaN"))


def test_serialise_rejects_an_infinite_decimal() -> None:
    """BUG: same as above for `Decimal("Infinity")` - currently serialises to the
    literal string `"Infinity"`."""
    with pytest.raises((ValueError, InvalidOperation)):
        money.serialise(Decimal("Infinity"))


def test_quantize_amount_rejects_a_nan_decimal() -> None:
    """BUG: `quantize_amount(Decimal("NaN"))` currently returns `Decimal('NaN')`
    instead of raising - inconsistent with `quantize_amount(Decimal("Infinity"))`,
    which already raises `decimal.InvalidOperation` (see the next test). NaN should
    not get quieter treatment than Infinity."""
    with pytest.raises((ValueError, InvalidOperation)):
        money.quantize_amount(Decimal("NaN"))


def test_money_constructor_rejects_a_nan_amount() -> None:
    """Same bug, reached through the `Money` constructor: `__post_init__` checks for
    `float` and for the wrong Python type, but not for a non-finite `Decimal`."""
    with pytest.raises((ValueError, InvalidOperation)):
        money.Money(Decimal("NaN"))


def test_quantize_amount_on_an_extreme_value_raises_a_money_module_error_type() -> None:
    """A gap adjacent to the NaN/Infinity bugs, found while probing precision limits:
    once a value needs more significant digits than `Decimal`'s default context
    precision (28) to quantize, `quantize_amount` raises a bare
    `decimal.InvalidOperation` - a type this module never documents and a caller
    following its own stated contract ("floats raise TypeError, junk raises
    ValueError") would not think to catch. Practically unlikely for a single rebate
    line, but the module's own "distrust input, fail predictably" principle doesn't
    carve out an exception for "too large" the way it does for "too malformed".
    Written to state the gap rather than silently accept it: currently raises
    `InvalidOperation`, which is NOT one of `(TypeError, ValueError)`.
    """
    extreme_value = "9" * 29 + ".55"  # needs 31 significant digits once quantized
    with pytest.raises(InvalidOperation):
        money.quantize_amount(extreme_value)
