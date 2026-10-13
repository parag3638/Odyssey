"""Step 11.3's load-bearing test: the production `ReferenceRebateEngine` and
`src/generator/legacy.py::new_rebate` agree on **every** line of the full 48,150-row seeded month -
not a sample - and the production run's totals equal the committed
`fixtures/oracle/expected_rebate_calcs.json` exactly.

Why this is the single most important test in the phase (the plan's own words): the oracle is
built from the **generator's** arithmetic, independently of `features/rebates`. If the two ever
disagreed, `fixtures/oracle/expected_variance_attribution.json`'s committed `$7,376.08` would stop
meaning what it says, and P13 would be reconciling against a number nobody could explain.

Needs the full 48,150-row month materialised *and calculated* first. `full_calculated_run`
depends on the `seeded_demo` fixture (`tests/conftest.py`), which resets and fully re-seeds the
demo once per test session rather than assuming a previous run left it behind - see that
fixture's docstring.

This file reads the database and the generator's pure arithmetic. It never imports
`features/rebates` and the generator in the same comparison function without labelling which side
is which - the whole point is that the two sides are independent.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row

from src.features.rebates.engine import RebateLine, RebateProgramInput
from src.features.rebates.reference_engine import ReferenceRebateEngine
from src.generator.catalog import MANUFACTURERS, PROGRAMS, RebateProgram
from src.generator.legacy import basis_for, new_rebate
from src.lib.manufacturers import lookup_from_rows
from src.lib.money import quantize_amount
from tests.live import APP_DB_URL, DEMO_SLUG

ORACLE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "oracle" / "expected_rebate_calcs.json"
)

#: name -> the catalog's RebateProgram for that manufacturer, built once. Independent of
#: `features/rebates`: this is the generator's own vocabulary (brand *codes*), resolved to the
#: manufacturer *name* the way the production FK requires, so a canonical row's display-name
#: spelling can be looked up directly without re-deriving `customer_manufacturers`.
_PROGRAM_BY_NAME: dict[str, RebateProgram] = {
    manufacturer.name: program
    for manufacturer in MANUFACTURERS
    for program in PROGRAMS
    if program.brand_code == manufacturer.code
}


@dataclass(frozen=True)
class _ShimLine:
    """Just enough of `InvoiceLine` for `legacy.basis_for` / `new_rebate` to read: `uom`,
    `qty`, `pack_size`, `catch_weight_lbs`. Built straight from a `canonical_lines` row rather
    than from the generator's own `InvoiceLine`, so this test needs no row-number correlation
    between the materialised table and the generator's in-memory file order."""

    uom: str
    qty: Decimal
    pack_size: int | None
    catch_weight_lbs: Decimal | None


def _expected_status(line: _ShimLine, program: RebateProgram) -> str:
    """A third, independent restatement of the basis-scope rule (`basis_for`'s doc, mirrored
    without importing it): neither `src.generator.rebates._status_of` (private, and the oracle's
    own) nor `features/rebates/reference_engine.py::_basis` (the module under test) is reused
    here, so a bug shared by both of *those* would still be caught by this test disagreeing."""
    if program.uom_basis == "CS":
        if line.uom == "CS":
            return "contributed"
        if line.uom == "EA":
            return "skipped" if line.pack_size in (None, 0) else "contributed"
        return "out_of_basis_scope"
    if line.uom == "LB":
        return "skipped" if line.catch_weight_lbs is None else "contributed"
    return "out_of_basis_scope"


def _find_full_month1_run() -> tuple[str, str] | None:
    with psycopg.connect(APP_DB_URL, autocommit=True) as conn:
        row = conn.execute(
            """
            select r.id, r.customer_id
            from ingest_runs r
            join customers c on c.id = r.customer_id
            where c.slug = %s and r.calculated_at is not null
            order by r.created_at desc
            limit 1
            """,
            (DEMO_SLUG,),
        ).fetchone()
    if row is None:
        return None
    return str(row[0]), str(row[1])


@pytest.fixture(scope="module")
def full_calculated_run(seeded_demo: None) -> tuple[str, str]:
    del seeded_demo
    found = _find_full_month1_run()
    if found is None:
        # Defensive only: the seeded_demo fixture should already have made this true, or
        # skipped the test itself (reset disabled / stack unreachable) before this fixture ran.
        pytest.skip(
            "no calculated demo run found even after the seeded_demo fixture ran - see its "
            "docstring in tests/conftest.py"
        )
    return found


@pytest.fixture(scope="module")
def live_conn() -> Iterator[psycopg.Connection[Any]]:
    conn = psycopg.connect(APP_DB_URL, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def _load_lines(conn: psycopg.Connection[Any], run_id: str) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select id, invoice_date, brand_manufacturer, uom, qty, pack_size, catch_weight_lbs "
            "from canonical_lines where ingest_run_id = %s order by source_row_number",
            (run_id,),
        )
        return cur.fetchall()


