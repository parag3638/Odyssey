"""The customer's legacy rebate numbers, and the oracle that scores attribution.

The parallel run compares two sets of numbers: what the customer's old system paid, and
what a calculation over the newly-mapped data produces. Every dollar of difference here
is introduced on purpose, by exactly one of five causes, on exactly one line — which is
what makes "attributes >=90% of seeded variance dollars to the correct root cause" a
measurement instead of an opinion.

The five causes, and why each is one-directional where it can be:

1. **case/each conversion.** An each-priced line with no pack size: the legacy system
   counted the eaches as cases and overpaid. The new calculation cannot convert at all,
   so it pays nothing. Legacy over.
2. **missing catch weight.** The weight was stripped from the export, but the legacy
   system still had it on file and paid on it. The new calculation has nothing to
   multiply. Legacy over.
3. **redistributor misattribution.** The legacy system looked the program up by vendor
   number, landed on a redistributor agreement, and applied its lower rate. Legacy under.
4. **duplicate lines.** The legacy system paid both copies; the new calculation pays the
   line once. Legacy over.
5. **date cutoff.** The legacy system assigned the last business day of March to April.
   The dollars are right but land in the wrong period.

Causes 1-4 reuse the readiness defect row sets, which `defects.py` already guarantees are
disjoint. Cause 5 takes only rows no other cause claimed. That is the one-cause-per-line
rule, and it holds by construction rather than by inspection.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from src.generator.catalog import (
    REDISTRIBUTOR_RATE,
    Catalog,
    RebateProgram,
    program_for_brand,
)
from src.generator.defects import (
    CHECK_CATCH_WEIGHT,
    CHECK_DUPLICATE,
    CHECK_MIXED_UOM,
    CHECK_VENDOR,
    SeededFile,
    build_seeded_file,
)
from src.generator.fixtures import ORACLE_DIR, write_json
from src.generator.invoices import InvoiceLine
from src.generator.seed import SEED
from src.lib.money import quantize_amount

CAUSE_CASE_EACH: Final = "case_each_conversion"
CAUSE_MISSING_WEIGHT: Final = "missing_catch_weight"
CAUSE_REDISTRIBUTOR: Final = "redistributor_misattribution"
CAUSE_DUPLICATE: Final = "duplicate_line"
CAUSE_DATE_CUTOFF: Final = "date_cutoff"
CAUSE_UNEXPLAINED: Final = "unexplained"

CAUSES: Final = (
    CAUSE_CASE_EACH,
    CAUSE_MISSING_WEIGHT,
    CAUSE_REDISTRIBUTOR,
    CAUSE_DUPLICATE,
    CAUSE_DATE_CUTOFF,
)

#: The legacy system shifted this day's lines into the following period.
CUTOFF_DAY: Final = date(2026, 3, 31)


@dataclass(frozen=True)
class LegacyNumbers:
    """Per program, per period, as the customer's spreadsheet reports it."""

    rows: tuple[tuple[str, str, Decimal, Decimal], ...]  # program_id, period, units, amount
    #: cause -> row_number -> the dollar magnitude that cause contributes on that line.
    contributions: dict[str, dict[int, Decimal]]


def period_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def cases_for(line: InvoiceLine) -> Decimal | None:
    """Cases the line represents, or None when the data cannot say.

    An each-priced line with no pack size is the uncomputable case, and returning None
    rather than guessing is the whole point: a guess here would silently invent rebate
    dollars.
    """
    if line.uom == "CS":
        return line.qty
    if line.uom == "EA":
        if line.pack_size in (None, 0):
            return None
        assert line.pack_size is not None
        return line.qty / Decimal(line.pack_size)
    return None


def basis_for(line: InvoiceLine, program: RebateProgram) -> Decimal | None:
    """The quantity the program's rate is paid on, or None when the data cannot say.

    One arithmetic, two readers: `new_rebate` turns it into money, and
    `src/generator/rebates.py` reports the basis itself in the rebate oracle. They must not
    drift, because the oracle's dollars and the oracle's quantities describe the same lines.
    """
    if program.kind == "per_case_volume":
        return cases_for(line)
    return line.catch_weight_lbs


def new_rebate(line: InvoiceLine, program: RebateProgram) -> Decimal:
    """What a calculation over the mapped export produces. Zero when uncomputable.

    One multiplication and one rounding, over the unquantised rate and basis: pre-rounding
    either operand moves the cent, and `rebate_calc_lines.amount_contribution` stores exactly
    this figure as the unit P13's attribution claims.
    """
    basis = basis_for(line, program)
    return Decimal("0") if basis is None else quantize_amount(program.rate * basis)


