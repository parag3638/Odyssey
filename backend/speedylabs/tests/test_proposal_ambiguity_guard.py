"""The look-alike rule (`proposal_guard.flag_indistinguishable`) as a pure function.

docs/plans/proposal-ambiguity-guard.md, step 1. No database and no model: the rule takes a
proposal, the profile columns it was made from and the model's own ambiguity lines, and returns
the finished pair. Everything the plan lists as an acceptance condition of the rule itself is
here; the paths that carry it (fresh, cache hit, idempotent replay) are in
`test_proposal_ambiguity_propose.py`.
"""

from __future__ import annotations

import copy
import gzip
import json
import re
import shutil
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from src.features.mapping import proposal_guard
from src.features.mapping.proposal_guard import (
    PARTY_FIELDS,
    clean_ambiguities,
    flag_indistinguishable,
)
from src.lib.parsers.csv_reader import iter_csv_rows
from src.lib.parsers.fwf_reader import SliceField, iter_fixed_width_rows
from src.lib.profiling.accumulator import profile_rows
from tests.proposal_support import GUARD_LINE, REASON_SENTENCE

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _column(
    name: str,
    *,
    type_guess: str = "integer",
    distinct: int = 25,
    null_pct: str = "0.000000",
    signature: str = "999999",
) -> dict[str, Any]:
    """A profile row shaped like `propose._profile_columns` returns."""
    patterns = [{"signature": signature, "count": 400}] if signature else []
    return {
        "column_name": name,
        "type_guess": type_guess,
        "null_pct": Decimal(null_pct),
        "distinct_count": distinct,
        "sample_values": [],
        "patterns": patterns,
    }


def _entry(column: str, confidence: str = "0.9", reason: str | None = "Samples look right.") -> Any:
    return {
        "sourceColumn": column,
        "transform": {"kind": "trim"},
        "origin": "llm",
        "confidence": confidence,
        "reason": reason,
    }


PAIR = [_column("CUSTNO"), _column("BILTNO"), _column("ITMNBR", distinct=300)]


# --------------------------------------------------------------------------------------
# The rule fires: cap, reason, ambiguity
# --------------------------------------------------------------------------------------


def test_a_confident_pick_between_two_look_alikes_is_capped_explained_and_named() -> None:
    entries, ambiguities = flag_indistinguishable({"customer_id": _entry("BILTNO")}, PAIR, [])

    assert entries["customer_id"]["confidence"] == "0.50"
    assert entries["customer_id"]["reason"] == f"Samples look right. {REASON_SENTENCE}"
    assert ambiguities == [GUARD_LINE]


def test_the_reason_is_just_the_sentence_when_the_model_gave_none() -> None:
    entries, _ = flag_indistinguishable({"customer_id": _entry("BILTNO", reason=None)}, PAIR, [])

    assert entries["customer_id"]["reason"] == REASON_SENTENCE


def test_picking_the_other_column_of_the_pair_reverses_the_sentence() -> None:
    entries, _ = flag_indistinguishable({"customer_id": _entry("CUSTNO")}, PAIR, [])

    assert entries["customer_id"]["reason"].endswith(
        "Lowered because BILTNO looks the same as CUSTNO in this file; only the distributor's "
        "record layout can decide."
    )


def test_a_confidence_already_under_the_cap_is_never_raised() -> None:
    entries, ambiguities = flag_indistinguishable(
        {"customer_id": _entry("BILTNO", confidence="0.30")}, PAIR, []
    )

    assert entries["customer_id"]["confidence"] == "0.30"
    assert ambiguities == [GUARD_LINE], "the ambiguity is still added"


def test_a_confidence_exactly_at_the_cap_is_left_as_written() -> None:
    entries, _ = flag_indistinguishable(
        {"customer_id": _entry("BILTNO", confidence="0.5")}, PAIR, []
    )

    assert entries["customer_id"]["confidence"] == "0.5"


