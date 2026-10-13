"""The file's own total against the sum of its lines.

The sharpest check in the set, because it needs nothing outside the file: an export whose
trailer disagrees with its own detail lines is self-inconsistent, and whichever number is
wrong, a rebate computed from the other one will not reconcile. Real AS/400 extracts do this
routinely - usually a trailer computed before a late adjustment.

**Three outcomes, not two.** A CSV has no trailer at all, and a fixed-width file may carry one
in a shape `lib/parsers/trailer.py` does not recognise. Both are `not_applicable` with the
reason stored, never a pass: "we checked and it agreed" and "there was nothing to check" are
different facts, and conflating them is exactly what a templated checklist does.
"""

from __future__ import annotations

from decimal import Decimal

from src.features.readiness.codes import FOOTER_TOTAL_MISMATCH
from src.features.readiness.context import FooterFacts
from src.features.readiness.rows import (
    CheckResult,
    Finding,
    ReadinessRow,
    clean,
    findings,
    not_applicable,
)

NO_FOOTER_REASON = (
    "This file has no footer record, so it declares no total to compare. CSV exports "
    "normally do not have one."
)
UNREADABLE_FOOTER_REASON = (
    "This file's footer record is not in a layout we recognise, so no declared total could "
    "be read from it. Send the record layout for the trailer and it can be checked."
)


class FooterTotalMismatch:
    code = FOOTER_TOTAL_MISMATCH

    def __init__(self, footer: FooterFacts, tolerance_usd: Decimal) -> None:
        self._footer = footer
        self._tolerance = tolerance_usd
        self._sum = Decimal("0.00")
        self._examined = 0

    def examine(self, row: ReadinessRow) -> None:
        self._examined += 1
        self._sum += row.ext_amount

    def finish(self) -> CheckResult:
        if not self._footer.has_footer_record:
            return not_applicable(self.code, NO_FOOTER_REASON, self._examined)
        if self._footer.declared_total is None:
            return not_applicable(self.code, UNREADABLE_FOOTER_REASON, self._examined)

        declared = self._footer.declared_total
        delta = self._sum - declared
        context = {
            "declaredTotalUsd": f"{declared:f}",
            "lineTotalUsd": f"{self._sum:f}",
            "toleranceUsd": f"{self._tolerance:f}",
        }
        if abs(delta) <= self._tolerance:
            return clean(self.code, self._examined, **context)

        row_count_note: dict[str, object] = {}
        if self._footer.declared_rows is not None and self._footer.declared_rows != self._examined:
            # Worth saying in the same finding: a count that also disagrees usually means
            # whole lines are missing rather than an amount being wrong.
            row_count_note = {
                "declaredRows": self._footer.declared_rows,
                "rowsFound": self._examined,
            }

        return findings(
            self.code,
            self._examined,
            Finding(
                # One file-level fact, so one. `AFFECTED_UNIT` says "file" for this code.
                affected_rows=1,
                affected_amount=abs(delta),
                detail={
                    "unit": "file",
                    "declaredTotalUsd": f"{declared:f}",
                    "lineTotalUsd": f"{self._sum:f}",
                    "deltaUsd": f"{abs(delta):f}",
                    "direction": "footer is lower" if delta > 0 else "footer is higher",
                    "toleranceUsd": f"{self._tolerance:f}",
                    **row_count_note,
                },
                example_row_numbers=(),
            ),
            **context,
        )