def _load_programs(conn: psycopg.Connection[Any], customer_id: str) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select id, external_ref, name, kind, uom_basis, rate, effective_from, effective_to, "
            "brand_manufacturer from rebate_programs where customer_id = %s order by external_ref",
            (customer_id,),
        )
        return cur.fetchall()


def _load_reference(conn: psycopg.Connection[Any], customer_id: str) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select name, brand_values, direct_vendor_values, redistributor_only "
            "from customer_manufacturers where customer_id = %s",
            (customer_id,),
        )
        return cur.fetchall()


def _engine_results_by_line(
    lines: list[dict[str, Any]],
    programs: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
) -> dict[UUID, Any]:
    """Run the production engine over every line at once and index its results by line id.

    The committed fixture's catalog gives each brand exactly one program, so each line gets at
    most one result; a second result for the same line would be a real defect this test should
    catch, not paper over, so a collision raises instead of silently keeping the last one.
    """
    lookup = lookup_from_rows(reference_rows)
    engine_programs: list[RebateProgramInput] = []
    for program in programs:
        entry = lookup.find(str(program["brand_manufacturer"]))
        spellings = (
            entry.brand_values if entry is not None else (str(program["brand_manufacturer"]),)
        )
        engine_programs.append(
            RebateProgramInput(
                program_id=program["id"],
                kind=str(program["kind"]),
                uom_basis=str(program["uom_basis"]),
                rate=Decimal(program["rate"]),
                brand_values=tuple(spellings),
                effective_from=program["effective_from"],
                effective_to=program["effective_to"],
            )
        )
    engine_lines = [
        RebateLine(
            canonical_line_id=row["id"],
            invoice_date=row["invoice_date"],
            brand_manufacturer=str(row["brand_manufacturer"]),
            uom=str(row["uom"]),
            qty=Decimal(row["qty"]),
            pack_size=None if row["pack_size"] is None else int(row["pack_size"]),
            catch_weight_lbs=None
            if row["catch_weight_lbs"] is None
            else Decimal(row["catch_weight_lbs"]),
        )
        for row in lines
    ]
    results = ReferenceRebateEngine().calculate(engine_lines, engine_programs)
    by_line: dict[UUID, Any] = {}
    for result in results:
        assert result.canonical_line_id not in by_line, (
            f"line {result.canonical_line_id} matched more than one program - the committed "
            "fixture's one-brand-one-program assumption no longer holds"
        )
        by_line[result.canonical_line_id] = result
    return by_line


def test_production_engine_matches_legacy_new_rebate_on_every_one_of_48150_lines(
    full_calculated_run: tuple[str, str], live_conn: psycopg.Connection[Any]
) -> None:
    """The load-bearing comparison: not sampled, all of them. A mismatch prints the row,
    the brand, the uom and both amounts, per the plan's own acceptance criterion."""
    run_id, customer_id = full_calculated_run
    lines = _load_lines(live_conn, run_id)
    programs = _load_programs(live_conn, customer_id)
    reference_rows = _load_reference(live_conn, customer_id)
    assert len(lines) == 48_150, f"expected the full 48,150-row month, got {len(lines)}"

    engine_by_line = _engine_results_by_line(lines, programs, reference_rows)

    mismatches: list[str] = []
    for row in lines:
        program = _PROGRAM_BY_NAME.get(str(row["brand_manufacturer"]))
        assert program is not None, (
            f"row {row['id']} has brand {row['brand_manufacturer']!r} with no catalog program - "
            "the committed fixture is supposed to give every brand exactly one program"
        )
        shim = _ShimLine(
            uom=str(row["uom"]),
            qty=Decimal(row["qty"]),
            pack_size=None if row["pack_size"] is None else int(row["pack_size"]),
            catch_weight_lbs=None
            if row["catch_weight_lbs"] is None
            else Decimal(row["catch_weight_lbs"]),
        )
        expected_amount = new_rebate(shim, program)  # type: ignore[arg-type]
        expected_status = _expected_status(shim, program)

        engine_result = engine_by_line.get(row["id"])
        actual_amount = (
            Decimal("0.00") if engine_result is None else engine_result.amount_contribution
        )
        actual_status = "contributed" if engine_result is None else engine_result.line_status

        if actual_amount != expected_amount or actual_status != expected_status:
            mismatches.append(
                f"row={row['id']} brand={row['brand_manufacturer']!r} uom={row['uom']!r} "
                f"expected_amount={expected_amount} actual_amount={actual_amount} "
                f"expected_status={expected_status} actual_status={actual_status}"
            )

    assert not mismatches, (
        f"{len(mismatches)} of {len(lines)} lines disagree between the production engine and "
        f"legacy.new_rebate:\n" + "\n".join(mismatches[:20])
    )


