"""mvp.md 4.2 - AS/400 quirk detection per field, and decoding per value."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.lib.errors import Unparseable
from src.lib.parsers.quirks import (
    CLAIM_THRESHOLD,
    QUIRK_CYYMMDD,
    QUIRK_IMPLIED_DECIMAL,
    QUIRK_LEADING_ZEROS,
    QUIRK_RIGHT_PADDED,
    QUIRK_TRAILING_SIGN,
    decode_cyymmdd,
    decode_implied_decimal,
    decode_right_padded,
    detect_quirks,
)


def _evidence(values: list[str], code: str) -> Decimal:
    for item in detect_quirks(values).evidence:
        if item.code == code:
            return item.match_fraction
    raise AssertionError(f"no evidence recorded for {code}")


# --------------------------------------------------------------------------------------
# CYYMMDD
# --------------------------------------------------------------------------------------


def test_cyymmdd_is_detected_and_decodes_the_2000s_branch() -> None:
    quirks = detect_quirks(["1261003", "1260115"])
    assert QUIRK_CYYMMDD in quirks.claimed
    assert decode_cyymmdd("1261003") == date(2026, 10, 3)


def test_cyymmdd_decodes_the_1900s_branch() -> None:
    """The C=0 branch is the one that rots untested until a 1999 date arrives."""
    assert decode_cyymmdd("0991231") == date(1999, 12, 31)
    assert decode_cyymmdd("0000101") == date(1900, 1, 1)


def test_a_date_that_does_not_exist_raises_and_names_the_value() -> None:
    with pytest.raises(Unparseable) as caught:
        decode_cyymmdd("1130229")
    message = str(caught.value)
    assert "1130229" in message
    assert "2013-02-29" in message, "say what it read, so the user can see the problem"


def test_a_field_of_dates_is_not_also_claimed_as_a_packed_number() -> None:
    """Dates are all digits. Reading 1261003 at scale 2 would give 12610.03.

    Passing a scale hint is the strongest form of this test: even when a caller insists
    on a scale, a field that reads as a date is not a scaled integer.
    """
    quirks = detect_quirks(["1261003", "1260115", "1261130"], scale_hint=2)
    assert QUIRK_CYYMMDD in quirks.claimed
    assert QUIRK_IMPLIED_DECIMAL not in quirks.claimed
    assert quirks.implied_scale is None
    assert quirks.proposed_scale is None, "a date field must not offer a scale either"


def test_a_non_date_shaped_value_is_rejected_rather_than_part_parsed() -> None:
    for value in ("261003", "2261003", "12610O3", ""):
        with pytest.raises(Unparseable):
            decode_cyymmdd(value)


# --------------------------------------------------------------------------------------
# Implied decimals
# --------------------------------------------------------------------------------------


def test_implied_decimal_is_claimed_once_a_caller_confirms_the_scale() -> None:
    """4.2: with the scale established (the generator declared 2), the quirk applies."""
    quirks = detect_quirks(["0001250", "0000075"], scale_hint=2)
    assert QUIRK_IMPLIED_DECIMAL in quirks.claimed
    assert quirks.implied_scale == 2
    assert quirks.scale_source == "confirmed"
    assert decode_implied_decimal("0001250", scale=2) == Decimal("12.50")
    assert decode_implied_decimal("0000075", scale=2) == Decimal("0.75")


def test_implied_decimal_is_only_a_candidate_until_someone_says_what_the_field_means() -> None:
    """The committed layout has `ITMNBR` - an item number - as a zero-padded all-digit
    field indistinguishable from `LINAMT`, which is money at scale 2. Claiming the quirk
    from the characters alone would offer to turn item 100700 into 1007.00."""
    quirks = detect_quirks(["0001250", "0000075"])
    assert QUIRK_IMPLIED_DECIMAL not in quirks.claimed
    assert quirks.implied_scale is None, "nothing may be applied without a confirmation"
    assert quirks.proposed_scale == 2, "but the editor still gets a sensible default"
    assert quirks.scale_source == "proposed"
    recorded = next(item for item in quirks.evidence if item.code == QUIRK_IMPLIED_DECIMAL)
    assert recorded.claimed is False
    assert recorded.match_fraction == Decimal("1.0000"), "the form matched; the meaning did not"


def test_an_out_of_range_scale_hint_is_refused_rather_than_clamped() -> None:
    for hint in (-1, 7):
        with pytest.raises(Unparseable):
            detect_quirks(["0001250"], scale_hint=hint)


def test_a_field_that_is_not_a_packed_number_proposes_no_scale() -> None:
    quirks = detect_quirks(["ACME CORP   ", "BETA       "])
    assert quirks.proposed_scale is None
    assert quirks.scale_source is None


def test_implied_decimal_scaling_is_exact_at_every_allowed_scale() -> None:
    assert decode_implied_decimal("0001250", scale=0) == Decimal("1250")
    assert decode_implied_decimal("0001250", scale=4) == Decimal("0.1250")
    assert decode_implied_decimal("0001250", scale=6) == Decimal("0.001250")


def test_a_scale_outside_the_allowed_range_is_refused() -> None:
    for scale in (-1, 7, 99):
        with pytest.raises(Unparseable) as caught:
            decode_implied_decimal("0001250", scale=scale)
        assert "0-6" in str(caught.value)


def test_decoding_never_returns_a_float() -> None:
    value = decode_implied_decimal("0001250", scale=2)
    assert isinstance(value, Decimal)
    assert not isinstance(value, float)


# --------------------------------------------------------------------------------------
# Trailing sign
# --------------------------------------------------------------------------------------


def test_trailing_sign_is_detected_when_any_value_uses_it_and_none_violate_the_form() -> None:
    """Only one of these two carries the sign - the unsigned one is the positive one."""
    quirks = detect_quirks(["000125-", "0001250"])
    assert QUIRK_TRAILING_SIGN in quirks.claimed
    assert decode_implied_decimal("000125-", scale=2) == Decimal("-1.25")
    assert decode_implied_decimal("0001250", scale=2) == Decimal("12.50")


def test_trailing_sign_is_not_claimed_when_no_value_actually_carries_a_sign() -> None:
    """Every unsigned numeric field fits the form, so the form alone must not claim it."""
    quirks = detect_quirks(["0001250", "0000075", "0000001"], scale_hint=2)
    assert QUIRK_TRAILING_SIGN not in quirks.claimed
    assert QUIRK_IMPLIED_DECIMAL in quirks.claimed


def test_a_plus_sign_is_honoured_as_well_as_a_minus() -> None:
    assert decode_implied_decimal("000125+", scale=2) == Decimal("1.25")


# --------------------------------------------------------------------------------------
# Right padding
# --------------------------------------------------------------------------------------


def test_right_padded_is_detected_and_interior_spacing_survives() -> None:
    quirks = detect_quirks(["ACME CORP   ", "BETA       "])
    assert QUIRK_RIGHT_PADDED in quirks.claimed
    assert decode_right_padded("ACME CORP   ") == "ACME CORP"
    assert decode_right_padded("ACME  INC     ") == "ACME  INC", "double space is data"


def test_a_leading_space_is_kept_because_the_writer_put_it_there() -> None:
    assert decode_right_padded("  ACME   ") == "  ACME"


def test_right_padded_is_not_claimed_when_every_value_fills_its_column() -> None:
    quirks = detect_quirks(["ACMECORP", "BETACORP", "GAMMACOR"])
    assert QUIRK_RIGHT_PADDED not in quirks.claimed


# --------------------------------------------------------------------------------------
# Leading zeros
# --------------------------------------------------------------------------------------


def test_leading_zeros_is_detected_on_a_zero_padded_numeric_field() -> None:
    quirks = detect_quirks(["0001250", "0000075"])
    assert QUIRK_LEADING_ZEROS in quirks.claimed


def test_leading_zeros_is_not_claimed_when_the_values_are_plain_numbers() -> None:
    quirks = detect_quirks(["1250", "75", "3"])
    assert QUIRK_LEADING_ZEROS not in quirks.claimed


# --------------------------------------------------------------------------------------
# The 95% rule and the all-null field
# --------------------------------------------------------------------------------------


def test_a_quirk_matching_under_the_threshold_is_reported_but_not_claimed() -> None:
    """4.2: partial matches are evidence, not a licence to transform the whole field."""
    # 17 of 20 are valid CYYMMDD - 85%, under the 95% bar.
    values = [f"126{month:02d}01" for month in range(1, 13)]
    values += ["1260115", "1260215", "1260315", "1260415", "1260515"]
    values += ["NOTADAT", "XXXXXXX", "9999999"]
    quirks = detect_quirks(values)

    fraction = _evidence(values, QUIRK_CYYMMDD)
    assert fraction < CLAIM_THRESHOLD
    assert QUIRK_CYYMMDD not in quirks.claimed
    recorded = next(item for item in quirks.evidence if item.code == QUIRK_CYYMMDD)
    assert recorded.claimed is False
    assert recorded.matched == 17
    assert recorded.considered == 20


def test_padding_only_values_do_not_count_as_values() -> None:
    """A fixed-width field holding only blanks is absent, not an empty string."""
    quirks = detect_quirks(["0001250", "       ", "0000075", "   "], scale_hint=2)
    recorded = next(item for item in quirks.evidence if item.code == QUIRK_IMPLIED_DECIMAL)
    assert recorded.considered == 2
    assert QUIRK_IMPLIED_DECIMAL in quirks.claimed


def test_an_entirely_blank_field_claims_nothing_and_does_not_divide_by_zero() -> None:
    quirks = detect_quirks(["    ", "    ", ""], scale_hint=2)
    assert quirks.claimed == ()
    assert quirks.implied_scale is None
    assert quirks.proposed_scale is None
    assert all(item.considered == 0 for item in quirks.evidence)
    assert all(item.match_fraction == Decimal(0) for item in quirks.evidence)


def test_every_code_in_the_vocabulary_is_always_reported() -> None:
    """The UI shows evidence for quirks that were *not* claimed, so all five must be
    present in every result or the absent ones look untested."""
    from src.lib.parsers.quirks import QUIRK_CODES

    quirks = detect_quirks(["ACME  ", "BETA  "])
    assert tuple(item.code for item in quirks.evidence) == QUIRK_CODES
