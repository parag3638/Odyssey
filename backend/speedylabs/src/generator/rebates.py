"""The customer's rebate programs, emitted as a committed fixture.

A rebate program is **commercial data**: the rate a manufacturer pays this distributor, which
arrives from a signed agreement and never from an uploaded file. `rebate_programs` has existed
since migration 0001 and nothing has ever written a row to it, so the demo needs a source - and
it must be the same seed the invoices came from, or the calculator would be scored against rates
that belong to a different world than the files describe.

So the generator writes it as a fixture and `scripts/seed_demo.py` loads the JSON, which is the
arrangement `customer_manufacturers` already uses, for the same reason and with the same
`--verify` protection. Production code reads the database and never imports `src.generator`.

**`brandManufacturer` is the manufacturer's name, never the brand code.** A program finds its
lines through `customer_manufacturers.brand_values`, because the spelling in
`canonical_lines.brand_manufacturer` is whatever that customer's `map_values` produced - the
committed fixture path yields `"Bayline Creamery"` while the ERP file says `"BLC"`. A program
carrying its own spelling would be a second list of spellings to keep in step with the first,
and a program keyed to the code would match nothing and report $0.00 in silence. A foreign key
on `(customer_id, brand_manufacturer)` makes that unmatchable row unrepresentable.

**No effective-date window, deliberately.** `effectiveFrom` is the first day the seeded file
covers and `effectiveTo` is null, so the window never clips a month. The date *rule* is still
implemented and tested in the engine; what is pinned here is that the seeded data does not
exercise it, because a window that dropped one month would move every number in the rebate
oracle.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Final

from src.generator.catalog import (
    REDISTRIBUTOR_RATE,
    Catalog,
    RebateProgram,
    build_catalog,
    program_for_brand,
)
from src.generator.defects import SeededFile, build_seeded_file
from src.generator.invoices import InvoiceLine
from src.generator.legacy import CUTOFF_DAY, basis_for, new_rebate
from src.generator.seed import SEED
from src.lib.money import quantize_amount, quantize_qty

#: The two reasons a line the program should have paid on cannot be computed. Spelled here rather
#: than imported from `features/rebates.codes`, so the oracle stays independent of the code it
#: scores - an oracle that imports its subject's vocabulary cannot catch a rename.
MISSING_PACK_SIZE: Final = "missing_pack_size"
MISSING_CATCH_WEIGHT: Final = "missing_catch_weight"

#: The first day the seeded invoices cover. A program effective from this day with no end
#: covers the whole file, which is what keeps the oracle's six periods whole.
EFFECTIVE_FROM: Final = "2026-01-01"


def build_rebate_programs(catalog: Catalog | None = None) -> list[dict[str, Any]]:
    """One entry per program, sorted by external reference so the file is byte-stable."""
    source = catalog if catalog is not None else build_catalog(SEED)
    names = {manufacturer.code: manufacturer.name for manufacturer in source.manufacturers}
    return [
        {
            "externalRef": program.program_id,
            "name": program.name,
            "kind": program.kind,
            # The manufacturer's name, resolved from the catalog - see the module docstring.
            "brandManufacturer": names[program.brand_code],
            "rate": f"{program.rate:f}",
            # The rate the legacy system paid when it looked a program up by vendor number and
            # landed on a redistributor agreement instead of the brand's own. Commercial data, and
            # P13's `redistributor_misattribution` rule cannot price that cause without it - the
            # number lives only in the generator otherwise, and production must never import that.
            "redistributorRate": f"{REDISTRIBUTOR_RATE:f}",
            "uomBasis": program.uom_basis,
            "effectiveFrom": EFFECTIVE_FROM,
            "effectiveTo": None,
        }
        for program in sorted(source.programs, key=lambda item: item.program_id)
    ]


def build_rebate_program_document(catalog: Catalog | None = None) -> dict[str, Any]:
    """The fixture's whole content, including the seed that produced it."""
    return {
        "seed": SEED,
        "note": (
            "Seeded rebate programs for the demo distributor. Fictional commercial terms, "
            "generated from the same seed as the invoice fixtures. Loaded by "
            "scripts/seed_demo.py into rebate_programs."
        ),
        # The one day this customer's legacy system posted into the following period. A fact the
        # customer tells you at onboarding, which P13's `date_cutoff` rule cannot infer: "the last
        # day of any month" would invent a relocation in every month, and reading it off the
        # variance signs would let the rule claim whatever makes the arithmetic work.
        "legacyPeriodCutoffDay": CUTOFF_DAY.isoformat(),
        "programs": build_rebate_programs(catalog),
    }


