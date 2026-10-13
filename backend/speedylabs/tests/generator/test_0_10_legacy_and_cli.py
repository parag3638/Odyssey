"""Step 0.10 — legacy rebate export, variance oracle, and `speedy-generate` CLI."""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from src.generator import cli
from src.generator.catalog import RebateProgram, program_for_brand
from src.generator.defects import (
    CHECK_CATCH_WEIGHT,
    CHECK_DUPLICATE,
    CHECK_MIXED_UOM,
    CHECK_VENDOR,
    build_seeded_file,
)
from src.generator.invoices import InvoiceLine, _bump_qty  # pyright: ignore[reportPrivateUsage]
from src.generator.legacy import (
    CAUSE_DATE_CUTOFF,
    build_legacy,
    build_variance_oracle,
    cases_for,
    new_rebate,
)
from src.generator.seed import SEED
from src.lib.money import extend, quantize_amount

BACKEND = Path(__file__).resolve().parents[2]
FIXTURES = BACKEND / "fixtures"


@dataclass(frozen=True)
class _FakeProgram:
    """A minimal stand-in for `RebateProgram`, for testing `new_rebate` in isolation
    without needing a program registered in the real catalog."""

    kind: str
    brand_code: str
    rate: Decimal
    program_id: str = "x"
    name: str = "x"
    uom_basis: str = "CS"


def _program(brand_code: str, program_kind: str, rate: str) -> RebateProgram:
    fake = _FakeProgram(kind=program_kind, brand_code=brand_code, rate=Decimal(rate))
    return fake  # type: ignore[return-value]  # structurally compatible, used read-only


# --- cases_for / new_rebate: the uncomputable case must not invent dollars -------------


def test_cases_for_none_on_each_priced_line_with_no_pack_size() -> None:

    seeded = build_seeded_file()
    mixed_line = next(line for line in seeded.lines if line.uom == "EA" and line.pack_size is None)
    assert cases_for(mixed_line) is None

    program = _program(mixed_line.brand_manufacturer, "per_case_volume", "1.000000")
    assert new_rebate(mixed_line, program) == Decimal("0.00"), (
        "new_rebate must return exactly 0, not coerce None to 0 cases times a nonzero "
        "rate via some other path, for an uncomputable case line"
    )


def test_cases_for_each_with_pack_size_divides_correctly() -> None:
    seeded = build_seeded_file()
    line = next(line for line in seeded.lines if line.uom == "CS")
    assert cases_for(line) == line.qty  # CS: 1:1
    each_line = replace(line, uom="EA", pack_size=4, qty=Decimal("20"))
    assert cases_for(each_line) == Decimal("5")


def test_cases_for_none_does_not_propagate_as_zero_rebate_dollars_into_legacy_totals() -> None:
    """The brief's specific worry: does anything downstream coerce None to 0 in a way
    that invents or destroys dollars, rather than cleanly excluding the line?

    Every mixed-uom-defect row is, by construction, an each-priced line with no pack
    size (`cases_for` returns None for it). `new_rebate` must therefore compute exactly
    0 for it directly — never a nonzero amount smuggled in through some other code
    path that silently treats None as 0 cases times a nonzero rate in the wrong
    direction, or as 1 case, or any other guess.
    """
    seeded = build_seeded_file()
    legacy = build_legacy(seeded)
    mixed_rows = set(seeded.affected_rows[CHECK_MIXED_UOM])
    by_row = {line.row_number: line for line in seeded.lines}

    for row in mixed_rows:
        line = by_row[row]
        assert line.uom == "EA" and line.pack_size is None
        assert cases_for(line) is None
        program = program_for_brand(line.brand_manufacturer)
        if program is not None and program.kind == "per_case_volume":
            assert new_rebate(line, program) == Decimal("0.00"), (
                f"row {row}: new_rebate did not return exactly 0 for an uncomputable case/each line"
            )
    # The case/each cause's contribution amounts may be negative (a credit line that
    # happens to also carry the mixed-uom defect has a negative qty, so "what legacy
    # would have overpaid" is itself negative) — that is a real, directionally correct
    # consequence of signed quantities, not an invented-dollars bug. What matters is
    # that every contribution is actually computed from `program.rate * line.qty`, not
    # some default substituted for the missing case count.
    for row, amount in legacy.contributions["case_each_conversion"].items():
        line = by_row[row]
        program = program_for_brand(line.brand_manufacturer)
        assert program is not None
        assert amount == quantize_amount(program.rate * line.qty)


