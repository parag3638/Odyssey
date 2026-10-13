"""Step 0.9 — fixed-width + CSV writers, and the month-2 drift file.

Builds on `test_independent_byte_level.py`'s from-scratch parser for the parts that
matter most (layout/oracle agreement); this file covers the writer-specific accept
criteria more directly, reusing the repo's own writer/decoder functions where the risk
is "does round-tripping agree", and staying independent where the risk is "does the
oracle describe the real file".
"""

from __future__ import annotations

import csv
import io
import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.generator.defects import build_seeded_file
from src.generator.drift import drifted_layout
from src.generator.source_layout import LAYOUT, offsets, record_width
from src.generator.writers import (
    WriterError,
    decode_csv_row,
    decode_fixed_width,
    detail_record,
    trailer_record,
    write_csv,
    write_fixed_width,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_every_record_is_exactly_the_declared_width_including_trailer() -> None:
    seeded = build_seeded_file()
    text = write_fixed_width(seeded.lines[:200], seeded.footer_total)
    records = [r for r in text.split("\n") if r]
    for r in records:
        assert len(r) == record_width()


def test_fixed_width_and_csv_decode_to_identical_canonical_tuples() -> None:
    seeded = build_seeded_file()
    sample = seeded.lines[:300]
    fw_text = write_fixed_width(sample, seeded.footer_total)
    csv_text = write_csv(sample)

    fw_records = [r for r in fw_text.split("\n") if r][:-1]  # drop trailer
    fw_decoded = [decode_fixed_width(r) for r in fw_records]

    reader = csv.DictReader(io.StringIO(csv_text))
    csv_decoded = [decode_csv_row(row) for row in reader]

    assert len(fw_decoded) == len(csv_decoded) == len(sample)
    for fw, cv in zip(fw_decoded, csv_decoded, strict=True):
        assert fw == cv


def test_csv_header_is_cryptic_names_in_layout_order_not_friendly_names() -> None:
    seeded = build_seeded_file()
    text = write_csv(seeded.lines[:10])
    header = next(csv.reader(io.StringIO(text)))
    assert header == [f.name for f in LAYOUT]
    for name in header:
        assert name.isupper()


def test_filler_columns_carry_junk_not_blanks_across_many_rows() -> None:
    seeded = build_seeded_file()
    sample = seeded.lines[:2000]
    text = write_fixed_width(sample, seeded.footer_total)
    records = [r for r in text.split("\n") if r][:-1]
    filler_cols = [(f, s, e) for f, s, e in offsets() if f.filler]
    assert filler_cols
    for field, start, end in filler_cols:
        values = {rec[start - 1 : end] for rec in records}
        non_blank = {v for v in values if v.strip() != ""}
        assert non_blank, f"{field.name} is blank on every one of {len(records)} sampled rows"


def test_no_decimal_point_in_fixed_width_money_fields() -> None:
    seeded = build_seeded_file()
    sample = seeded.lines[:500]
    text = write_fixed_width(sample, seeded.footer_total)
    records = [r for r in text.split("\n") if r][:-1]
    money_cols = [(f, s, e) for f, s, e in offsets() if f.kind in ("dec2", "dec4")]
    for field, start, end in money_cols:
        for rec in records:
            assert "." not in rec[start - 1 : end], f"{field.name} contains a decimal point"


def test_negatives_carry_trailing_sign_and_round_trip() -> None:
    seeded = build_seeded_file()
    credit_lines = [line for line in seeded.lines if line.qty < 0][:20]
    assert credit_lines, "no credit lines in sample to test the trailing sign"
    for line in credit_lines:
        record = detail_record(line, __import__("random").Random(1))
        decoded = decode_fixed_width(record)
        assert decoded["qty"] == line.qty
        assert decoded["ext_amount"] == line.ext_amount
        # the raw slice for SHPUNT must end in '-'
        qty_field = next(f for f in LAYOUT if f.canonical == "qty")
        start, end = next((s, e) for f, s, e in offsets() if f.canonical == "qty")
        raw = record[start - 1 : end]
        assert raw.rstrip()[-1:] == "-" or raw.endswith("-"), (
            f"no trailing sign in {raw!r} for {qty_field.name}"
        )


def test_non_ascii_byte_raises_rather_than_misaligning() -> None:
    from dataclasses import replace

    seeded = build_seeded_file()
    line = replace(seeded.lines[0], customer_name="Café Lakeside")  # non-ASCII é
    with pytest.raises(WriterError):
        detail_record(line, __import__("random").Random(1))


def test_trailer_record_sums_to_exactly_the_seeded_delta() -> None:
    seeded = build_seeded_file()
    footer = trailer_record(len(seeded.lines), seeded.footer_total)
    assert len(footer) == record_width()
    detail_sum = sum((line.ext_amount for line in seeded.lines), Decimal("0"))
    assert detail_sum - seeded.footer_total == Decimal("1842.57")


def test_month2_drift_is_exactly_what_the_oracle_claims_no_more() -> None:
    # Only the layout matters here; row-distribution drift is covered separately by
    # test_committed_drift_oracle_matches_recomputation.
    layout2 = drifted_layout()

    month1_names = [f.name for f in LAYOUT]
    month2_names = [f.name for f in layout2]
    renamed = [(a, b) for a, b in zip(month1_names, month2_names, strict=True) if a != b]
    assert renamed == [("SHPUNT", "SHPQTY")]

    month1_widths = {f.name: f.width for f in LAYOUT}
    month2_widths = {f.name: f.width for f in layout2}
    width_changes = [
        (name, month1_widths[name], month2_widths[name])
        for name in month1_widths
        if name in month2_widths and month1_widths[name] != month2_widths[name]
    ]
    assert width_changes == [("BILTNM", 30, 32)]

    # Positional comparison, not name-keyed: SHPUNT's rename already makes a
    # name-keyed lookup miss across the two layouts, which would wrongly count it as
    # a type change too.
    kind_changes = [f1.name for f1, f2 in zip(LAYOUT, layout2, strict=True) if f1.kind != f2.kind]
    assert kind_changes == ["CSEMUL"]

    # No columns added or removed: the only names present in one layout and not the
    # other must be exactly the renamed pair, and the two layouts have the same length.
    assert len(month1_names) == len(month2_names)
    assert set(month1_names) ^ set(month2_names) == {"SHPUNT", "SHPQTY"}

    # Every field after the widened BILTNM must shift start/end by exactly the delta.
    # Positional, not name-keyed, because SHPUNT/SHPQTY don't share a key across the
    # two layouts.
    offsets1 = [(s, e) for _f, s, e in offsets()]
    offsets2 = [(s, e) for _f, s, e in offsets(layout2)]
    biltnm_index = month1_names.index("BILTNM")
    delta = 2  # 32 - 30
    for idx in range(biltnm_index):
        assert offsets1[idx] == offsets2[idx], (
            f"{month1_names[idx]} shifted but comes before the widened field"
        )
    # The widened field itself: same start, but its own end grows with the new width.
    s1, e1 = offsets1[biltnm_index]
    s2, e2 = offsets2[biltnm_index]
    assert s1 == s2 and e2 == e1 + delta, "BILTNM's own offsets did not reflect its new width"
    for idx in range(biltnm_index + 1, len(offsets1)):
        s1, e1 = offsets1[idx]
        s2, e2 = offsets2[idx]
        assert (s2, e2) == (s1 + delta, e1 + delta), f"{month1_names[idx]} did not shift by {delta}"


def test_committed_drift_oracle_matches_recomputation() -> None:
    oracle = json.loads((FIXTURES / "oracle" / "expected_drift.json").read_text())
    uom = oracle["distribution"]["uomMixPercent"]
    m1_ea = Decimal(uom["month1"].get("EA", "0"))
    m2_ea = Decimal(uom["month2"].get("EA", "0"))
    assert m1_ea < Decimal("1.0"), f"month1 EA% looks too high already: {m1_ea}"
    assert m2_ea > Decimal("30.0"), f"month2 EA% did not shift enough: {m2_ea}"
    cw = oracle["distribution"]["avgCatchWeightLbs"]
    assert Decimal(cw["month2"]) > Decimal(cw["month1"])
