"""A reference rebate calculator. **Not Speedy's engine**, and it says so everywhere.

This is a stand-in: it implements the two program kinds the spec names, with no tiers, minimums,
accruals or proration, so the demo can show rebate dollars without pretending to be the real thing.
`engine_name` and `engine_version` are stored on every row it produces and shown beside every number
it computes, which is what keeps the claim honest after the numbers leave this module.

**The arithmetic is not a choice made here.** `fixtures/oracle/expected_variance_attribution.json`
is committed, and its $7,376.08 of seeded variance is the difference between a legacy system's
numbers and *this* calculator's. So this code has to reproduce `src/generator/legacy.py::new_rebate`
exactly - every rule below is matched by a line in that module, and
`tests/test_rebate_engine_matches_oracle.py` compares all 48,150 lines rather than sampling.

One multiplication, one rounding
--------------------------------
`amount = quantize_amount(rate x basis)`, over the **unquantised** rate and basis. Never pre-round
either operand: `rate` is 6dp commercial data and the basis can be non-terminating (10 eaches of a
3-pack). `basis_contribution` is quantised to 4dp *separately*, for the record, because that is the
column's scale - it is not an input to the money.

Rounding per line, then summing, is load-bearing rather than incidental. Rounding once over an
aggregate basis instead diverges by $0.28 across the demo's 18 program-periods and up to $0.09 in a
single one, those cents arrive in P13 as residue no cause can claim, and `rebate_calcs.amount` could
no longer equal the sum of its own 2dp per-line contributions.

Three line statuses, not two
----------------------------
`skipped` means the program should have paid on this line and the data would not say how much - a
defect someone has to fix. `out_of_basis_scope` means the line is priced in a unit this program does
not pay on, which is nobody's problem: this distributor buys 12,666 cases of a brand whose program
is a per-pound billback. Measured over the committed fixture, calling those skipped reports 17,550
skipped lines where 195 are real.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from src.features.rebates.codes import (
    CONTRIBUTED,
    MISSING_CATCH_WEIGHT,
    MISSING_PACK_SIZE,
    OUT_OF_BASIS_SCOPE,
    SKIPPED,
    LineStatus,
)
from src.features.rebates.engine import LineRebate, RebateLine, RebateProgramInput
from src.lib.manufacturers import normalise
from src.lib.money import quantize_amount, quantize_qty

#: Stored on every row this engine writes, and shown beside every number it computes.
ENGINE_NAME: Final = "reference-stand-in"
#: Bump this with any change to the rules above. `canonical/rebate_engine.json` publishes it
#: alongside the rule table, and `make check` fails if that file and this constant disagree - so a
#: rules change cannot be committed without the version bump in the same diff.
ENGINE_VERSION: Final = "1.0.0"

_ZERO_QTY: Final = quantize_qty(0)
_ZERO_AMOUNT: Final = quantize_amount(0)


def _basis(
    line: RebateLine, program: RebateProgramInput
) -> tuple[Decimal | None, LineStatus, str | None]:
    """The quantity this program pays on for this line, with why when there is none.

    Mirrors `generator/legacy.py::basis_for`, split three ways: that function returns `None` both
    for an each-priced line with no pack size *and* for every line in the other basis, and those
    two are a defect and a non-event respectively.
    """
    if program.uom_basis == "CS":
        if line.uom == "CS":
            return line.qty, CONTRIBUTED, None
        if line.uom == "EA":
            if line.pack_size is None or line.pack_size == 0:
                # The uncomputable case. A guess here would invent rebate dollars, which is the
                # defect P13 prices as `case_each_conversion`.
                return None, SKIPPED, MISSING_PACK_SIZE
            return line.qty / Decimal(line.pack_size), CONTRIBUTED, None
        return None, OUT_OF_BASIS_SCOPE, None
    if line.uom == "LB":
        if line.catch_weight_lbs is None:
            return None, SKIPPED, MISSING_CATCH_WEIGHT
        return line.catch_weight_lbs, CONTRIBUTED, None
    return None, OUT_OF_BASIS_SCOPE, None


@dataclass(frozen=True)
class ReferenceRebateEngine:
    """Satisfies `RebateEngine` structurally. Inherits nothing, by design."""

    engine_name: str = ENGINE_NAME
    engine_version: str = ENGINE_VERSION

    def calculate(
        self,
        lines: Sequence[RebateLine],
        programs: Sequence[RebateProgramInput],
    ) -> Sequence[LineRebate]:
        # Spellings are normalised once per call rather than per line: with 10,000 lines a batch
        # and three programs, doing it per line is 30,000 avoidable normalisations.
        matchers = [
            (program, {normalise(value) for value in program.brand_values}) for program in programs
        ]
        results: list[LineRebate] = []
        for line in lines:
            spelling = normalise(line.brand_manufacturer)
            for program, spellings in matchers:
                if spelling not in spellings:
                    # No result at all, not a zero row: a program that was never about this line
                    # should not appear in that line's provenance.
                    continue
                if line.invoice_date < program.effective_from:
                    continue
                if program.effective_to is not None and line.invoice_date > program.effective_to:
                    continue
                results.append(self._price(line, program))
        return results

    def _price(self, line: RebateLine, program: RebateProgramInput) -> LineRebate:
        basis, status, reason = _basis(line, program)
        if basis is None:
            return LineRebate(
                canonical_line_id=line.canonical_line_id,
                program_id=program.program_id,
                line_status=status,
                skipped_reason=reason,
                basis_contribution=_ZERO_QTY,
                amount_contribution=_ZERO_AMOUNT,
            )
        return LineRebate(
            canonical_line_id=line.canonical_line_id,
            program_id=program.program_id,
            line_status=status,
            skipped_reason=None,
            # Quantised for the record, after the money was computed from the unquantised figure.
            basis_contribution=quantize_qty(basis),
            amount_contribution=quantize_amount(program.rate * basis),
        )