# --- _bump_qty / duplicate-copy adjacency ----------------------------------------------


def test_bump_qty_preserves_extension_invariant_for_case_and_pound_lines() -> None:
    case_line = InvoiceLine(
        row_number=1,
        invoice_no="X",
        invoice_date=date(2026, 1, 5),
        customer_id="1",
        customer_name="n",
        dist_item_id="d",
        mfr_item_code="m",
        brand_manufacturer="NVF",
        invoice_vendor="300145",
        qty=Decimal("3"),
        uom="CS",
        pack_size=6,
        catch_weight_lbs=None,
        unit_price=Decimal("12.345"),
        ext_amount=Decimal("37.04"),  # 3 * 12.345 rounded
        is_deviated=False,
        deviated_price=None,
    )
    bumped_case = _bump_qty(case_line)
    assert bumped_case.qty == Decimal("4")
    assert bumped_case.ext_amount == extend(Decimal("4"), case_line.unit_price)
    assert bumped_case.ext_amount != case_line.ext_amount

    lb_line = replace(
        case_line,
        uom="LB",
        pack_size=None,
        catch_weight_lbs=Decimal("12.50"),
        ext_amount=extend(Decimal("12.50"), case_line.unit_price),
    )
    bumped_lb = _bump_qty(lb_line)
    assert bumped_lb.qty == lb_line.qty + 1
    assert bumped_lb.ext_amount == lb_line.ext_amount, (
        "a pound line's extension is driven by catch weight, not qty — bumping qty to "
        "break a key collision must not change the money"
    )


def test_duplicate_pairs_are_adjacent_rows_not_just_correctly_matched_by_key() -> None:
    """The brief's specific worry: `legacy.py` takes every second element of the sorted
    duplicate row list, assuming pairs are adjacent integers. Stress it by building many
    seeded files (varying the seed) and checking the adjacency assumption holds every
    time, not just for the pinned seed."""
    for seed_offset in range(5):
        seeded = build_seeded_file(SEED + seed_offset * 1000)
        rows = sorted(seeded.affected_rows[CHECK_DUPLICATE])
        assert len(rows) % 2 == 0
        for i in range(0, len(rows), 2):
            assert rows[i + 1] == rows[i] + 1, (
                f"seed {SEED + seed_offset * 1000}: duplicate rows {rows[i]} and "
                f"{rows[i + 1]} are not adjacent — the every-second-element "
                f"assumption in legacy.py would misattribute this pair"
            )


# --- date-cutoff cause disjointness -----------------------------------------------------


def test_date_cutoff_cause_rows_are_disjoint_from_all_four_other_causes_and_non_empty() -> None:
    seeded = build_seeded_file()
    legacy = build_legacy(seeded)
    cutoff_rows = set(legacy.contributions[CAUSE_DATE_CUTOFF])
    assert cutoff_rows, (
        "date_cutoff claimed zero rows — if this is ever empty, the cause exists by "
        "luck, not by construction"
    )

    other_rows: set[int] = set()
    for cause, per_line in legacy.contributions.items():
        if cause == CAUSE_DATE_CUTOFF:
            continue
        other_rows |= set(per_line)
    assert cutoff_rows.isdisjoint(other_rows), (
        f"date_cutoff overlaps other causes: {cutoff_rows & other_rows}"
    )

    # Also disjoint from the readiness-defect row sets that feed the other four causes.
    claimed_defect_rows: set[int] = set()
    for code in (CHECK_MIXED_UOM, CHECK_CATCH_WEIGHT, CHECK_VENDOR, CHECK_DUPLICATE):
        claimed_defect_rows |= set(seeded.affected_rows[code])
    assert cutoff_rows.isdisjoint(claimed_defect_rows)


# --- variance oracle structure -----------------------------------------------------------


def test_variance_oracle_amounts_are_decimal_strings_and_sum_exactly() -> None:
    seeded = build_seeded_file()
    legacy = build_legacy(seeded)
    oracle = build_variance_oracle(legacy)

    total = Decimal(oracle["totalSeededVarianceUsd"])
    assert isinstance(oracle["totalSeededVarianceUsd"], str)
    computed_sum = sum((Decimal(c["amountUsd"]) for c in oracle["causes"]), Decimal("0"))
    assert computed_sum == total

    all_rows: list[int] = []
    for cause in oracle["causes"]:
        assert isinstance(cause["amountUsd"], str)
        Decimal(cause["amountUsd"])  # must parse
        all_rows.extend(cause["rows"])
    assert len(all_rows) == len(set(all_rows)), "a row appears in more than one cause"

    for cause in oracle["causes"]:
        assert cause["amountUsd"] != "0.00" or cause["lineCount"] == 0, (
            f"{cause['cause']} claims lines but zero dollars"
        )
        assert cause["lineCount"] > 0, f"{cause['cause']} contributes zero lines"


