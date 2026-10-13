"""UTC-only time handling (BACKEND.md §3: ISO 8601 UTC; §5: `timestamptz` columns),
plus step 0.5's AS/400 CYYMMDD codec.

A naive datetime must never be serialised - there is no way to know, downstream, which
zone it was supposed to mean. CYYMMDD must refuse anything it cannot decode exactly
rather than guess - decoding it wrong is the single most likely way to silently
mis-date a whole file.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from src.lib import timeutil


def test_now_is_always_timezone_aware_utc() -> None:
    value = timeutil.now()

    assert value.tzinfo is not None
    assert value.utcoffset() == timedelta(0)


def test_to_iso_refuses_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="naive"):
        timeutil.to_iso(datetime(2026, 10, 4, 9, 30, 0))


def test_to_iso_formats_utc_with_a_z_suffix() -> None:
    value = datetime(2026, 10, 4, 9, 30, 0, tzinfo=UTC)

    assert timeutil.to_iso(value) == "2026-10-04T09:30:00Z"


def test_to_iso_converts_a_non_utc_zone_to_utc_first() -> None:
    value = datetime(2026, 10, 4, 14, 30, 0, tzinfo=timezone(timedelta(hours=5)))

    assert timeutil.to_iso(value) == "2026-10-04T09:30:00Z"


def test_to_iso_handles_the_midnight_utc_boundary() -> None:
    value = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=-1)))

    assert timeutil.to_iso(value) == "2026-01-01T00:59:59Z"


# --- decode_cyymmdd / encode_cyymmdd --------------------------------------------------


def test_decode_cyymmdd_matches_the_acceptance_examples() -> None:
    assert timeutil.decode_cyymmdd("1261003") == date(2026, 10, 3)
    assert timeutil.decode_cyymmdd("0991231") == date(1999, 12, 31)


def test_decode_cyymmdd_rejects_a_date_that_does_not_exist() -> None:
    """1130229: 2013-02-29 - 2013 is not a leap year. The offending value must appear
    in the message, not just the decoded (and already-wrong) parts."""
    with pytest.raises(ValueError, match="1130229"):
        timeutil.decode_cyymmdd("1130229")


def test_decode_cyymmdd_accepts_a_bare_int_with_an_implied_leading_zero() -> None:
    """Numeric source columns lose a leading zero; `991231` must decode the same way
    as the zero-padded string `"0991231"` - both are the same 7-digit value."""
    assert timeutil.decode_cyymmdd(991231) == date(1999, 12, 31)
    assert timeutil.decode_cyymmdd(991231) == timeutil.decode_cyymmdd("0991231")


def test_decode_cyymmdd_accepts_century_digits_above_1() -> None:
    """The century digit is a single digit 0-9, not restricted to {0, 1}: `C=2` means
    the 2100s, not an error. `2130101` -> 2113-01-01, not a 1900s/2000s-only format."""
    assert timeutil.decode_cyymmdd("2130101") == date(2113, 1, 1)


def test_decode_cyymmdd_rejects_eight_digits_rather_than_silently_truncating() -> None:
    """An 8-digit value must never be read as a 7-digit one with the extra digit
    dropped - that would silently mis-date the row instead of refusing it."""
    with pytest.raises(ValueError, match="7 digits"):
        timeutil.decode_cyymmdd("12610030")


@pytest.mark.parametrize("bad", ["", "   "])
def test_decode_cyymmdd_rejects_empty_and_whitespace_only_input(bad: str) -> None:
    with pytest.raises(ValueError):
        timeutil.decode_cyymmdd(bad)


def test_decode_cyymmdd_rejects_a_negative_int() -> None:
    with pytest.raises(ValueError):
        timeutil.decode_cyymmdd(-1261003)


def test_decode_cyymmdd_rejects_a_bool() -> None:
    """`bool` is an `int` subclass in Python; `True`/`False` must not silently decode
    as `1`/`0` through the int branch."""
    with pytest.raises(TypeError):
        timeutil.decode_cyymmdd(True)


def test_decode_cyymmdd_rejects_a_float() -> None:
    with pytest.raises(TypeError):
        timeutil.decode_cyymmdd(1261003.0)  # type: ignore[arg-type]


def test_encode_cyymmdd_round_trips_decode_cyymmdd() -> None:
    for raw in ("1261003", "0991231", "2130101"):
        assert timeutil.encode_cyymmdd(timeutil.decode_cyymmdd(raw)) == raw


def test_encode_cyymmdd_rejects_a_year_outside_its_range() -> None:
    with pytest.raises(ValueError, match="2900"):
        timeutil.encode_cyymmdd(date(2900, 1, 1))