def build_legacy(seeded: SeededFile | None = None, seed: int = SEED) -> LegacyNumbers:
    """Legacy totals, plus the per-line dollar contribution of each cause.

    Programs are looked up by brand through `program_for_brand`, so no catalog argument
    is needed here - the catalog is only required to put program *names* on the export.
    """
    sf = seeded if seeded is not None else build_seeded_file(seed)
    by_row = {line.row_number: line for line in sf.lines}

    claimed_elsewhere = {
        row
        for code in (CHECK_MIXED_UOM, CHECK_CATCH_WEIGHT, CHECK_VENDOR, CHECK_DUPLICATE)
        for row in sf.affected_rows[code]
    }
    # Cause 5 may only use rows nothing else claimed, or a line would carry two causes.
    cutoff_rows = tuple(
        sorted(
            line.row_number
            for line in sf.lines
            if line.invoice_date == CUTOFF_DAY and line.row_number not in claimed_elsewhere
        )
    )

    # The duplicate copies, not the originals: the second of each identical pair is the
    # one the legacy system paid twice.
    duplicate_rows = sf.affected_rows[CHECK_DUPLICATE]
    duplicate_copies = tuple(duplicate_rows[i] for i in range(1, len(duplicate_rows), 2))

    contributions: dict[str, dict[int, Decimal]] = {cause: {} for cause in CAUSES}
    deltas: dict[tuple[str, str], Decimal] = {}

    def add_delta(program_id: str, period: str, amount: Decimal) -> None:
        key = (program_id, period)
        deltas[key] = deltas.get(key, Decimal("0")) + amount

    # --- true totals, as the new calculation will produce them ---
    totals: dict[tuple[str, str], Decimal] = {}
    units: dict[tuple[str, str], Decimal] = {}
    for line in sf.lines:
        program = program_for_brand(line.brand_manufacturer)
        if program is None:
            continue
        key = (program.program_id, period_of(line.invoice_date))
        totals[key] = totals.get(key, Decimal("0")) + new_rebate(line, program)
        basis = cases_for(line) if program.kind == "per_case_volume" else line.catch_weight_lbs
        units[key] = units.get(key, Decimal("0")) + (basis or Decimal("0"))

    # --- 1. case/each: legacy counted eaches as cases ---
    for row in sf.affected_rows[CHECK_MIXED_UOM]:
        line = by_row[row]
        program = program_for_brand(line.brand_manufacturer)
        if program is None or program.kind != "per_case_volume":
            continue
        amount = quantize_amount(program.rate * line.qty)
        contributions[CAUSE_CASE_EACH][row] = amount
        add_delta(program.program_id, period_of(line.invoice_date), amount)

    # --- 2. missing weight: legacy still had it ---
    for row, weight in sf.removed_weights.items():
        line = by_row[row]
        program = program_for_brand(line.brand_manufacturer)
        if program is None or program.kind != "per_lb_billback":
            continue
        amount = quantize_amount(program.rate * weight)
        contributions[CAUSE_MISSING_WEIGHT][row] = amount
        add_delta(program.program_id, period_of(line.invoice_date), amount)

    # --- 3. misattribution: legacy used the redistributor's lower rate ---
    for row in sf.affected_rows[CHECK_VENDOR]:
        line = by_row[row]
        program = program_for_brand(line.brand_manufacturer)
        if program is None:
            continue
        basis = cases_for(line) if program.kind == "per_case_volume" else line.catch_weight_lbs
        if basis is None:
            continue
        shortfall = quantize_amount((program.rate - REDISTRIBUTOR_RATE) * basis)
        contributions[CAUSE_REDISTRIBUTOR][row] = abs(shortfall)
        add_delta(program.program_id, period_of(line.invoice_date), -shortfall)

    # --- 4. duplicates: legacy paid the second copy too ---
    for row in duplicate_copies:
        line = by_row[row]
        program = program_for_brand(line.brand_manufacturer)
        if program is None:
            continue
        amount = new_rebate(line, program)
        if amount == 0:
            continue
        contributions[CAUSE_DUPLICATE][row] = amount
        add_delta(program.program_id, period_of(line.invoice_date), amount)

    # --- 5. date cutoff: dollars right, period wrong ---
    for row in cutoff_rows:
        line = by_row[row]
        program = program_for_brand(line.brand_manufacturer)
        if program is None:
            continue
        amount = new_rebate(line, program)
        if amount == 0:
            continue
        contributions[CAUSE_DATE_CUTOFF][row] = amount
        march = period_of(line.invoice_date)
        april = "2026-04"
        add_delta(program.program_id, march, -amount)
        add_delta(program.program_id, april, amount)

    rows = tuple(
        (
            program_id,
            period,
            quantize_amount(units.get((program_id, period), Decimal("0"))),
            quantize_amount(
                totals.get((program_id, period), Decimal("0"))
                + deltas.get((program_id, period), Decimal("0"))
            ),
        )
        for program_id, period in sorted(set(totals) | set(deltas))
    )
    return LegacyNumbers(rows=rows, contributions=contributions)


def write_legacy_csv(legacy: LegacyNumbers, catalog: Catalog) -> str:
    """A spreadsheet-shaped export, the way a customer actually hands these over."""
    names = {p.program_id: p.name for p in catalog.programs}
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["PROGRAM", "PROGRAM_NAME", "PERIOD", "BASIS_UNITS", "REBATE_PAID"])
    for program_id, period, basis, amount in legacy.rows:
        writer.writerow(
            [program_id, names.get(program_id, ""), period, f"{basis:f}", f"{amount:f}"]
        )
    return buffer.getvalue()


def build_variance_oracle(legacy: LegacyNumbers) -> dict[str, Any]:
    """Ground truth for A5: every dollar, and the one line and cause it came from."""
    causes: list[dict[str, Any]] = []
    total = Decimal("0")
    for cause in CAUSES:
        per_line = legacy.contributions[cause]
        amount = quantize_amount(sum(per_line.values(), Decimal("0")))
        total += amount
        causes.append(
            {
                "cause": cause,
                "amountUsd": f"{amount:f}",
                "lineCount": len(per_line),
                "rows": sorted(per_line),
            }
        )
    return {
        "seed": SEED,
        "totalSeededVarianceUsd": f"{quantize_amount(total):f}",
        "causes": causes,
    }


def write_variance_oracle(legacy: LegacyNumbers) -> Path:
    return write_json(
        ORACLE_DIR / "expected_variance_attribution.json", build_variance_oracle(legacy)
    )
