"""Pound-priced lines with no shipped weight.

A catch-weight item is billed on actual pounds, so a `LB` line without
`catch_weight_lbs` cannot be extended, cannot be rebated per pound, and cannot be
reconciled against the invoice total. There is nothing to infer it from: the weight is a
physical measurement the distributor took.

Blocking, and the one check whose fix is unambiguous - the ERP has the number, the export
is not sending it.
"""

from __future__ import annotations

from decimal import Decimal

from src.features.readiness.codes import CATCH_WEIGHT_MISSING
from src.features.readiness.rows import (
    MAX_EXAMPLES,
    CheckResult,
    Finding,
    ReadinessRow,
    clean,
    findings,
)

POUND_UNIT = "LB"


class CatchWeightMissing:
    code = CATCH_WEIGHT_MISSING

    def __init__(self) -> None:
        self._rows: list[int] = []
        self._amount = Decimal("0.00")
        self._examined = 0

    def examine(self, row: ReadinessRow) -> None:
        if row.uom != POUND_UNIT:
            return
        # Examined counts the rows the check *applied* to - pound lines - not every row in
        # the file. A checklist saying "48,150 rows examined" for a check that looked at
        # 9,000 of them would be overstating its own coverage.
        self._examined += 1
        if row.catch_weight_lbs is None:
            self._rows.append(row.source_row_number)
            self._amount += row.ext_amount

    def finish(self) -> CheckResult:
        if not self._rows:
            return clean(self.code, self._examined)
        return findings(
            self.code,
            self._examined,
            Finding(
                affected_rows=len(self._rows),
                affected_amount=self._amount,
                detail={},
                example_row_numbers=tuple(self._rows[:MAX_EXAMPLES]),
            ),
        )
