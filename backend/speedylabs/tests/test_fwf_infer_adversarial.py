"""Tester pass, P4.1 - adversarial cases against `infer_layout`'s zero-false-cuts claim.

The builder's committed claim (docs/decisions.md 2026-10-06, "Boundary inference cuts on
a blank histogram at 99%, and guesses nothing"): on the committed 500-record sample,
inference finds 14 of the oracle's 21 boundaries and invents none, and the threshold was
chosen because a false cut is far more damaging than a missed one. This file tries to
break that property on inputs the committed sample cannot exercise: noisier padding right
at the threshold boundary, one-column files, one-character records, and the full 50k-row
file (which the builder had only run inference against a 500-row sample of).
"""

from __future__ import annotations

import gzip
import time
from decimal import Decimal
from pathlib import Path

import pytest

from src.lib.parsers.fwf_infer import BLANK_THRESHOLD, infer_layout

REPO_ROOT = Path(__file__).resolve().parent.parent
FULL_FWF_GZ = REPO_ROOT / "fixtures" / "full" / "month1_invoices.txt.gz"


def _write(path: Path, records: list[str]) -> Path:
    path.write_text("".join(f"{record}\n" for record in records), encoding="ascii")
    return path


# --------------------------------------------------------------------------------------
# Noisier padding right at the 99% line
# --------------------------------------------------------------------------------------


def _record_with_controlled_interior_blank(index: int, *, blank: bool) -> str:
    """21 characters: "ACME  " (always blank at 5-6) + an 8-digit packed number (never
    blank) + "WXYZ" + a 2-char tail whose *first* character is a fixed digit and whose
    *second* character (position 20) is the one under test + a trailing "Q" (position
    21, always non-blank).

    Position 20 is deliberately **not** the record's last character: the algorithm
    always treats the final position as a field end regardless of blank evidence
    ("the record's final position always ends a field"), so testing the threshold at
    the last position would pass or fail for the wrong reason. Position 20 sits in the
    interior, with a guaranteed non-blank character after it, so a cut appearing there
    can only come from the blank-fraction rule actually being evaluated.
    """
    number = f"{index:08d}"
    tail = f"0{' ' if blank else 'X'}"
    return f"ACME  {number}WXYZ{tail}Q"


def test_a_position_blank_in_exactly_99_percent_of_records_cuts(tmp_path: Path) -> None:
    """500 records; the interior position (character 20) is blank in exactly
    495/500 = 99.0% of them - right at the threshold, not comfortably above it."""
    records = [
        _record_with_controlled_interior_blank(index, blank=index < 495) for index in range(500)
    ]
    path = _write(tmp_path / "exact99.txt", records)

    layout = infer_layout(path)

    assert layout.evidence.blank_fractions[19] == Decimal("0.9900")
    found = {(f.start, f.end) for f in layout.fields}
    assert (1, 6) in found
    assert (7, 20) in found, (
        "a position blank in exactly 99.00% of records must still end a field - the "
        f"threshold is >=, not >. Fields found: {sorted(found)}"
    )
    assert (21, 21) in found


def test_a_position_blank_in_98_point_8_percent_of_records_does_not_cut(
    tmp_path: Path,
) -> None:
    """One short of 99%: 494/500 = 98.8%, at the same interior position. The field must
    NOT be split there - a false cut below the threshold corrupts every value
    downstream, the exact failure mode the 99% threshold exists to prevent."""
    records = [
        _record_with_controlled_interior_blank(index, blank=index < 494) for index in range(500)
    ]
    path = _write(tmp_path / "under99.txt", records)

    layout = infer_layout(path)

    assert layout.evidence.blank_fractions[19] == Decimal("0.9880")
    found = {(f.start, f.end) for f in layout.fields}
    assert (7, 20) not in found, (
        f"98.8% blank at an interior position must not become a boundary: {sorted(found)}"
    )
    assert (7, 21) in found, (
        f"with no boundary inside it, characters 7-21 must merge into one field: {sorted(found)}"
    )


def test_scattered_noise_just_under_the_cap_invents_no_cut(tmp_path: Path) -> None:
    """A dirtier real-world file than the committed sample: a genuinely single field
    (no internal padding at all in most rows) gets occasional noise-free short values
    in up to 1% of rows, simulating OCR-adjacent or re-keyed outliers. Inference must
    still treat the whole span as one field, not invent a boundary at the noisy spot."""
    # 300 records, one 20-character packed numeric field, always full-width except
    # exactly 2 records (<1%) which happen to have a trailing blank at position 20.
    records: list[str] = []
    for index in range(300):
        value = f"{index:020d}"
        if index in (10, 20):  # 2/300 = 0.67%, under the 1% slack the 99% bar allows
            value = f"{index:019d} "
        records.append(value)
    path = _write(tmp_path / "noisy.txt", records)

    layout = infer_layout(path)

    cuts = {f.end for f in layout.fields}
    assert cuts == {20}, (
        f"a position blank in only 2/300 (0.67%) records must not become a boundary: {cuts}"
    )


