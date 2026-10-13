"""mvp.md 5.3 - the deterministic validators, and 5.6's apply primitive underneath them.

Every number in the identity tests is calculated by hand in the test itself, so a wrong
tolerance shows up as a wrong verdict rather than as agreement with whatever the code
happens to compute.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.features.mapping.apply import FieldMapping, apply_mapping
from src.features.mapping.transforms import parse
from src.features.mapping.validators import (
    FAIL_SHARE,
    FieldVerdict,
    require_complete,
    validate,
)
from src.lib.errors import MappingIncomplete


def _rows(header: list[str], data: list[list[str]], mapping: dict[str, dict[str, object]]):
    parsed = {
        name: FieldMapping(
            source_column=str(spec["sourceColumn"]), transform=parse(spec["transform"])
        )
        for name, spec in mapping.items()
    }
    return list(apply_mapping(header, data, parsed)), list(parsed)


def _verdict(verdicts: list[FieldVerdict], name: str) -> FieldVerdict:
    return next(v for v in verdicts if v.canonical_field == name)


# --------------------------------------------------------------------------------------
# A field that is mapped correctly
# --------------------------------------------------------------------------------------


def test_a_cyymmdd_column_mapped_with_the_right_transform_passes() -> None:
    rows, mapped = _rows(
        ["IVPSTDT"],
        [["1260101"], ["1260215"], ["1261130"]],
        {"invoice_date": {"sourceColumn": "IVPSTDT", "transform": {"kind": "cyymmdd_to_date"}}},
    )
    verdict = _verdict(validate(rows, mapped), "invoice_date")
    assert verdict.verdict == "pass"
    assert verdict.failed_rows == 0
    assert verdict.checked_rows == 3


def test_a_free_text_column_mapped_as_a_date_fails_with_examples() -> None:
    rows, mapped = _rows(
        ["BILTNM"],
        [["Cedar Grove"], ["Havenwood"], ["Riverbend"], ["Oakcrest"]],
        {"invoice_date": {"sourceColumn": "BILTNM", "transform": {"kind": "cyymmdd_to_date"}}},
    )
    verdict = _verdict(validate(rows, mapped), "invoice_date")
    assert verdict.verdict == "fail"
    assert verdict.failed_rows == 4
    assert verdict.example_row_numbers == (2, 3, 4, 5), "physical line numbers, 1-based"
    assert "Cedar Grove" in verdict.detail["firstMessage"]


# --------------------------------------------------------------------------------------
# uom: the enum, and the unmapped values named
# --------------------------------------------------------------------------------------


def test_uom_with_source_codes_and_no_value_map_fails_naming_drum() -> None:
    rows, mapped = _rows(
        ["UMCODE"],
        [["CASE"], ["EACH"], ["DRUM"], ["CASE"]],
        {"uom": {"sourceColumn": "UMCODE", "transform": {"kind": "upper"}}},
    )
    verdict = _verdict(validate(rows, mapped), "uom")
    assert verdict.verdict == "fail"
    assert "DRUM" in verdict.detail["unmappedValues"]
    assert set(verdict.detail["unmappedValues"]) == {"CASE", "EACH", "DRUM"}
    assert verdict.detail["allowed"] == ["CS", "EA", "LB"]


def test_uom_with_a_value_map_covering_every_code_passes() -> None:
    rows, mapped = _rows(
        ["UMCODE"],
        [["CASE"], ["EACH"], ["POUND"]],
        {
            "uom": {
                "sourceColumn": "UMCODE",
                "transform": {
                    "kind": "map_values",
                    "table": [
                        {"from": "CASE", "to": "CS"},
                        {"from": "EACH", "to": "EA"},
                        {"from": "POUND", "to": "LB"},
                    ],
                },
            }
        },
    )
    verdict = _verdict(validate(rows, mapped), "uom")
    assert verdict.verdict == "pass"


def test_a_value_the_map_does_not_cover_is_an_unreadable_row_not_a_silent_pass() -> None:
    rows, mapped = _rows(
        ["UMCODE"],
        [["CASE"], ["DRUM"]],
        {
            "uom": {
                "sourceColumn": "UMCODE",
                "transform": {"kind": "map_values", "table": [{"from": "CASE", "to": "CS"}]},
            }
        },
    )
    verdict = _verdict(validate(rows, mapped), "uom")
    assert verdict.verdict == "fail"
    assert verdict.failed_rows == 1
    assert "DRUM" in verdict.detail["firstMessage"]


# --------------------------------------------------------------------------------------
# warn: the mapping is right, the data has gaps
# --------------------------------------------------------------------------------------


def test_a_required_field_null_in_three_percent_of_rows_warns_rather_than_fails() -> None:
    """The plan's own example. A human decides whether 3% is tolerable; refusing to
    proceed would be the tool overruling the person who knows the business."""
    data = [["18.41"] for _ in range(97)] + [[""] for _ in range(3)]
    rows, mapped = _rows(
        ["SELPRC"],
        data,
        {"unit_price": {"sourceColumn": "SELPRC", "transform": {"kind": "trim"}}},
    )
    verdict = _verdict(validate(rows, mapped), "unit_price")
    assert verdict.verdict == "warn"
    assert verdict.failed_rows == 3
    assert verdict.checked_rows == 100
    assert verdict.detail["nullShare"] == "0.0300"
    assert len(verdict.example_row_numbers) == 3


def test_nulls_past_the_fail_share_become_a_failure() -> None:
    """Above a fifth it is not a gap, it is the wrong column."""
    null_count = 30
    data = [["18.41"] for _ in range(70)] + [[""] for _ in range(null_count)]
    rows, mapped = _rows(
        ["SELPRC"],
        data,
        {"unit_price": {"sourceColumn": "SELPRC", "transform": {"kind": "trim"}}},
    )
    verdict = _verdict(validate(rows, mapped), "unit_price")
    assert Decimal(verdict.detail["nullShare"]) > FAIL_SHARE
    assert verdict.verdict == "fail"


def test_an_entirely_empty_column_fails_however_plausible_its_name() -> None:
    rows, mapped = _rows(
        ["RSRVD1"],
        [[""], ["   "], [""]],
        {"customer_name": {"sourceColumn": "RSRVD1", "transform": {"kind": "trim"}}},
    )
    verdict = _verdict(validate(rows, mapped), "customer_name")
    assert verdict.verdict == "fail"
    assert verdict.detail["reason"] == "every sampled row is empty"


def test_an_optional_field_that_is_entirely_null_is_not_a_failure() -> None:
    """`catch_weight_lbs` is genuinely absent on a file of case-only items."""
    rows, mapped = _rows(
        ["SHPWGT"],
        [[""], [""], [""]],
        {
            "catch_weight_lbs": {
                "sourceColumn": "SHPWGT",
                "transform": {"kind": "implied_decimal", "scale": 2},
            }
        },
    )
    verdict = _verdict(validate(rows, mapped), "catch_weight_lbs")
    assert verdict.verdict == "warn", (
        "the schema says this field may be absent, so an empty column is a question for "
        "a human - 'is that the right column?' - not a verdict against the schema"
    )
    assert "optional" in verdict.detail["reason"]


# --------------------------------------------------------------------------------------
# The cross-field identity, hand-calculated
# --------------------------------------------------------------------------------------


def _identity_rows(triples: list[tuple[str, str, str]]):
    return _rows(
        ["SHPUNT", "SELPRC", "LINAMT"],
        [list(triple) for triple in triples],
        {
            "qty": {"sourceColumn": "SHPUNT", "transform": {"kind": "trim"}},
            "unit_price": {"sourceColumn": "SELPRC", "transform": {"kind": "trim"}},
            "ext_amount": {"sourceColumn": "LINAMT", "transform": {"kind": "trim"}},
        },
    )


def test_the_identity_passes_when_every_line_ties_out() -> None:
    # 9 x 18.41 = 165.69 exactly. 3 x 19.16 = 57.48 exactly.
    rows, mapped = _identity_rows([("9", "18.41", "165.69"), ("3", "19.16", "57.48")])
    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")
    assert verdict.verdict == "pass"
    assert verdict.checked_rows == 2
    assert verdict.failed_rows == 0


def test_a_one_cent_rounding_difference_is_inside_tolerance() -> None:
    # 3 x 19.165 = 57.495; a distributor rounding to 57.49 differs by 0.005.
    rows, mapped = _identity_rows([("3", "19.165", "57.49")])
    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")
    assert verdict.verdict == "pass"
    assert verdict.detail["largestDifference"] == "0"


def test_a_real_disagreement_is_counted_with_the_largest_difference() -> None:
    """One bad line in twenty is dirty data, which warns. The share is what decides, so
    the sample has to be big enough for one row to be a small share of it - in a
    three-row sample one bad row is 33%, which is a failure and rightly so."""
    # 9 x 18.41 = 165.69 exactly. A line claiming 165.00 is out by 0.69 - far past a
    # cent, and past 0.1% of 165.00 (0.165).
    triples = [("9", "18.41", "165.69") for _ in range(19)]
    triples.append(("9", "18.41", "165.00"))
    rows, mapped = _identity_rows(triples)

    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")

    assert verdict.checked_rows == 20
    assert verdict.failed_rows == 1
    assert verdict.example_row_numbers == (21,), "the 20th data row is physical line 21"
    assert verdict.detail["largestDifference"] == "0.69"
    assert Decimal(verdict.detail["mismatchShare"]) == Decimal("0.0500")
    assert verdict.verdict == "warn"


def test_one_bad_line_in_three_is_past_the_threshold_and_fails() -> None:
    """The other side of the same rule, stated so the boundary is not a surprise."""
    rows, mapped = _identity_rows(
        [("9", "18.41", "165.69"), ("9", "18.41", "165.00"), ("3", "19.16", "57.48")]
    )
    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")
    assert Decimal(verdict.detail["mismatchShare"]) > FAIL_SHARE
    assert verdict.verdict == "fail"


def test_a_qty_pointed_at_the_wrong_column_fails_the_identity() -> None:
    """The check no single-column rule can make: every value here is a valid number, and
    the mapping is still wrong."""
    rows, mapped = _rows(
        ["SHPUNT", "CSEMUL", "SELPRC", "LINAMT"],
        [
            ["9", "6", "18.41", "165.69"],
            ["3", "12", "19.16", "57.48"],
            ["5", "24", "10.00", "50.00"],
        ],
        {
            # CSEMUL is the pack size, not the quantity.
            "qty": {"sourceColumn": "CSEMUL", "transform": {"kind": "trim"}},
            "unit_price": {"sourceColumn": "SELPRC", "transform": {"kind": "trim"}},
            "ext_amount": {"sourceColumn": "LINAMT", "transform": {"kind": "trim"}},
        },
    )
    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")
    assert verdict.verdict == "fail"
    assert verdict.failed_rows == 3
    assert Decimal(verdict.detail["mismatchShare"]) == Decimal("1.0000")


def test_the_identity_is_skipped_when_one_of_the_three_is_unmapped() -> None:
    rows, mapped = _rows(
        ["SHPUNT", "SELPRC"],
        [["9", "18.41"]],
        {
            "qty": {"sourceColumn": "SHPUNT", "transform": {"kind": "trim"}},
            "unit_price": {"sourceColumn": "SELPRC", "transform": {"kind": "trim"}},
        },
    )
    assert all(v.canonical_field != "ext_amount_identity" for v in validate(rows, mapped))


def test_no_float_appears_anywhere_in_the_identity_check() -> None:
    rows, mapped = _identity_rows([("9", "18.41", "165.69")])
    for row in rows:
        for value in row.values.values():
            assert not isinstance(value, float), value
    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")
    for value in verdict.detail.values():
        assert not isinstance(value, float), value


# --------------------------------------------------------------------------------------
# mapping_incomplete
# --------------------------------------------------------------------------------------


def test_validating_an_incomplete_mapping_names_every_missing_required_field() -> None:
    with pytest.raises(MappingIncomplete) as caught:
        require_complete(["invoice_no", "invoice_date", "qty"])
    fields = caught.value.fields or {}
    assert "customer_id" in fields
    assert "uom" in fields
    assert "ext_amount" in fields
    # The optional ones are not demanded.
    for optional in ("pack_size", "catch_weight_lbs", "deviated_price"):
        assert optional not in fields
    assert "customer_id" in caught.value.message


def test_a_complete_mapping_raises_nothing() -> None:
    from src.features.canonical.model import REQUIRED_FIELD_NAMES

    require_complete(list(REQUIRED_FIELD_NAMES))


# --------------------------------------------------------------------------------------
# apply: one bad row does not lose the file
# --------------------------------------------------------------------------------------


def test_a_failing_row_is_reported_with_its_line_number_and_the_rest_still_process() -> None:
    rows, _ = _rows(
        ["IVPSTDT"],
        [["1260101"], ["notadate"], ["1260215"]],
        {"invoice_date": {"sourceColumn": "IVPSTDT", "transform": {"kind": "cyymmdd_to_date"}}},
    )
    assert [row.ok for row in rows] == [True, False, True]
    assert rows[1].row_number == 3
    assert "notadate" in rows[1].failures["invoice_date"]
    assert rows[0].values["invoice_date"] is not None
    assert rows[2].values["invoice_date"] is not None


def test_a_short_row_reads_as_empty_rather_than_raising() -> None:
    """A ragged row is refused at parse time, but this function must not be the thing
    that crashes if one ever reaches it."""
    rows, mapped = _rows(
        ["A", "B"],
        [["1260101"]],
        {
            "invoice_date": {"sourceColumn": "A", "transform": {"kind": "cyymmdd_to_date"}},
            "customer_name": {"sourceColumn": "B", "transform": {"kind": "trim"}},
        },
    )
    assert rows[0].values["customer_name"] == ""
    assert _verdict(validate(rows, mapped), "customer_name").verdict == "fail"


def test_a_mapping_naming_a_column_that_is_not_in_the_header_yields_no_values() -> None:
    rows, mapped = _rows(
        ["A"],
        [["x"]],
        {"customer_name": {"sourceColumn": "NOPE", "transform": {"kind": "trim"}}},
    )
    assert rows[0].values == {}
    assert _verdict(validate(rows, mapped), "customer_name").verdict == "fail"


def test_the_row_limit_stops_reading_early() -> None:
    parsed = {"customer_name": FieldMapping(source_column="A", transform=parse({"kind": "trim"}))}
    data = [[f"row{index}"] for index in range(1000)]
    rows = list(apply_mapping(["A"], data, parsed, limit=10))
    assert len(rows) == 10
    assert rows[-1].row_number == 11


def test_fixed_width_rows_are_numbered_from_one() -> None:
    """A fixed-width file has no header, so its first record is physical line 1."""
    parsed = {"customer_name": FieldMapping(source_column="A", transform=parse({"kind": "trim"}))}
    rows = list(apply_mapping(["A"], [["x"], ["y"]], parsed, first_row_number=1))
    assert [row.row_number for row in rows] == [1, 2]


# --------------------------------------------------------------------------------------
# The identity picks its basis per line: catch weight where there is one
# --------------------------------------------------------------------------------------


def _weighted_rows(quads: list[tuple[str, str, str, str]]):
    """(qty, unit_price, ext_amount, catch_weight_lbs)."""
    return _rows(
        ["SHPUNT", "SELPRC", "LINAMT", "SHPWGT"],
        [list(quad) for quad in quads],
        {
            "qty": {"sourceColumn": "SHPUNT", "transform": {"kind": "trim"}},
            "unit_price": {"sourceColumn": "SELPRC", "transform": {"kind": "trim"}},
            "ext_amount": {"sourceColumn": "LINAMT", "transform": {"kind": "trim"}},
            "catch_weight_lbs": {"sourceColumn": "SHPWGT", "transform": {"kind": "trim"}},
        },
    )


def test_a_catch_weight_line_extends_on_pounds_not_on_quantity() -> None:
    """2 cases weighing 30.00 lb at 5.64/lb extends to 169.20, not to 2 x 5.64 = 11.28.
    Checking such a line against qty would report a mismatch of 157.92 - which is how
    98 perfectly good lines in the committed sample came to look broken."""
    rows, mapped = _weighted_rows([("2.00", "5.6400", "169.20", "30.00")])
    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")
    assert verdict.verdict == "pass"
    assert verdict.detail["rowsTiedOnCatchWeight"] == 1
    assert verdict.detail["rowsTiedOnQty"] == 0


def test_a_catch_weight_item_with_no_weight_is_the_mismatch_worth_reporting() -> None:
    """The real defect the sample contains twice: priced per pound, no pounds recorded.
    With no weight there is nothing to extend on but qty, and it does not tie out."""
    rows, mapped = _weighted_rows([("2.00", "5.6400", "169.20", "")])
    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")
    assert verdict.failed_rows == 1
    assert verdict.detail["rowsTiedOnQty"] == 0, "it ties out on neither basis"
    assert verdict.detail["rowsTiedOnCatchWeight"] == 0
    assert verdict.detail["largestDifference"] == "157.92"


def test_a_zero_weight_line_still_ties_out_on_quantity() -> None:
    """A weight of 0.00 would make the weight-based expectation zero, so the line ties out
    on quantity instead - and both are tried, so there is no cliff edge here."""
    rows, mapped = _weighted_rows([("9.00", "18.4100", "165.69", "0.00")])
    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")
    assert verdict.verdict == "pass"
    assert verdict.detail["rowsTiedOnQty"] == 1


def test_a_noise_weight_does_not_fail_a_correct_quantity_priced_line() -> None:
    """Found by the Phase 5 tester. The basis used to be chosen by "is there a weight?",
    so a correct qty-priced line carrying a 0.001 rounding artefact in its weight column
    flipped to the weight basis and failed outright. Both bases are now tried, and a line
    that ties out on either is a line whose three columns agree."""
    rows, mapped = _weighted_rows([("9.00", "18.4100", "165.69", "0.001")] * 10)
    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")
    assert verdict.verdict == "pass"
    assert verdict.detail["rowsTiedOnQty"] == 10
    assert verdict.detail["rowsTiedOnCatchWeight"] == 0


def test_a_wrong_qty_on_catch_weight_lines_is_a_known_blind_spot() -> None:
    """Also found by the Phase 5 tester, and recorded rather than fixed here.

    On a line that ties out on pounds, `qty` gets no coverage from this check at all - so
    a file of mostly catch-weight lines can carry a completely wrong `qty` mapping past
    it, every value well-formed. Catching that needs a judgement about whether a quantity
    is *plausible*, which belongs with P8's readiness checks and not in an arithmetic
    identity. This test pins the gap so that closing it is a visible change.
    """
    rows, mapped = _weighted_rows([("999", "5.6400", "169.20", "30.00")] * 10)
    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")
    assert verdict.verdict == "pass", "the blind spot, asserted so it cannot close silently"
    assert verdict.detail["rowsTiedOnCatchWeight"] == 10


def test_case_and_catch_weight_lines_are_counted_separately() -> None:
    rows, mapped = _weighted_rows(
        [
            ("9.00", "18.4100", "165.69", ""),
            ("2.00", "5.6400", "169.20", "30.00"),
            ("3.00", "19.1600", "57.48", ""),
        ]
    )
    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")
    assert verdict.verdict == "pass"
    assert verdict.detail["rowsTiedOnCatchWeight"] == 1
    assert verdict.detail["rowsTiedOnQty"] == 2


# --------------------------------------------------------------------------------------
# Against the committed sample, with the hand-written mapping
# --------------------------------------------------------------------------------------


def _apply_committed_csv(limit: int | None = None):
    import csv

    from tests.mappings import CSV_MAPPING

    path = Path(__file__).resolve().parents[1] / "fixtures" / "sample" / "month1_invoices.csv"
    with path.open(encoding="ascii", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        data = list(reader)
    mapping = {
        name: FieldMapping(
            source_column=str(spec["sourceColumn"]), transform=parse(spec["transform"])
        )
        for name, spec in CSV_MAPPING.items()
    }
    return list(apply_mapping(header, data, mapping, limit=limit)), list(mapping)


def test_the_hand_written_csv_mapping_passes_every_field() -> None:
    """No LLM involved anywhere: this is the fallback the spec requires, so it is proven
    on the real file rather than asserted to be possible."""
    rows, mapped = _apply_committed_csv()
    require_complete(mapped)
    verdicts = validate(rows, mapped)

    per_field = {
        v.canonical_field: v for v in verdicts if v.canonical_field != "ext_amount_identity"
    }
    assert len(per_field) == 16
    failures = {name: v.verdict for name, v in per_field.items() if v.verdict != "pass"}
    assert failures == {}, failures
    assert all(v.checked_rows == 500 for v in per_field.values())


def test_the_committed_sample_has_exactly_two_lines_that_do_not_tie_out() -> None:
    """Pinned deliberately. These two are the generator's seeded catch-weight-without-
    weight defect; if this number moves, either the fixture or the identity changed."""
    rows, mapped = _apply_committed_csv()
    verdict = _verdict(validate(rows, mapped), "ext_amount_identity")

    assert verdict.verdict == "warn"
    assert verdict.failed_rows == 2
    assert verdict.checked_rows == 500
    assert verdict.example_row_numbers == (134, 189)
    # 98 + 400 + 2 = 500. The counts are of lines that *tied out* on each basis, so the
    # two failures are in neither - which is why this is 400 and not 402.
    assert verdict.detail["rowsTiedOnCatchWeight"] == 98
    assert verdict.detail["rowsTiedOnQty"] == 400


def test_pointing_customer_id_at_the_decoy_column_still_validates_which_is_the_point() -> None:
    """`CUSTNO` is the ship-to account - the planted decoy. It is a perfectly valid
    string column, so no single-field rule can catch the substitution. This test exists
    to record that: the deterministic validators do not save a user from this mistake,
    which is exactly why the mapping UI shows sample values and why P8's readiness
    checks look at customer-level totals."""
    import csv

    from tests.mappings import CSV_MAPPING

    path = Path(__file__).resolve().parents[1] / "fixtures" / "sample" / "month1_invoices.csv"
    with path.open(encoding="ascii", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        data = list(reader)

    wrong = dict(CSV_MAPPING)
    wrong["customer_id"] = {"sourceColumn": "CUSTNO", "transform": {"kind": "trim"}}
    mapping = {
        name: FieldMapping(
            source_column=str(spec["sourceColumn"]), transform=parse(spec["transform"])
        )
        for name, spec in wrong.items()
    }
    rows = list(apply_mapping(header, data, mapping))
    verdict = _verdict(validate(rows, list(mapping)), "customer_id")
    assert verdict.verdict == "pass", (
        "a wrong-but-well-formed column passes field-level validation; the samples in "
        "the UI are what a human distinguishes it by"
    )


def test_five_thousand_rows_across_sixteen_fields_validate_well_under_three_seconds() -> None:
    """5.3's speed criterion. The sample is 500 rows, so it is repeated tenfold to reach
    the configured sample size."""
    import time

    rows, mapped = _apply_committed_csv()
    many = [rows[index % len(rows)] for index in range(5_000)]
    assert len(many) == 5_000

    start = time.monotonic()
    require_complete(mapped)
    verdicts = validate(many, mapped)
    elapsed = time.monotonic() - start

    assert len(verdicts) == 17, "16 fields plus the identity"
    assert elapsed < 3, f"validation took {elapsed:.2f}s against a 3s budget"


def test_no_float_reaches_any_value_or_verdict_detail() -> None:
    """5.3: "no float appears in any computation". Checked over the real file rather
    than a constructed row, because the risk is a parser returning one."""
    rows, mapped = _apply_committed_csv(limit=200)
    for row in rows:
        for value in row.values.values():
            assert not isinstance(value, float), (row.row_number, value)
    for verdict in validate(rows, mapped):
        for value in verdict.detail.values():
            assert not isinstance(value, float), (verdict.canonical_field, value)