def test_production_run_totals_equal_the_committed_oracle_exactly(
    full_calculated_run: tuple[str, str], live_conn: psycopg.Connection[Any]
) -> None:
    """`fixtures/oracle/expected_rebate_calcs.json` is committed and pinned: this is the file
    `expected_variance_attribution.json`'s $7,376.08 is measured against, and this test proves
    the real, materialised, API-triggered calculation reproduces it from the database, not just
    in the pure engine layer (11.3's own "checked end to end, not just in the pure layer")."""
    run_id, _ = full_calculated_run
    oracle = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))
    assert oracle["totalAmountUsd"] == "387648.02"
    assert oracle["calcLineCount"] == 48_150
    assert oracle["calcCount"] == 18

    with live_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select p.external_ref, c.period_start, c.period_end, c.basis_qty, c.amount,
                   c.contributed_line_count, c.skipped_line_count, c.out_of_scope_line_count
            from rebate_calcs c join rebate_programs p on p.id = c.program_id
            where c.ingest_run_id = %s
            order by p.external_ref, c.period_start
            """,
            (run_id,),
        )
        stored = cur.fetchall()

    assert len(stored) == 18
    total = sum((Decimal(row["amount"]) for row in stored), Decimal("0"))
    assert quantize_amount(total) == Decimal(oracle["totalAmountUsd"])

    oracle_by_key = {
        (entry["programExternalRef"], entry["periodStart"]): entry for entry in oracle["calcs"]
    }
    for row in stored:
        key = (row["external_ref"], row["period_start"].isoformat())
        expected = oracle_by_key[key]
        assert str(row["amount"]) == expected["amountUsd"], key
        assert str(row["basis_qty"]) == expected["basisQty"], key
        assert row["contributed_line_count"] == expected["contributedLineCount"], key
        assert row["skipped_line_count"] == expected["skippedLineCount"], key
        assert row["out_of_scope_line_count"] == expected["outOfScopeLineCount"], key


def test_missing_pack_size_and_missing_catch_weight_counts_equal_the_variance_oracle(
    full_calculated_run: tuple[str, str], live_conn: psycopg.Connection[Any]
) -> None:
    """The two oracles must agree about which lines are uncomputable - 125 `missing_pack_size`,
    70 `missing_catch_weight` - which is the precondition P13's attribution rules need."""
    run_id, _ = full_calculated_run
    with live_conn.cursor() as cur:
        cur.execute(
            "select skipped_reason, count(*) from rebate_calc_lines "
            "where ingest_run_id = %s and line_status = 'skipped' group by skipped_reason",
            (run_id,),
        )
        counts = dict(cur.fetchall())
    assert counts.get("missing_pack_size") == 125
    assert counts.get("missing_catch_weight") == 70


def test_aggregate_rounding_would_have_diverged_by_28_cents(
    full_calculated_run: tuple[str, str], live_conn: psycopg.Connection[Any]
) -> None:
    """Pins the divergence the plan measured: rounding once over the aggregate basis per
    program-period, instead of once per line then summed, overstates the total by $0.28 across
    these 18 rows - so nobody "simplifies" the rounding later without a failing test naming the
    cost."""
    run_id, _customer_id = full_calculated_run
    lines = _load_lines(live_conn, run_id)

    # Per-period aggregate basis, grouped exactly like `rebate_calcs` is; the rate is read back
    # from the catalog by program id below, not carried through this dict.
    basis_by_period: dict[tuple[str, str], Decimal] = {}
    for row in lines:
        program = _PROGRAM_BY_NAME.get(str(row["brand_manufacturer"]))
        if program is None:
            continue
        shim = _ShimLine(
            uom=str(row["uom"]),
            qty=Decimal(row["qty"]),
            pack_size=None if row["pack_size"] is None else int(row["pack_size"]),
            catch_weight_lbs=None
            if row["catch_weight_lbs"] is None
            else Decimal(row["catch_weight_lbs"]),
        )
        status = _expected_status(shim, program)
        if status != "contributed":
            continue
        basis = basis_for(shim, program)  # type: ignore[arg-type]
        assert basis is not None
        key = (
            program.program_id,
            f"{row['invoice_date'].year:04d}-{row['invoice_date'].month:02d}",
        )
        basis_by_period[key] = basis_by_period.get(key, Decimal("0")) + basis

    aggregate_total = Decimal("0")
    for (program_id, _period), basis in basis_by_period.items():
        rate = next(p.rate for p in PROGRAMS if p.program_id == program_id)
        aggregate_total += quantize_amount(rate * basis)
    aggregate_total = quantize_amount(aggregate_total)

    oracle = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))
    per_line_total = Decimal(oracle["totalAmountUsd"])

    assert aggregate_total - per_line_total == Decimal("0.28")
    assert aggregate_total != per_line_total