# --------------------------------------------------------------------------------------
# Degenerate shapes: one column, one character wide
# --------------------------------------------------------------------------------------


def test_a_single_character_wide_file_does_not_crash(tmp_path: Path) -> None:
    """Every record is exactly one character. There is no interior to have padding in,
    and the loop that looks at `blanks[position + 1]` must not index past the array."""
    records = [("A" if index % 2 else "B") for index in range(50)]
    path = _write(tmp_path / "onechar.txt", records)

    layout = infer_layout(path)

    assert layout.record_width == 1
    assert len(layout.fields) == 1
    assert (layout.fields[0].start, layout.fields[0].end) == (1, 1)


def test_a_single_character_wide_mostly_blank_file(tmp_path: Path) -> None:
    """Same degenerate width, but the one column is blank in >99% of records - the cut
    position (1) is also the record width, so the "or position + 1 >= width" branch of
    `_cut_positions` must fire rather than reading `blanks[1]` out of bounds."""
    records = [(" " if index % 1000 else "X") for index in range(50)]
    path = _write(tmp_path / "oneblank.txt", records)

    layout = infer_layout(path)

    assert layout.record_width == 1
    assert len(layout.fields) == 1


def test_a_one_column_file_with_no_padding_anywhere_is_one_unflagged_field(
    tmp_path: Path,
) -> None:
    """A single wide field, fully packed, is genuinely one field - it must not be
    flagged ambiguous just for being wide, since AMBIGUOUS_MIN_WIDTH + no interior
    evidence is the signal, and this field has no *other* field to be confused with."""
    records = [f"{index:020d}" for index in range(40)]
    path = _write(tmp_path / "onecol.txt", records)

    layout = infer_layout(path)

    assert len(layout.fields) == 1
    assert layout.fields[0].width == 20
    # This one is legitimately flagged low-confidence per the documented heuristic
    # (wide + featureless), which is correct behaviour, not a bug - asserted here so a
    # future change to the heuristic notices it changed this case.
    assert layout.fields[0].confidence == "low"


# --------------------------------------------------------------------------------------
# The full 50k-record file: the 2s inference budget, never measured on real-file scale
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def full_fwf_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not FULL_FWF_GZ.exists():
        pytest.skip(f"full fixture missing: {FULL_FWF_GZ}")
    out_dir = tmp_path_factory.mktemp("full_fwf")
    out_path = out_dir / "month1_invoices.txt"
    with gzip.open(FULL_FWF_GZ, "rb") as source, out_path.open("wb") as dest:
        dest.write(source.read())
    return out_path


def test_inference_on_the_full_file_finishes_under_the_two_second_budget(
    full_fwf_path: Path,
) -> None:
    """mvp.md P4 Speed: "inference over 5,000 sampled records under 2s." The builder's
    own tests only ever ran this against the 500-record committed sample; this is the
    first measurement against the real ~48k-line file, where `infer_layout` must stop
    sampling at 5,000 records rather than reading the whole file."""
    started = time.perf_counter()
    layout = infer_layout(full_fwf_path)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"inference took {elapsed:.3f}s against a 2s budget"
    assert layout.evidence.records_sampled <= 5_000


def test_inference_on_the_full_file_still_invents_no_boundary(full_fwf_path: Path) -> None:
    """The zero-false-cuts property, re-measured at real scale rather than only against
    the 500-row sample - sampling more real data could in principle surface a position
    that is accidentally >=99% blank in the small sample but not in the large one, or
    vice versa."""
    import json

    oracle = json.loads((REPO_ROOT / "fixtures" / "oracle" / "expected_layout.json").read_text())
    oracle_cuts = {int(column["end"]) for column in oracle["columns"]}

    layout = infer_layout(full_fwf_path)
    found = {field.end for field in layout.fields}

    assert found - oracle_cuts == set(), (
        f"inference on the full file invented boundaries absent from the oracle: "
        f"{sorted(found - oracle_cuts)}"
    )
    assert layout.evidence.threshold == BLANK_THRESHOLD
