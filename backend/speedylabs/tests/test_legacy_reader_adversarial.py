"""Attacking `src.features.legacy.reader.read` directly - pure function, no database, no
live stack needed. Companion to P12.2's own criteria (`docs/plans/phase-12-variances.md`
step 12.2): this file goes after the shapes the plan explicitly worries about
(duplicate columns, BOM, CRLF, embedded newlines, a header with trailing blanks) plus the
amount/period grammar boundaries and a few attack payloads (unicode, SQL-injection-shaped
text) to confirm they are merely *stored*, never interpreted.

Every case here was run ad hoc against the real module before being written down, so the
expected outcomes are measured, not guessed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.features.legacy.reader import MAX_LEGACY_ROWS, read
from src.lib.errors import LegacyUnmapped, Unparseable

HEADER = b"PROGRAM,PERIOD,REBATE_PAID"
SAMPLE_LEGACY_CSV = (
    Path(__file__).resolve().parents[1] / "fixtures" / "sample" / "legacy_rebates.csv"
)


def _csv(*lines: bytes) -> bytes:
    return b"\n".join(lines) + b"\n"


# --- the documented shape: three required, two permitted, nothing else -------------------------


def test_the_committed_fixture_reads_as_eighteen_rows() -> None:
    rows = read(SAMPLE_LEGACY_CSV.read_bytes())
    assert len(rows) == 18
    refs = {row.program_external_ref for row in rows}
    assert refs == {"PG-BLC-LB", "PG-CPC-CS", "PG-NVF-CS"}
    assert all(isinstance(row.amount, Decimal) for row in rows)
    assert [row.source_row_number for row in rows] == list(range(2, 20))


def test_a_missing_required_column_is_refused_naming_it() -> None:
    with pytest.raises(LegacyUnmapped) as exc:
        read(_csv(b"PROGRAM,PERIOD", b"PG-BLC-LB,2026-01"))
    assert "REBATE_PAID" in str(exc.value)


def test_an_unknown_column_is_refused_naming_it() -> None:
    with pytest.raises(LegacyUnmapped) as exc:
        read(_csv(HEADER + b",EXTRA_COL", b"PG-BLC-LB,2026-01,100.00,whatever"))
    assert "EXTRA_COL" in str(exc.value)


def test_an_erp_header_is_refused_by_shape() -> None:
    erp_header = (
        b"CUSTNO,BILTNO,INVDATE,INVNO,DISTITEM,MFRITEM,BRANDMFR,VENDNO,QTY,UOM,PACKSZ,"
        b"CWLBS,UNITPRC,EXTAMT,DEVFLAG,DEVPRC"
    )
    with pytest.raises(LegacyUnmapped):
        read(_csv(erp_header, b"C1,B1,2026-01-15,INV1,1,1,Acme,1,1,CS,,,"))


def test_four_column_file_without_program_name_imports_fine() -> None:
    rows = read(_csv(HEADER, b"PG-BLC-LB,2026-01,100.00"))
    assert len(rows) == 1
    assert rows[0].program_name_text is None


# --- BASIS_UNITS: permitted, present in raw, never its own field --------------------------------


def test_basis_units_is_permitted_and_lands_only_in_raw() -> None:
    rows = read(_csv(HEADER + b",BASIS_UNITS", b"PG-BLC-LB,2026-01,100.00,47255.94"))
    assert len(rows) == 1
    assert rows[0].raw["BASIS_UNITS"] == "47255.94"
    assert not hasattr(rows[0], "basis_units")
    assert not hasattr(rows[0], "units")
    assert not hasattr(rows[0], "basis_qty")


def test_legacy_row_has_no_quantity_field_of_any_kind() -> None:
    import dataclasses

    from src.features.legacy.reader import LegacyRow

    field_names = {f.name for f in dataclasses.fields(LegacyRow)}
    for forbidden in ("basis", "units", "qty", "quantity", "basis_units"):
        assert forbidden not in field_names


# --- the defect this phase exists to prevent, and a sibling it did NOT catch --------------------


def test_a_row_with_more_fields_than_the_header_is_refused_not_misaligned() -> None:
    """The measured P12.2 defect: an unquoted thousands separator splits one field into two,
    shifting every later value - the file used to import *cleanly* with a wrong amount."""
    with pytest.raises(Unparseable) as exc:
        read(_csv(HEADER, b"PG-BLC-LB,2026-01,1,234.50"))
    assert "4" in str(exc.value) and "3" in str(exc.value)


def test_a_short_row_is_refused_not_misaligned() -> None:
    with pytest.raises(Unparseable):
        read(_csv(HEADER, b"PG-BLC-LB,2026-01"))


def test_a_duplicate_required_column_is_refused_rather_than_silently_shadowed() -> None:
    # Body rewritten once the defect was fixed: it previously asserted the buggy shape under an
    # xfail, with a comment saying the intended behaviour was "a refusal naming the duplicate".
    # That is now what happens, so the test asserts the behaviour its own name promises.
    with pytest.raises(LegacyUnmapped) as refusal:
        read(_csv(HEADER + b",REBATE_PAID", b"PG-BLC-LB,2026-01,100.00,200.00"))
    assert "REBATE_PAID" in str(refusal.value)


# --- encoding and line-ending shapes --------------------------------------------------------------


def test_a_byte_order_mark_is_stripped() -> None:
    rows = read(b"\xef\xbb\xbf" + _csv(HEADER, b"PG-BLC-LB,2026-01,100.00"))
    assert len(rows) == 1
    assert rows[0].program_external_ref == "PG-BLC-LB"


def test_crlf_line_endings_are_read_cleanly() -> None:
    content = b"\r\n".join([HEADER, b"PG-BLC-LB,2026-01,100.00"]) + b"\r\n"
    rows = read(content)
    assert len(rows) == 1
    assert rows[0].amount == Decimal("100.00")


def test_a_quoted_comma_inside_program_name_does_not_shift_fields() -> None:
    rows = read(_csv(HEADER + b",PROGRAM_NAME", b'PG-BLC-LB,2026-01,100.00,"Bayline, Inc."'))
    assert len(rows) == 1
    assert rows[0].program_name_text == "Bayline, Inc."
    assert rows[0].amount == Decimal("100.00")


def test_an_embedded_newline_in_a_quoted_field_is_one_record() -> None:
    content = (
        HEADER
        + b",PROGRAM_NAME\n"
        + b'PG-BLC-LB,2026-01,100.00,"Line1\nLine2"\n'
        + b"PG-CPC-CS,2026-01,50.00,Simple\n"
    )
    rows = read(content)
    assert len(rows) == 2
    assert rows[0].program_name_text == "Line1\nLine2"
    assert rows[1].program_external_ref == "PG-CPC-CS"


def test_whitespace_around_header_names_is_stripped() -> None:
    rows = read(_csv(b" PROGRAM , PERIOD , REBATE_PAID ", b"PG-BLC-LB,2026-01,100.00"))
    assert len(rows) == 1


def test_a_trailing_blank_header_column_is_refused() -> None:
    with pytest.raises(LegacyUnmapped):
        read(_csv(HEADER + b",", b"PG-BLC-LB,2026-01,100.00,"))


def test_a_blank_line_in_the_middle_is_skipped_not_counted() -> None:
    content = HEADER + b"\nPG-BLC-LB,2026-01,100.00\n\nPG-CPC-CS,2026-01,50.00\n"
    rows = read(content)
    assert len(rows) == 2
    # Row numbers are physical lines; the blank line (3) is consumed but not emitted.
    assert [row.source_row_number for row in rows] == [2, 4]


def test_non_utf8_bytes_are_refused_not_a_500() -> None:
    with pytest.raises(Unparseable):
        read(b"\xff\xfe" + _csv(HEADER, b"PG-BLC-LB,2026-01,100.00"))


# --- the amount grammar: strict, never coerced, never rounded ------------------------------------


def test_three_decimal_places_is_refused_not_rounded() -> None:
    with pytest.raises(Unparseable) as exc:
        read(_csv(HEADER, b"PG-BLC-LB,2026-01,8930.715"))
    assert "8930.715" in str(exc.value)


def test_a_currency_symbol_is_refused() -> None:
    with pytest.raises(Unparseable):
        read(_csv(HEADER, b"PG-BLC-LB,2026-01,$100.00"))


def test_a_parenthesised_negative_is_refused() -> None:
    with pytest.raises(Unparseable):
        read(_csv(HEADER, b"PG-BLC-LB,2026-01,(100.00)"))


def test_a_thousands_separator_is_refused_via_the_field_count_guard() -> None:
    # An unquoted comma inside the amount splits the field, so this is caught by the row's
    # field-count check before the amount grammar ever runs - still refused, which is what
    # matters, but worth pinning which guard catches it.
    with pytest.raises(Unparseable) as exc:
        read(_csv(HEADER, b"PG-BLC-LB,2026-01,1,234.50"))
    assert "comma" in str(exc.value).lower()


def test_a_negative_amount_parses() -> None:
    rows = read(_csv(HEADER, b"PG-BLC-LB,2026-01,-100.00"))
    assert rows[0].amount == Decimal("-100.00")


def test_an_empty_program_is_refused() -> None:
    with pytest.raises(Unparseable) as exc:
        read(_csv(HEADER, b",2026-01,100.00"))
    assert "PROGRAM" in str(exc.value)


def test_a_whitespace_only_program_is_refused() -> None:
    with pytest.raises(Unparseable):
        read(_csv(HEADER, b"   ,2026-01,100.00"))


# --- the period grammar: month-end derivation at short, long and leap-adjacent months ------------


@pytest.mark.parametrize(
    ("period", "expected_start", "expected_end"),
    [
        ("2026-01", date(2026, 1, 1), date(2026, 1, 31)),
        ("2026-02", date(2026, 2, 1), date(2026, 2, 28)),
        ("2026-04", date(2026, 4, 1), date(2026, 4, 30)),
        ("2026-12", date(2026, 12, 1), date(2026, 12, 31)),
    ],
)
def test_period_month_end_derivation(period: str, expected_start: date, expected_end: date) -> None:
    rows = read(_csv(HEADER, f"PG-BLC-LB,{period},100.00".encode()))
    assert rows[0].period_start == expected_start
    assert rows[0].period_end == expected_end


@pytest.mark.parametrize(
    "period", ["2026-13", "2026-00", "2026-1", "2026-01-31", "26-01", "2026/01", "not-a-period"]
)
def test_a_malformed_period_is_refused(period: str) -> None:
    with pytest.raises(Unparseable):
        read(_csv(HEADER, f"PG-BLC-LB,{period},100.00".encode()))


# --- bounds --------------------------------------------------------------------------------------


def test_zero_data_rows_is_refused() -> None:
    with pytest.raises(Unparseable) as exc:
        read(HEADER + b"\n")
    assert "no data rows" in str(exc.value).lower()


def test_more_than_the_row_cap_is_refused_naming_the_cap() -> None:
    lines = [HEADER] + [b"PG-BLC-LB,2026-01,1.00"] * (MAX_LEGACY_ROWS + 1)
    with pytest.raises(Unparseable) as exc:
        read(_csv(*lines))
    assert f"{MAX_LEGACY_ROWS:,}" in str(exc.value)


def test_exactly_the_row_cap_is_accepted() -> None:
    lines = [HEADER] + [b"PG-BLC-LB,2026-01,1.00"] * MAX_LEGACY_ROWS
    rows = read(_csv(*lines))
    assert len(rows) == MAX_LEGACY_ROWS


# --- attack payloads: stored verbatim, never interpreted -----------------------------------------


def test_unicode_in_program_name_round_trips() -> None:
    content = (HEADER + b",PROGRAM_NAME\n") + "PG-BLC-LB,2026-01,100.00,Café Bayline 😀\n".encode()
    rows = read(content)
    assert rows[0].program_name_text == "Café Bayline 😀"


def test_sql_injection_shaped_program_name_is_just_text() -> None:
    payload = "Robert'); DROP TABLE legacy_rebate_lines;--"
    content = (HEADER + b",PROGRAM_NAME\n") + f"PG-BLC-LB,2026-01,100.00,{payload}\n".encode()
    rows = read(content)
    assert rows[0].program_name_text == payload  # stored as plain text, nothing executed


def test_18_digit_amount_survives_as_decimal_never_a_float() -> None:
    # The grammar caps at 15 integer digits; confirm the boundary value round-trips exactly
    # and is never coerced through `float`.
    rows = read(_csv(HEADER, b"PG-BLC-LB,2026-01,123456789012345.12"))
    assert rows[0].amount == Decimal("123456789012345.12")
    assert isinstance(rows[0].amount, Decimal)


# --- the scope guard holds structurally: no code path reaches the mapping machinery -------------


def test_no_module_in_the_legacy_package_imports_mapping_or_spec_machinery() -> None:
    import ast
    import pathlib

    package_dir = pathlib.Path(__import__("src.features.legacy", fromlist=["x"]).__file__).parent
    forbidden_substrings = (
        "features.mapping",
        "features.specs",
        "features.profiling",
        "lib.parsers.fwf_infer",
        "mapping_drafts",
        "mapping_specs",
    )
    for path in package_dir.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        source = path.read_text()
        for forbidden in forbidden_substrings:
            assert forbidden not in source, f"{path} references {forbidden!r}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "mapping" not in alias.name and "specs" not in alias.name, (
                        f"{path} imports {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert "mapping" not in node.module and "specs" not in node.module, (
                    f"{path} imports from {node.module}"
                )
