"""Step 0.7 — six months of clean invoice lines."""

from __future__ import annotations

from decimal import Decimal

from src.generator.invoices import ALWAYS_REQUIRED, build_lines
from src.lib.money import extend


def test_row_count_within_2_percent_of_50000() -> None:
    lines = build_lines()
    assert abs(len(lines) - 50_000) <= 1_000, f"{len(lines)} rows is not within 2% of 50,000"


def test_dates_span_exactly_six_consecutive_months_weekdays_only() -> None:
    lines = build_lines()
    months = sorted({(d.year, d.month) for d in (line.invoice_date for line in lines)})
    assert months == [(2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6)]
    for line in lines:
        assert line.invoice_date.weekday() < 5, f"row {line.row_number} falls on a weekend"


def test_every_ext_amount_equals_qty_times_unit_price_or_catch_weight() -> None:
    lines = build_lines()
    mismatches: list[tuple[int, Decimal, Decimal]] = []
    for line in lines:
        basis = line.catch_weight_lbs if line.uom == "LB" else line.qty
        assert basis is not None, f"row {line.row_number}: no basis to extend"
        expected = extend(basis, line.unit_price)
        if expected != line.ext_amount:
            mismatches.append((line.row_number, expected, line.ext_amount))
    assert not mismatches, f"{len(mismatches)} lines disagree, e.g. {mismatches[:5]}"


def test_two_runs_produce_identical_row_sets_and_order() -> None:
    first = build_lines()
    second = build_lines()
    assert first == second


def test_no_required_field_is_none() -> None:
    lines = build_lines()
    offenders = [
        (line.row_number, line.missing_required()) for line in lines if line.missing_required()
    ]
    assert not offenders, (
        f"{len(offenders)} clean lines are missing a required field: {offenders[:5]}"
    )


def test_always_required_fields_cover_the_canonical_non_conditional_set() -> None:
    # Sanity on the test fixture itself: conditional fields (pack_size, catch_weight_lbs,
    # deviated_price) must NOT be in ALWAYS_REQUIRED, or missing_required() would flag
    # every case line for lacking a catch weight.
    assert "pack_size" not in ALWAYS_REQUIRED
    assert "catch_weight_lbs" not in ALWAYS_REQUIRED
    assert "deviated_price" not in ALWAYS_REQUIRED


def test_credits_exist_and_carry_negative_qty_and_ext() -> None:
    lines = build_lines()
    credit_lines = [line for line in lines if line.qty < 0]
    assert credit_lines, "no credit lines were generated"
    for line in credit_lines:
        assert line.ext_amount < 0
    rate = len(credit_lines) / len(lines)
    # ~1 in 70 case lines, so loosely around 1%-2% of the whole file.
    assert Decimal("0.003") < Decimal(rate) < Decimal("0.03"), (
        f"credit rate {rate:.4f} is implausible"
    )


def test_catch_weight_lines_always_carry_a_weight_in_clean_data() -> None:
    lines = build_lines()
    for line in lines:
        if line.uom == "LB":
            assert line.catch_weight_lbs is not None
            assert line.pack_size is None