# --- the rebate oracle ----------------------------------------------------------------


def _period_start(day: date) -> date:
    return day.replace(day=1)


def _period_end(start: date) -> date:
    following = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return following - timedelta(days=1)


def build_rebate_oracle(seeded: SeededFile | None = None, seed: int = SEED) -> dict[str, Any]:
    """What a correct calculation over the seeded file produces, per program per month.

    Built from the **generator's** arithmetic - `legacy.py::basis_for` and `new_rebate` - and never
    from `features/rebates`, so the equivalence test compares two independent implementations
    instead of comparing one with itself. That is the whole value of the file: it is the side of
    P13's subtraction that is not the legacy system, and if the production engine and this disagree
    then `expected_variance_attribution.json`'s $7,376.08 does not mean what it says.

    One quantised amount per line, summed - not a rounding of an aggregate basis. Measured, the two
    conventions differ by $0.28 over these 18 rows, and those cents would arrive in P13 as residue
    no cause can claim.
    """
    sf = seeded if seeded is not None else build_seeded_file(seed)
    programs = {program.program_id: program for program in build_catalog(seed).programs}

    #: (external_ref, period_start) -> accumulated basis, amount and the three line counts.
    rows: dict[tuple[str, date], dict[str, Any]] = {}
    for line in sf.lines:
        program = program_for_brand(line.brand_manufacturer)
        if program is None:
            continue
        start = _period_start(line.invoice_date)
        row = rows.setdefault(
            (program.program_id, start),
            {
                "basis": Decimal("0"),
                "amount": Decimal("0"),
                "contributed": 0,
                "skipped": 0,
                "outOfScope": 0,
                "skippedReasons": {MISSING_PACK_SIZE: 0, MISSING_CATCH_WEIGHT: 0},
            },
        )
        status, reason = _status_of(line, program)
        if status == "contributed":
            basis = basis_for(line, program)
            assert basis is not None
            row["basis"] += quantize_qty(basis)
            row["amount"] += new_rebate(line, program)
            row["contributed"] += 1
        elif status == "skipped":
            row["skipped"] += 1
            assert reason is not None
            row["skippedReasons"][reason] += 1
        else:
            row["outOfScope"] += 1

    calcs: list[dict[str, Any]] = []
    total = Decimal("0")
    lines_written = 0
    for (external_ref, start), row in sorted(
        rows.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        program = programs[external_ref]
        total += row["amount"]
        lines_written += row["contributed"] + row["skipped"] + row["outOfScope"]
        calcs.append(
            {
                "programExternalRef": external_ref,
                "kind": program.kind,
                "uomBasis": program.uom_basis,
                "periodStart": start.isoformat(),
                "periodEnd": _period_end(start).isoformat(),
                "basisQty": f"{quantize_qty(row['basis']):f}",
                "amountUsd": f"{row['amount']:f}",
                "contributedLineCount": row["contributed"],
                "skippedLineCount": row["skipped"],
                "outOfScopeLineCount": row["outOfScope"],
                "skippedReasons": [
                    {"reason": reason, "lineCount": count}
                    for reason, count in sorted(row["skippedReasons"].items())
                    if count
                ],
            }
        )
    return {
        "seed": SEED,
        "engineRules": "per-line quantisation, ROUND_HALF_EVEN, one multiplication per line",
        "totalAmountUsd": f"{quantize_amount(total):f}",
        "calcCount": len(calcs),
        "calcLineCount": lines_written,
        "calcs": calcs,
    }


def _status_of(line: InvoiceLine, program: RebateProgram) -> tuple[str, str | None]:
    """The three-way split, derived here independently of `features/rebates`.

    `basis_for` returns None both for an each-priced line with no pack size and for every line in
    the other basis, and those two are a defect and a non-event respectively. Splitting on the
    program's basis is what makes the oracle's 125 and 70 line counts equal
    `expected_variance_attribution.json`'s `case_each_conversion` and `missing_catch_weight`.
    """
    if program.uom_basis == "CS":
        if line.uom == "CS":
            return "contributed", None
        if line.uom == "EA":
            if line.pack_size in (None, 0):
                return "skipped", MISSING_PACK_SIZE
            return "contributed", None
        return "out_of_basis_scope", None
    if line.uom == "LB":
        if line.catch_weight_lbs is None:
            return "skipped", MISSING_CATCH_WEIGHT
        return "contributed", None
    return "out_of_basis_scope", None