def test_three_look_alikes_cap_at_a_third_and_the_line_names_all_three() -> None:
    columns = [_column("AAA"), _column("BBB"), _column("CCC"), _column("ZZZ", distinct=3)]

    entries, ambiguities = flag_indistinguishable({"customer_id": _entry("CCC")}, columns, [])

    assert entries["customer_id"]["confidence"] == "0.33"
    assert entries["customer_id"]["reason"].endswith(
        "Lowered because AAA and BBB look the same as CCC in this file; only the "
        "distributor's record layout can decide."
    )
    assert len(ambiguities) == 1
    assert ambiguities[0].startswith("customer_id: AAA, BBB and CCC look the same in this file")


def test_the_other_party_field_is_covered_too() -> None:
    entries, ambiguities = flag_indistinguishable({"invoice_vendor": _entry("CUSTNO")}, PAIR, [])

    assert entries["invoice_vendor"]["confidence"] == "0.50"
    assert ambiguities[0].startswith("invoice_vendor: CUSTNO and BILTNO look the same")


def test_the_scope_is_exactly_the_two_party_fields() -> None:
    assert {"customer_id", "invoice_vendor"} == PARTY_FIELDS


def test_both_party_fields_in_one_proposal_each_get_their_own_line() -> None:
    entries, ambiguities = flag_indistinguishable(
        {"customer_id": _entry("BILTNO"), "invoice_vendor": _entry("CUSTNO")}, PAIR, []
    )

    assert entries["customer_id"]["confidence"] == entries["invoice_vendor"]["confidence"] == "0.50"
    assert [line.split(":")[0] for line in ambiguities] == ["customer_id", "invoice_vendor"]


# --------------------------------------------------------------------------------------
# The rule stays quiet
# --------------------------------------------------------------------------------------


def test_a_look_alike_pair_on_a_field_outside_the_party_fields_is_untouched() -> None:
    columns = [_column("SELPRC", type_guess="decimal"), _column("LINAMT", type_guess="decimal")]
    entries = {"unit_price": _entry("SELPRC"), "ext_amount": _entry("LINAMT")}

    finished, ambiguities = flag_indistinguishable(entries, columns, ["model line"])

    assert finished == entries
    assert ambiguities == ["model line"]


@pytest.mark.parametrize(
    ("label", "twin"),
    [
        ("distinct counts differ by one", _column("BILTNO", distinct=26)),
        ("share of blanks differs", _column("BILTNO", null_pct="0.000002")),
        ("type differs", _column("BILTNO", type_guess="text")),
        ("top value shape differs", _column("BILTNO", signature="99999")),
    ],
)
def test_one_statistic_apart_is_not_a_look_alike(label: str, twin: dict[str, Any]) -> None:
    del label
    entries, ambiguities = flag_indistinguishable(
        {"customer_id": _entry("BILTNO")}, [_column("CUSTNO"), twin], []
    )

    assert entries["customer_id"]["confidence"] == "0.9"
    assert ambiguities == []


def test_two_blank_columns_do_not_match_each_other() -> None:
    columns = [
        _column(name, type_guess="empty", distinct=0, null_pct="1.000000", signature="")
        for name in ("FLD017", "FLD023")
    ]

    entries, ambiguities = flag_indistinguishable({"customer_id": _entry("FLD017")}, columns, [])

    assert entries["customer_id"]["confidence"] == "0.9"
    assert ambiguities == []


def test_a_blank_column_is_not_a_twin_of_a_populated_one() -> None:
    columns = [_column("CUSTNO"), _column("FLD017", distinct=0, signature="")]

    entries, _ = flag_indistinguishable({"customer_id": _entry("CUSTNO")}, columns, [])

    assert entries["customer_id"]["confidence"] == "0.9"


