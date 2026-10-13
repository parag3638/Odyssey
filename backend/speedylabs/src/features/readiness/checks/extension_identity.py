"""Line totals that do not equal quantity times price.

The basis is decided by `uom`: a pound line extends on `catch_weight_lbs`, everything else on
`qty`. A line whose basis is **null is excluded**, not failed - a `LB` line with no weight has
no computable extension, and `catch_weight_missing` is already reporting it. Counting it twice
would make one bad line look like two problems and corrupt both counts.

**Deliberately not P5's rule.** `mapping/validators._validate_extension_identity` accepts
*either* basis, because it is asking "is this mapping right?" and must not cry wolf on a
catch-weight column whose weight carries a rounding artefact. Here the mapping is already
settled and the question is "is this data right?", where the basis is known. Using P5's rule
would fall back to `qty x price` on the 140 weight-less lines and flag every one of them as an
extension mismatch too - the double-counting above, in the other direction.

Tolerance is absolute (`$0.01`) with no relative component, for a measured reason: the
generator seeds exactly `$3.50` of error per affected line, and a relative tolerance of 0.001
would hide that on any line over `$3,500`.
"""

from __future__ import annotations

from decimal import Decimal

from src.features.readiness.codes import EXTENSION_MISMATCH
from src.features.readiness.rows import (
    MAX_EXAMPLES,
    CheckResult,
    Finding,
    ReadinessRow,
    clean,
    findings,
)
from src.lib.money import extend

POUND_UNIT = "LB"


def basis_for(row: ReadinessRow) -> Decimal | None:
    """The quantity this line is billed on, or `None` when the file does not say."""
    return row.catch_weight_lbs if row.uom == POUND_UNIT else row.qty


class ExtensionMismatch:
    code = EXTENSION_MISMATCH

    def __init__(self, tolerance_usd: Decimal) -> None:
        self._tolerance = tolerance_usd
        self._rows: list[int] = []
        self._absolute_delta = Decimal("0.00")
        self._worst = Decimal("0.00")
        self._examined = 0
        self._skipped_no_basis = 0

    def examine(self, row: ReadinessRow) -> None:
        basis = basis_for(row)
        if basis is None:
            self._skipped_no_basis += 1
            return
        self._examined += 1
        delta = row.ext_amount - extend(basis, row.unit_price)
        if abs(delta) > self._tolerance:
            self._rows.append(row.source_row_number)
            self._absolute_delta += abs(delta)
            self._worst = max(self._worst, abs(delta))

    def finish(self) -> CheckResult:
        context = {
            "toleranceUsd": f"{self._tolerance:f}",
            "linesWithoutABasis": self._skipped_no_basis,
        }
        if not self._rows:
            return clean(self.code, self._examined, **context)
        return findings(
            self.code,
            self._examined,
            Finding(
                affected_rows=len(self._rows),
                affected_amount=self._absolute_delta,
                detail={
                    "toleranceUsd": f"{self._tolerance:f}",
                    "largestDifferenceUsd": f"{self._worst:f}",
                    "linesWithoutABasis": self._skipped_no_basis,
                },
                example_row_numbers=tuple(self._rows[:MAX_EXAMPLES]),
            ),
            **context,
        )
