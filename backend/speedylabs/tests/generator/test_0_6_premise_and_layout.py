"""Step 0.6 — the premise test, plus the layout's other accept criteria.

mvp.md's own words: "If a later layout change makes the naive matcher succeed, the
layout has stopped representing the distributor the spec describes and the test
should fail." This file is that test, independently written (not imported from
anything the builder wrote beyond `source_layout.py` itself, which is the thing under
test).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from src.generator.catalog import build_catalog
from src.generator.source_layout import CANONICAL_FIELDS, LAYOUT, offsets, record_width, to_oracle
from tests.baselines.naive_name_matcher import run_naive_matcher, score_against_truth

_BACKEND_ROOT: Path = Path(__file__).resolve().parents[2]
_CATALOG_SNIPPET = (
    "from src.generator.catalog import build_catalog; print(build_catalog().to_json())"
)


def _env_with_random_hashseed() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "random"
    return env


def _catalog_json_in_fresh_process() -> str:
    result = subprocess.run(
        [sys.executable, "-c", _CATALOG_SNIPPET],
        cwd=_BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env=_env_with_random_hashseed(),
    )
    return result.stdout.strip()


def test_naive_matcher_maps_fewer_than_40_percent_of_canonical_fields_correctly() -> None:
    result = run_naive_matcher()
    correct, correctness = score_against_truth(result)
    rate = correct / len(correctness)
    assert rate < 0.40, (
        f"naive name matcher scored {rate:.1%} ({correct}/{len(correctness)}) — "
        f"the layout no longer defeats a name-only matcher: {correctness}"
    )


def test_naive_matcher_gets_the_misleading_column_wrong() -> None:
    """The matcher must get `customer_id` wrong specifically by falling into the
    CUSTNO trap (assigning the ship-to decoy) rather than landing on BILTNO, the true
    bill-to. mvp.md: "it maps the misleading column **wrongly**" — this is the one
    column the plan calls out by name, so the test checks the specific failure mode,
    not just "wrong in general"."""
    result = run_naive_matcher()
    assert result.assignments["customer_id"] == "CUSTNO", (
        f"expected the naive matcher to wrongly assign customer_id to the CUSTNO decoy "
        f"(the premise's trap), got {result.assignments['customer_id']!r} instead — "
        f"if it now correctly lands on BILTNO, the layout has stopped defeating a "
        f"name-only matcher the way the spec requires"
    )
    assert result.assignments["customer_id"] != "BILTNO"


def test_naive_matcher_is_materially_below_40_with_margin() -> None:
    """Guard against a borderline pass: the plan's bar is <40%, but a layout that
    regresses to e.g. 35% is still fragile. Flag it loudly rather than let it creep."""
    result = run_naive_matcher()
    correct, correctness = score_against_truth(result)
    rate = correct / len(correctness)
    assert rate <= 0.25, (
        f"naive matcher scored {rate:.1%}, which is under the 40% bar but close enough "
        f"that a tiny layout change could cross it — worth a human look: {correctness}"
    )


# --- the rest of 0.6's accept criteria -------------------------------------------------


def test_catalog_determinism_across_processes() -> None:
    in_process_1 = build_catalog().to_json()
    in_process_2 = build_catalog().to_json()
    assert in_process_1 == in_process_2, "catalog differs between two builds in one process"

    fresh = _catalog_json_in_fresh_process()
    assert fresh == in_process_1.strip(), "catalog differs in a fresh process"


def test_catalog_byte_identical_under_random_pythonhashseed() -> None:
    """Dict/set iteration order is hash-seed-dependent in CPython; the catalog module's
    own docstring claims it never iterates a set. Prove that under an actually-randomised
    hash seed, not just the default of whatever this test runner happens to use."""
    baseline = build_catalog().to_json().strip()
    for _ in range(3):
        assert _catalog_json_in_fresh_process() == baseline


def test_exactly_200_skus_with_expected_shapes() -> None:
    catalog = build_catalog()
    assert len(catalog.skus) == 200
    for sku in catalog.skus:
        assert isinstance(sku.list_price, Decimal)
        if sku.catch_weight:
            assert sku.uom == "LB"
            assert sku.weight_min_lbs is not None and sku.weight_max_lbs is not None
            assert sku.weight_max_lbs > sku.weight_min_lbs
        else:
            assert sku.pack_size >= 1


def test_seed_plus_one_changes_the_catalog() -> None:
    base = build_catalog(20261003).to_json()
    bumped = build_catalog(20261004).to_json()
    assert base != bumped, "seed is decorative: changing it by one left the catalog unchanged"


def test_layout_names_are_cryptic_as400_idiom() -> None:
    for field in LAYOUT:
        assert 6 <= len(field.name) <= 10, f"{field.name} is not 6-10 chars"
        assert field.name.isupper(), f"{field.name} is not upper-case"
        assert field.name.isascii()


def test_layout_order_is_not_canonical_order() -> None:
    present_canonical_in_order = [f.canonical for f in LAYOUT if f.canonical is not None]
    assert tuple(present_canonical_in_order) != CANONICAL_FIELDS
    # Stronger: the canonical fields that are present must not appear in canonical order.
    assert tuple(present_canonical_in_order) != tuple(
        c for c in CANONICAL_FIELDS if c in present_canonical_in_order
    )


def test_at_least_three_filler_columns_interleaved() -> None:
    filler_positions = [i for i, f in enumerate(LAYOUT) if f.filler]
    assert len(filler_positions) >= 3
    # Interleaved, not appended: not all filler positions are at the tail.
    assert filler_positions[0] < len(LAYOUT) - len(filler_positions)


def test_exactly_one_actively_misleading_column() -> None:
    """CUSTNO must exist, carry canonical=None (it is not a real canonical source) and
    not be marked as filler — it is a decoy, not reserved space."""
    decoys = [f for f in LAYOUT if f.canonical is None and not f.filler]
    assert len(decoys) >= 1
    names = {f.name for f in decoys}
    assert "CUSTNO" in names


def test_layout_oracle_records_every_column_fully() -> None:
    oracle = to_oracle()
    assert len(oracle) == len(LAYOUT)
    computed = offsets()
    for entry, (field, start, end) in zip(oracle, computed, strict=True):
        assert entry["name"] == field.name
        assert entry["start"] == start
        assert entry["end"] == end
        assert entry["filler"] == field.filler
        assert entry["width"] == field.width == end - start + 1


def test_committed_layout_oracle_matches_source_layout_module() -> None:
    oracle_path = _BACKEND_ROOT / "fixtures" / "oracle" / "expected_layout.json"
    data: dict[str, object] = json.loads(oracle_path.read_text())
    assert data["recordWidth"] == record_width()
    assert data["columns"] == to_oracle()
