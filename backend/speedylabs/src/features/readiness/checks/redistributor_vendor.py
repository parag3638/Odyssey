"""Lines billed direct for a brand this distributor only buys through a redistributor.

The narrow, provable rule: the brand resolves to a reference entry marked
`redistributor_only`, and the line's `invoice_vendor` is one of that entry's own direct vendor
values. For such a brand a direct vendor number is *impossible*, so the line is misattributed
from the file alone, with no judgement needed.

The rule a reader might expect - "this vendor is not a known redistributor" - was rejected
deliberately: it would warn on every unfamiliar vendor on the day a customer's reference list
is first loaded, which is a checklist nobody reads. A line billed under an unknown vendor is
not evidence of anything; a line billed under the brand's *own* vendor for a brand that cannot
be bought direct is.

Why it matters: a misattributed line is rebated under the wrong program at the wrong rate, and
it is the P13 variance cause that is hardest to find by hand.

Needs the customer's reference list. Without one, `not_applicable` with a reason - never a
clean pass, because nothing was checked.
"""

from __future__ import annotations

from decimal import Decimal

from src.features.readiness.codes import REDISTRIBUTOR_VENDOR_MISMATCH
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
    "This customer has no manufacturer reference list loaded, so there is no way to tell "
    "which brands are bought through a redistributor. Load the list and this can be checked."
)


class RedistributorVendorMismatch:
    code = REDISTRIBUTOR_VENDOR_MISMATCH

    def __init__(self, manufacturers: ManufacturerLookup) -> None:
        self._manufacturers = manufacturers
        self._rows: list[int] = []
        self._amount = Decimal("0.00")
        self._brands: set[str] = set()
        self._examined = 0

    def examine(self, row: ReadinessRow) -> None:
        if not self._manufacturers.loaded:
            return
        entry = self._manufacturers.find(row.brand_manufacturer)
        if entry is None or not entry.redistributor_only:
            return
        # Only redistributor-only brands are in scope, so those are the rows examined.
        self._examined += 1
        if entry.bills_direct_as(row.invoice_vendor):
            self._rows.append(row.source_row_number)
            self._amount += row.ext_amount
            self._brands.add(entry.name)

    def finish(self) -> CheckResult:
        if not self._manufacturers.loaded:
            return not_applicable(self.code, NO_REFERENCE_REASON)
        if not self._rows:
            return clean(self.code, self._examined)
        return findings(
            self.code,
            self._examined,
            Finding(
                affected_rows=len(self._rows),
                affected_amount=self._amount,
                detail={
                    "brandsAffected": sorted(self._brands)[:MAX_EXAMPLES],
                },
                example_row_numbers=tuple(self._rows[:MAX_EXAMPLES]),
            ),
        )
