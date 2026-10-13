"""Brands that are not in this customer's manufacturer list.

A warning, not a blocker: an unrecognised brand is usually a new supplier or a spelling the
reference list has not caught up with, and a file is still usable - those lines simply earn no
rebate until somebody says which manufacturer they belong to.

The generator seeds **no** instances of this, so on the demo file it reports `clean` after
examining every row. That is the point of keeping it: a check that examined 48,150 rows and
found nothing is the sharpest available proof that outcomes are *recorded from this file*
rather than printed from a template. A static checklist cannot tell the difference between
this and a check that never ran.
"""

from __future__ import annotations

from decimal import Decimal

from src.features.readiness.codes import UNKNOWN_MANUFACTURER
from src.features.readiness.rows import (
    MAX_EXAMPLES,
    CheckResult,
    Finding,
    ReadinessRow,
    clean,
    findings,
    not_applicable,
)
from src.lib.manufacturers import ManufacturerLookup

NO_REFERENCE_REASON = (
    "This customer has no manufacturer reference list loaded, so every brand would look "
    "unknown. Load the list and this can be checked."
)

#: Distinct unknown values listed in the detail. Capped: a mis-mapped brand column would
#: otherwise put every distinct value in the file into a JSON response.
MAX_LISTED_VALUES = 20


class UnknownManufacturer:
    code = UNKNOWN_MANUFACTURER

    def __init__(self, manufacturers: ManufacturerLookup) -> None:
        self._manufacturers = manufacturers
        self._rows: list[int] = []
        self._amount = Decimal("0.00")
        self._values: set[str] = set()
        self._examined = 0

    def examine(self, row: ReadinessRow) -> None:
        if not self._manufacturers.loaded:
            return
        self._examined += 1
        if self._manufacturers.find(row.brand_manufacturer) is None:
            self._rows.append(row.source_row_number)
            self._amount += row.ext_amount
            self._values.add(row.brand_manufacturer)

    def finish(self) -> CheckResult:
        if not self._manufacturers.loaded:
            return not_applicable(self.code, NO_REFERENCE_REASON)
        context = {"knownManufacturers": len(self._manufacturers.entries)}
        if not self._rows:
            return clean(self.code, self._examined, **context)
        return findings(
            self.code,
            self._examined,
            Finding(
                affected_rows=len(self._rows),
                affected_amount=self._amount,
                detail={
                    "unknownValues": sorted(self._values)[:MAX_LISTED_VALUES],
                    "unknownValueCount": len(self._values),
                },
                example_row_numbers=tuple(self._rows[:MAX_EXAMPLES]),
            ),
            **context,
        )
