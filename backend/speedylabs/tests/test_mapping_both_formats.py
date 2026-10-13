"""mvp.md 5.6 - both formats produce identical canonical tuples (acceptance criterion A1).

This is the first half of the spec's headline claim: "Both file formats map correctly; I
can override any mapping and re-run." The two files are the *same* 500 invoice lines
rendered two ways - a CSV with its decimal points written out, and the ERP's own
fixed-width output with packed numerics and implied scales. If the canonical tuples
differ by so much as a Decimal's scale, then every number downstream depends on which
file a customer happened to send, which is the opposite of the product.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.features.mapping.apply import (
    AppliedRow,
    FieldMapping,
    apply_mapping,
    canonical_values,
)
from src.features.mapping.transforms import parse
from src.lib.parsers.fwf_reader import SliceField, iter_fixed_width_rows
from tests.mappings import CSV_MAPPING, FIXED_WIDTH_MAPPING

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sample"
ORACLE = Path(__file__).resolve().parents[1] / "fixtures" / "oracle" / "expected_layout.json"


def _mapping(spec: dict[str, dict[str, Any]]) -> dict[str, FieldMapping]:
    return {
        name: FieldMapping(
            source_column=str(entry["sourceColumn"]), transform=parse(entry["transform"])
        )
        for name, entry in spec.items()
    }


def _csv_rows(mapping_spec: dict[str, dict[str, Any]] | None = None) -> list[AppliedRow]:
    with (FIXTURES / "month1_invoices.csv").open(encoding="ascii", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        data = list(reader)
    return list(apply_mapping(header, data, _mapping(mapping_spec or CSV_MAPPING)))


def _fixed_width_rows(
    mapping_spec: dict[str, dict[str, Any]] | None = None,
) -> list[AppliedRow]:
    """Sliced at the oracle's boundaries, with the ERP's own field names.

    That is the state P4's field editor produces once a human has entered the record
    layout: inference finds 14 of the 21 boundaries on its own, and the rest are typed
    in. Using the oracle here is using the answer a human would have given.
    """
    columns = json.loads(ORACLE.read_text())["columns"]
    fields = [
        SliceField(name=column["name"], start=column["start"], end=column["end"])
        for column in sorted(columns, key=lambda column: column["order"])
    ]
    path = FIXTURES / "month1_invoices.txt"
    for names, rows in iter_fixed_width_rows(path, fields):
        return list(
            apply_mapping(
                names,
                rows,
                _mapping(mapping_spec or FIXED_WIDTH_MAPPING),
                first_row_number=1,
            )
        )
    return []


def _canonical(rows: list[AppliedRow]) -> list[dict[str, Any]]:
    return [canonical_values(row) for row in rows]


def _scales(values: dict[str, Any]) -> dict[str, int | str]:
    """Every Decimal's exponent, so "identical" includes scale rather than just value.

    `Decimal("9.00") == Decimal("9")` is True in Python, so comparing values alone would
    not notice a scale drift - and the canonical CSV writes what the scale says.
    """
    return {
        name: value.as_tuple().exponent
        for name, value in values.items()
        if isinstance(value, Decimal)
    }


# --------------------------------------------------------------------------------------
# A1, first half
# --------------------------------------------------------------------------------------


def test_both_formats_map_to_identical_canonical_tuples_row_for_row() -> None:
    from_csv = _canonical(_csv_rows())
    from_fixed = _canonical(_fixed_width_rows())

    assert len(from_csv) == 500
    assert len(from_fixed) == 500

    for index, (csv_row, fixed_row) in enumerate(zip(from_csv, from_fixed, strict=True)):
        assert csv_row == fixed_row, (
            f"row {index + 1} differs between the two renderings:\n"
            f"  csv  : {csv_row}\n"
            f"  fixed: {fixed_row}"
        )


def test_identical_includes_decimal_scale_not_just_numeric_value() -> None:
    from_csv = _canonical(_csv_rows())
    from_fixed = _canonical(_fixed_width_rows())

    for index, (csv_row, fixed_row) in enumerate(zip(from_csv, from_fixed, strict=True)):
        assert _scales(csv_row) == _scales(fixed_row), f"row {index + 1} scales differ"

    # And the scales really are the ones the layout declares, not an accident.
    first = from_csv[0]
    assert first["qty"] == Decimal("9.00")
    assert first["qty"].as_tuple().exponent == -2
    assert first["unit_price"].as_tuple().exponent == -4


def test_neither_format_produces_a_single_failed_row() -> None:
    """Both mappings are complete and correct, so nothing should be unreadable. A
    failure here would mean one rendering carries a value the other does not."""
    assert [row.row_number for row in _csv_rows() if not row.ok] == []
    assert [row.row_number for row in _fixed_width_rows() if not row.ok] == []


def test_the_absent_values_are_absent_in_both_and_are_none_not_empty_strings() -> None:
    """The two files say "no pack size" differently - a blank CSV field, and six spaces
    in a fixed-width column. Both must become None: `strip_leading_zeros` used to turn
    the padded version into "0", which is a different and false statement."""
    from_csv = _canonical(_csv_rows())
    from_fixed = _canonical(_fixed_width_rows())

    optional = ("pack_size", "catch_weight_lbs", "deviated_price")
    absent_in_csv = {
        name: [index for index, row in enumerate(from_csv) if row[name] is None]
        for name in optional
    }
    absent_in_fixed = {
        name: [index for index, row in enumerate(from_fixed) if row[name] is None]
        for name in optional
    }
    assert absent_in_csv == absent_in_fixed
    # And they really are absent in some rows, or this proves nothing.
    assert all(len(rows) > 0 for rows in absent_in_csv.values())
    assert not any(value == "" for row in from_csv for value in row.values())
    assert not any(value == "" for row in from_fixed for value in row.values())


# --------------------------------------------------------------------------------------
# "I can override any mapping and re-run" - the other half of A1
# --------------------------------------------------------------------------------------


def test_overriding_one_field_changes_exactly_that_field_and_nothing_else() -> None:
    """Pointing `qty` at the pack-size column changes `qty` and leaves the other fifteen
    byte-identical. That is what makes an override safe to try."""
    baseline = _canonical(_csv_rows())

    overridden_spec = dict(CSV_MAPPING)
    overridden_spec["qty"] = {"sourceColumn": "CSEMUL", "transform": {"kind": "trim"}}
    overridden = _canonical(_csv_rows(overridden_spec))

    changed: set[str] = set()
    for before, after in zip(baseline, overridden, strict=True):
        for name in before:
            if before[name] != after[name]:
                changed.add(name)
    assert changed == {"qty"}, changed


def test_overriding_the_customer_column_changes_only_the_customer() -> None:
    """The decoy swap, as a change rather than as a verdict: `CUSTNO` is the ship-to
    account and `BILTNO` the bill-to, so this changes every customer_id and nothing
    else - which is precisely why it is dangerous and why the UI shows samples."""
    baseline = _canonical(_csv_rows())

    decoy_spec = dict(CSV_MAPPING)
    decoy_spec["customer_id"] = {"sourceColumn": "CUSTNO", "transform": {"kind": "trim"}}
    decoyed = _canonical(_csv_rows(decoy_spec))

    changed = {
        name
        for before, after in zip(baseline, decoyed, strict=True)
        for name in before
        if before[name] != after[name]
    }
    assert changed == {"customer_id"}
    differing = sum(
        1
        for before, after in zip(baseline, decoyed, strict=True)
        if before["customer_id"] != after["customer_id"]
    )
    assert differing > 400, "the two accounts differ on nearly every line"


def test_a_bad_row_is_reported_with_its_line_number_and_the_rest_still_process() -> None:
    """5.6's failure criterion, on the real file: break one field's transform and the
    other 499 rows still come through."""
    broken_spec = dict(CSV_MAPPING)
    # The invoice number is text; reading it as a CYYMMDD date cannot work.
    broken_spec["invoice_date"] = {
        "sourceColumn": "IVHDR01",
        "transform": {"kind": "cyymmdd_to_date"},
    }
    rows = _csv_rows(broken_spec)

    failed = [row for row in rows if not row.ok]
    assert len(failed) == 500, "every row fails this particular mistake"
    assert failed[0].row_number == 2, "physical line numbers, so a person can go and look"
    # The other fifteen fields are still read on a row whose date failed.
    assert failed[0].values["invoice_no"] == "HP26000001"
    assert failed[0].values["qty"] == "9.00"
    assert "invoice_date" not in failed[0].values


def test_the_credit_lines_survive_both_renderings_with_their_sign() -> None:
    """Found by this suite, not by design: the fixed-width file carries three credit
    lines as a negative quantity with the sign in the last character ("00000300-"), and
    the first fixed-width mapping read `qty` with `implied_decimal`, which refuses a
    trailing sign. Three of 500 lines vanished into failures.

    A dropped credit is an overpaid rebate, so these are pinned by line number.
    """
    from_csv = _canonical(_csv_rows())
    from_fixed = _canonical(_fixed_width_rows())

    credit_lines = [index for index, row in enumerate(from_csv) if row["qty"] < 0]
    assert credit_lines == [149, 157, 181], "the sample's three credit lines, 0-based"

    for index in credit_lines:
        assert from_csv[index]["qty"] == from_fixed[index]["qty"]
        assert from_csv[index]["ext_amount"] == from_fixed[index]["ext_amount"]
        assert from_csv[index]["qty"] < 0
        assert from_csv[index]["ext_amount"] < 0

    assert from_csv[149]["qty"] == Decimal("-3.00")
    assert from_csv[149]["ext_amount"] == Decimal("-269.19")