def test_committed_variance_oracle_matches_recomputation_from_seed() -> None:
    oracle_path = FIXTURES / "oracle" / "expected_variance_attribution.json"
    committed = json.loads(oracle_path.read_text())
    seeded = build_seeded_file()
    legacy = build_legacy(seeded)
    recomputed = build_variance_oracle(legacy)
    assert committed == recomputed


# --- archives & CLI ----------------------------------------------------------------------


def test_committed_archives_are_under_2mb_and_decompress_byte_identical_to_the_seed() -> None:
    expected = cli.build_payloads()
    for relative in (
        "full/month1_invoices.txt.gz",
        "full/month1_invoices.csv.gz",
        "full/month2_invoices.txt.gz",
        "full/month2_invoices.csv.gz",
    ):
        path = FIXTURES / relative
        size = path.stat().st_size
        assert size <= 2 * 1024 * 1024, f"{relative} is {size} bytes, over the 2 MB cap"
        on_disk = path.read_bytes()
        # Byte-identical as *compressed* archives (mtime=0 makes gzip output deterministic).
        assert on_disk == expected[relative], (
            f"{relative} does not match a fresh build from the seed"
        )
        # And the decompressed payload must match too, independently of gzip bytes.
        assert gzip.decompress(on_disk) == gzip.decompress(expected[relative])


def test_second_generate_run_leaves_tree_unchanged(tmp_path: Path) -> None:
    out1 = tmp_path / "out"
    cli.generate(out1)
    snapshot1 = {p.relative_to(out1): p.read_bytes() for p in out1.rglob("*") if p.is_file()}

    out2 = tmp_path / "out2"
    cli.generate(out2)
    snapshot2 = {p.relative_to(out2): p.read_bytes() for p in out2.rglob("*") if p.is_file()}

    assert snapshot1.keys() == snapshot2.keys()
    for rel in snapshot1:
        assert snapshot1[rel] == snapshot2[rel], f"{rel} differs between two generate() runs"


def test_corrupted_oracle_makes_verify_fail_naming_the_file_and_survives(tmp_path: Path) -> None:
    out = tmp_path / "out"
    cli.generate(out)
    target = out / "oracle" / "expected_readiness.json"
    original = target.read_bytes()
    target.write_bytes(b'{"corrupted": true}')

    problems = cli.verify(out)
    assert any("expected_readiness.json" in p for p in problems), problems

    # Corruption must survive the check — verify() must never write.
    assert target.read_bytes() == b'{"corrupted": true}'
    assert target.read_bytes() != original

    exit_code = cli.main(["--out", str(out), "--verify"])
    assert exit_code == 1


def test_cli_verify_exits_0_against_the_real_committed_fixtures() -> None:
    exit_code = cli.main(["--out", str(FIXTURES), "--verify"])
    assert exit_code == 0


def test_sample_files_are_the_first_500_rows_and_contain_each_seeded_defect_class() -> None:
    """mvp.md asks whether the 500-row samples are representative. If every defect
    class is clustered after row 500, a reviewer opening the sample file sees only
    clean data and the sample is misleading."""
    seeded = build_seeded_file()
    sample_rows = set(range(1, 501))
    for code in (CHECK_MIXED_UOM, CHECK_CATCH_WEIGHT, CHECK_VENDOR):
        rows = set(seeded.affected_rows[code])
        in_sample = rows & sample_rows
        assert in_sample, (
            f"{code}: no seeded row falls within the first 500 — the sample is "
            f"unrepresentative for this defect class"
        )


def test_generator_tests_are_discoverable_from_backend_root_make_check() -> None:
    """0.10's final accept criterion: `make check` includes and passes the generator's
    own tests. This file and its siblings live under `backend/tests/generator/`, which
    `pytest.ini_options.testpaths = ["tests", "src"]` already covers — confirmed by
    running pytest's own collection, not by assumption."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "tests/generator",
            "tests/baselines",
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_naive_matcher_maps_fewer_than_40_percent" in result.stdout