def test_a_column_missing_from_the_profile_is_returned_as_stored() -> None:
    """A replay after a layout change: the entry names a column the profile no longer has."""
    entries = {"customer_id": _entry("GONE")}

    finished, ambiguities = flag_indistinguishable(entries, PAIR, ["uom: UMCODE vs RSRVD1"])

    assert finished == entries
    assert ambiguities == ["uom: UMCODE vs RSRVD1"]


def test_an_empty_profile_and_an_empty_proposal_are_both_fine() -> None:
    assert flag_indistinguishable({}, PAIR, []) == ({}, [])
    entries = {"customer_id": _entry("BILTNO")}
    assert flag_indistinguishable(entries, [], []) == (entries, [])


def test_a_profile_value_that_is_not_a_number_does_not_raise() -> None:
    odd = _column("CUSTNO")
    odd["null_pct"] = "not-a-number"
    entries = {"customer_id": _entry("BILTNO")}

    finished, ambiguities = flag_indistinguishable(entries, [odd, _column("BILTNO")], [])

    assert finished == entries
    assert ambiguities == []


# --------------------------------------------------------------------------------------
# Ambiguity lines: cleaning, de-duplication, order
# --------------------------------------------------------------------------------------


def test_a_model_line_that_already_names_the_field_and_both_columns_suppresses_the_guard_line() -> (
    None
):
    model_line = "customer_id: CUSTNO vs BILTNO - nothing in the samples separates them."

    entries, ambiguities = flag_indistinguishable(
        {"customer_id": _entry("BILTNO")}, PAIR, [model_line]
    )

    assert ambiguities == [model_line]
    assert entries["customer_id"]["confidence"] == "0.50", "the cap still applies"


@pytest.mark.parametrize(
    "model_line",
    [
        "customer_id: BILTNO could be the ship-to.",
        "CUSTNO vs BILTNO - cannot tell.",
        "customer_id: CUSTNO2 vs BILTNO2 - cannot tell.",
        "customer_id: custno vs biltno - cannot tell.",
    ],
)
def test_a_model_line_that_does_not_name_everything_does_not_suppress_it(model_line: str) -> None:
    _, ambiguities = flag_indistinguishable({"customer_id": _entry("BILTNO")}, PAIR, [model_line])

    assert ambiguities == [model_line, GUARD_LINE]


def test_model_lines_come_first_in_their_order_then_the_guard_line() -> None:
    _, ambiguities = flag_indistinguishable(
        {"customer_id": _entry("BILTNO")}, PAIR, ["uom: UMCODE vs RSRVD1", "qty: unclear unit"]
    )

    assert ambiguities == ["uom: UMCODE vs RSRVD1", "qty: unclear unit", GUARD_LINE]


def test_clean_ambiguities_strips_drops_blanks_and_dedupes_keeping_first_and_order() -> None:
    lines = ["  b  ", "a", "", "   ", "b", "a ", "\tc\n", "b"]

    assert clean_ambiguities(lines) == ["b", "a", "c"]


def test_exact_duplicate_and_whitespace_only_model_lines_collapse_in_the_result() -> None:
    _, ambiguities = flag_indistinguishable(
        {"customer_id": _entry("BILTNO")},
        PAIR,
        ["uom: UMCODE vs RSRVD1", "   ", "uom: UMCODE vs RSRVD1 ", ""],
    )

    assert ambiguities == ["uom: UMCODE vs RSRVD1", GUARD_LINE]


# --------------------------------------------------------------------------------------
# Purity, and the log line
# --------------------------------------------------------------------------------------


def test_neither_the_entries_nor_the_columns_nor_the_model_lines_are_mutated() -> None:
    entries = {"customer_id": _entry("BILTNO"), "qty": _entry("SHPUNT")}
    model_lines = ["  uom: UMCODE vs RSRVD1 ", "", "uom: UMCODE vs RSRVD1 "]
    before = copy.deepcopy((entries, PAIR, model_lines))

    flag_indistinguishable(entries, PAIR, model_lines)

    assert (entries, PAIR, model_lines) == before


