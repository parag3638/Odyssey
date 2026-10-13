"""mvp.md 4.1 - fixed-width boundary inference, scored against the committed oracle.

The oracle (`fixtures/oracle/expected_layout.json`) is what the generator actually
wrote, so it is the only honest scoreboard. The numbers asserted here are committed
deliberately: inference finds 14 of the layout's 21 boundaries and invents none of them.
A change that raises 14 is an improvement and should update these; a change that
produces a boundary the generator never wrote is a defect, which is why the zero-false-
cuts assertion is separate and absolute.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.lib.errors import Unparseable
from src.lib.parsers.fwf_infer import (
    AMBIGUOUS_MIN_WIDTH,
    MIN_RECORDS_FOR_CONFIDENCE,
    SAMPLE_RECORD_COUNT,
    infer_layout,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FWF = REPO_ROOT / "fixtures" / "sample" / "month1_invoices.txt"
ORACLE = REPO_ROOT / "fixtures" / "oracle" / "expected_layout.json"

#: The boundaries inference finds on the committed sample, as (start, end) 1-based pairs.
EXPECTED_BOUNDARIES: tuple[tuple[int, int], ...] = (
    (1, 18),
    (19, 37),
    (38, 49),
    (50, 58),
    (59, 61),
    (62, 91),
    (92, 108),
    (109, 122),
    (123, 135),
    (136, 139),
    (140, 145),
    (146, 155),
    (156, 163),
    (164, 186),
)

#: Field indexes flagged low-confidence: wide, with no blank padding inside them.
#: Indexes 0, 1 and 6 really are several fields each. Index 13 (164-186) is four fields
#: and is NOT flagged - `ALWPRC` is empty in 90% of records, so its blanks read as
#: interior padding and the field looks structured. That blind spot is asserted rather
#: than hidden, so a future change that closes it fails here and gets noticed.
EXPECTED_LOW_CONFIDENCE_INDEXES: frozenset[int] = frozenset({0, 1, 6})


def _oracle_cuts() -> set[int]:
    oracle = json.loads(ORACLE.read_text())
    return {int(column["end"]) for column in oracle["columns"]}


def test_inference_invents_no_boundary_the_generator_did_not_write() -> None:
    """The absolute criterion: a false cut splits a real field and corrupts every value."""
    layout = infer_layout(SAMPLE_FWF)
    found = {field.end for field in layout.fields}
    assert found - _oracle_cuts() == set(), (
        f"inference proposed boundaries absent from the oracle: {sorted(found - _oracle_cuts())}"
    )


def test_inference_matches_the_committed_boundaries_on_the_sample() -> None:
    layout = infer_layout(SAMPLE_FWF)
    assert layout.record_width == 186
    assert layout.has_trailer is True
    assert layout.evidence.records_sampled == 500, "the TRLR record must not be sampled"
    assert tuple((f.start, f.end) for f in layout.fields) == EXPECTED_BOUNDARIES


def test_inference_recall_against_the_oracle_is_committed_not_assumed() -> None:
    layout = infer_layout(SAMPLE_FWF)
    oracle_cuts = _oracle_cuts()
    found = {field.end for field in layout.fields}
    assert len(found & oracle_cuts) == 14
    assert len(oracle_cuts) == 21
    # The seven invisible boundaries, named so a reader knows exactly what is missing.
    assert sorted(oracle_cuts - found) == [10, 25, 29, 97, 169, 170, 175]


def test_a_filler_column_comes_back_as_an_ordinary_field() -> None:
    """4.1: the tool cannot know a column is filler, and guessing would start a
    heuristic that silently drops real data. FLD023 occupies 136-139 in the oracle."""
    layout = infer_layout(SAMPLE_FWF)
    assert (136, 139) in tuple((f.start, f.end) for f in layout.fields)


def test_fields_are_contiguous_and_cover_the_whole_record() -> None:
    layout = infer_layout(SAMPLE_FWF)
    assert layout.fields[0].start == 1
    assert layout.fields[-1].end == layout.record_width
    for earlier, later in zip(layout.fields, layout.fields[1:], strict=False):
        assert later.start == earlier.end + 1, "a gap or overlap would lose characters"


def test_wide_fields_with_no_interior_padding_are_flagged_low_confidence() -> None:
    layout = infer_layout(SAMPLE_FWF)
    flagged = {field.index for field in layout.fields if field.confidence == "low"}
    assert flagged == EXPECTED_LOW_CONFIDENCE_INDEXES
    for field in layout.fields:
        if field.confidence == "low":
            assert field.note is not None and "several fields" in field.note
    regions = {region.field_index for region in layout.evidence.ambiguous_regions}
    assert regions == EXPECTED_LOW_CONFIDENCE_INDEXES


def test_the_evidence_carries_verbatim_records_for_the_ruler() -> None:
    """The editor draws cut markers over real characters, padding included - a ruler over
    stripped values would show different characters from the ones the cuts fall between."""
    layout = infer_layout(SAMPLE_FWF)
    records = layout.evidence.sample_records

    assert len(records) == SAMPLE_RECORD_COUNT
    assert all(len(record) == 186 for record in records), "verbatim, not stripped"
    assert records[0].startswith("HP26000001"), records[0]
    assert not any(record.upper().startswith("TRLR") for record in records)


def test_the_evidence_says_the_field_count_is_a_lower_bound() -> None:
    """The one thing a user must not conclude is that 14 fields means 14 fields."""
    layout = infer_layout(SAMPLE_FWF)
    assert any("lower bound" in note for note in layout.evidence.notes)
    assert layout.evidence.threshold == Decimal("0.99")
    assert len(layout.evidence.blank_fractions) == 186
    assert layout.evidence.confidence == "low"


# --------------------------------------------------------------------------------------
# Constructed files: the cases the committed sample cannot show
# --------------------------------------------------------------------------------------


def _write(path: Path, records: list[str]) -> Path:
    path.write_text("".join(f"{record}\n" for record in records), encoding="ascii")
    return path


def test_two_fields_abutting_with_no_padding_come_back_as_one_flagged_field(
    tmp_path: Path,
) -> None:
    """4.1: return them as one field and record the ambiguity - never guess the cut."""
    # "ACME  " (6, right-padded text) + two 8-character zero-padded numbers that are
    # full width in every record, so nothing separates them.
    records = [f"ACME  {index:08d}{index * 7:08d}" for index in range(1, 61)]
    path = _write(tmp_path / "abut.txt", records)

    layout = infer_layout(path)

    assert layout.record_width == 22
    assert tuple((f.start, f.end) for f in layout.fields) == ((1, 6), (7, 22))
    merged = layout.fields[1]
    assert merged.width >= AMBIGUOUS_MIN_WIDTH
    assert merged.confidence == "low"
    assert merged.note is not None and "several fields" in merged.note
    assert [(r.start, r.end) for r in layout.evidence.ambiguous_regions] == [(7, 22)]


def test_a_short_file_still_returns_a_layout_but_marked_low_confidence(
    tmp_path: Path,
) -> None:
    """4.1: fewer than 20 records still gets a layout - a draft beats an error here."""
    records = [f"ACME  {index:04d}" for index in range(1, 11)]
    path = _write(tmp_path / "short.txt", records)

    layout = infer_layout(path)

    assert len(records) < MIN_RECORDS_FOR_CONFIDENCE
    assert tuple((f.start, f.end) for f in layout.fields) == ((1, 6), (7, 10))
    assert layout.evidence.confidence == "low"
    assert layout.evidence.records_sampled == 10
    assert any(str(MIN_RECORDS_FOR_CONFIDENCE) in note for note in layout.evidence.notes)
    # Low confidence for the sample size, not for ambiguity - these fields are narrow.
    assert layout.evidence.ambiguous_regions == ()
    assert all(field.confidence == "low" for field in layout.fields)


def test_ragged_records_raise_naming_the_first_offending_line(tmp_path: Path) -> None:
    records = [
        "ACME  00000001",
        "BETA  00000002",
        "SHORT",
        "DELTA  0000004",
        "ECHO  00000005",
    ]
    path = _write(tmp_path / "ragged.txt", records)

    with pytest.raises(Unparseable) as caught:
        infer_layout(path)

    message = str(caught.value)
    assert "Line 3" in message, message
    assert "14" in message, "the expected width belongs in the message"


def test_a_trailer_of_a_different_width_is_not_mistaken_for_a_ragged_record(
    tmp_path: Path,
) -> None:
    records = [f"ACME  {index:08d}" for index in range(1, 31)]
    records.append("TRLR000030")
    path = _write(tmp_path / "trailer.txt", records)

    layout = infer_layout(path)

    assert layout.has_trailer is True
    assert layout.record_width == 14
    assert layout.evidence.records_sampled == 30


def test_an_empty_file_is_rejected_rather_than_returning_a_zero_field_layout(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "empty.txt", [])
    with pytest.raises(Unparseable):
        infer_layout(path)


def test_one_enormous_line_is_rejected_before_it_becomes_a_histogram(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "huge.txt", ["x" * 40_000])
    with pytest.raises(Unparseable) as caught:
        infer_layout(path)
    assert "one long line" in str(caught.value)


def test_a_blank_line_is_skipped_without_shifting_the_reported_line_number(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path / "blanks.txt",
        ["ACME  00000001", "", "BETA  00000002", "SHORT", "DELTA 00000004"],
    )
    with pytest.raises(Unparseable) as caught:
        infer_layout(path)
    assert "Line 4" in str(caught.value), "the blank line still counts as a line"
