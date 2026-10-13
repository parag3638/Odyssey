"""Items sold under more than one unit, with no pack size to convert them.

**A per-item rule, not a per-row one.** "This line has no pack size" is not a problem: a
pound-priced catch-weight line never has one and never needs one. The problem is an *item*
that appears as cases in one place and eaches in another, where nothing in the file says how
many eaches make a case - so no rebate rate expressed per case can be applied to the whole
item. Checking only `pack_size is null` would also take every clean `LB` line in the file.

Two passes over the rows is not an option (one streaming read is the budget), so this holds
the per-item unit set and the candidate rows, and decides at `finish`.
"""

from __future__ import annotations

from decimal import Decimal

from src.features.readiness.codes import MIXED_UOM
from src.features.readiness.rows import (
    MAX_EXAMPLES,
    CheckResult,
    Finding,
    ReadinessRow,
    clean,
    findings,
)

#: Units that need a pack size to be comparable with a per-case rate. A pound is priced by
#: weight and carries its own, so it is not a candidate.
CONVERTIBLE_UNITS = ("CS", "EA")


class MixedUomWithoutPackSize:
    code = MIXED_UOM

    def __init__(self) -> None:
        self._units_by_item: dict[str, set[str]] = {}
        self._candidates: dict[str, list[tuple[int, Decimal]]] = {}
        self._examined = 0

    def examine(self, row: ReadinessRow) -> None:
        self._examined += 1
        self._units_by_item.setdefault(row.dist_item_id, set()).add(row.uom)
        if row.uom in CONVERTIBLE_UNITS and row.pack_size is None:
            self._candidates.setdefault(row.dist_item_id, []).append(
                (row.source_row_number, row.ext_amount)
            )

    def finish(self) -> CheckResult:
        affected: list[tuple[int, Decimal]] = []
        items: list[str] = []
        for item, rows in self._candidates.items():
            if len(self._units_by_item.get(item, set())) > 1:
                affected.extend(rows)
                items.append(item)

        context = {"itemsExamined": len(self._units_by_item)}
        if not affected:
            return clean(self.code, self._examined, **context)

        affected.sort()
        return findings(
            self.code,
            self._examined,
            Finding(
                affected_rows=len(affected),
                affected_amount=sum((amount for _, amount in affected), Decimal("0.00")),
                detail={
                    "itemsAffected": len(items),
                    # Capped: a pathological file must not put 10,000 item codes in a JSON
                    # response, and the first few are what a person starts from.
                    "exampleItems": sorted(items)[:MAX_EXAMPLES],
                },
                example_row_numbers=tuple(number for number, _ in affected[:MAX_EXAMPLES]),
            ),
            **context,
        )