def test_firing_logs_one_warning_with_names_and_no_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The app configures structlog with `cache_logger_on_first_use=True`, so once any earlier test
    # has used the module's `log`, `capture_logs()` can no longer see it. A fresh logger made
    # inside the capture has nothing cached, which makes the test independent of run order.
    with capture_logs() as logs:
        monkeypatch.setattr(proposal_guard, "log", structlog.get_logger())
        flag_indistinguishable({"customer_id": _entry("BILTNO")}, PAIR, [])

    capped = [line for line in logs if line["event"] == "llm_confidence_capped"]
    assert len(capped) == 1
    assert capped[0]["log_level"] == "warning"
    assert capped[0]["canonical_field"] == "customer_id"
    assert capped[0]["source_column"] == "BILTNO"
    assert capped[0]["look_alikes"] == ["CUSTNO"]
    assert capped[0]["reported"] == "0.9"
    assert capped[0]["capped"] == "0.50"


def test_staying_quiet_logs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    with capture_logs() as logs:
        monkeypatch.setattr(proposal_guard, "log", structlog.get_logger())
        flag_indistinguishable({"customer_id": _entry("ITMNBR")}, PAIR, [])

    assert [line for line in logs if line["event"] == "llm_confidence_capped"] == []


# --------------------------------------------------------------------------------------
# Measured: the twelve committed fixtures
# --------------------------------------------------------------------------------------

_ORACLE = json.loads((FIXTURES / "oracle" / "expected_layout.json").read_text())
_SLICES = [SliceField(c["name"], c["start"], c["end"]) for c in _ORACLE["columns"]]
_FILES = sorted((FIXTURES / "sample").glob("month*")) + sorted((FIXTURES / "full").glob("*.gz"))


@pytest.fixture(scope="module")
def unzipped(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    folder = tmp_path_factory.mktemp("fixtures")
    for source in _FILES:
        if source.suffix == ".gz":
            with gzip.open(source, "rb") as src, (folder / source.stem).open("wb") as dst:
                shutil.copyfileobj(src, dst)
        else:
            shutil.copy(source, folder / source.name)
    yield folder


def _profile_columns(path: Path) -> list[dict[str, Any]]:
    rows = iter_csv_rows(path) if path.suffix == ".csv" else iter_fixed_width_rows(path, _SLICES)
    names, data = next(rows)
    return [
        {
            "column_name": p.column_name,
            "type_guess": p.type_guess,
            "null_pct": p.null_pct,
            "distinct_count": p.distinct_count,
            "sample_values": p.sample_values,
            "patterns": p.patterns,
        }
        for p in profile_rows(names, data)
    ]


def test_there_are_twelve_committed_fixture_files() -> None:
    assert len(_FILES) == 12, [f.name for f in _FILES]


@pytest.mark.parametrize("fixture", _FILES, ids=lambda path: path.name)
def test_each_committed_fixture_has_exactly_one_look_alike_pair_custno_and_biltno(
    unzipped: Path, fixture: Path
) -> None:
    """The plan's measurement, through the public rule: offer every column in turn as the
    `customer_id` source and collect the ones the rule caps. Exactly CUSTNO and BILTNO - no
    six-digit id pair, no price pair - in the 500-row samples and the 48,150-row export alike."""
    columns = _profile_columns(
        unzipped / (fixture.stem if fixture.suffix == ".gz" else fixture.name)
    )

    capped: set[str] = set()
    for column in columns:
        name = str(column["column_name"])
        entries, ambiguities = flag_indistinguishable({"customer_id": _entry(name)}, columns, [])
        if entries["customer_id"]["confidence"] != "0.9":
            capped.add(name)
            mentioned = {
                str(c["column_name"])
                for c in columns
                if re.search(rf"\b{c['column_name']}\b", ambiguities[0])
            }
            assert mentioned == {"CUSTNO", "BILTNO"}, ambiguities[0]

    assert capped == {"CUSTNO", "BILTNO"}
