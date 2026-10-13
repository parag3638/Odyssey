"""Invoice lines that appear more than once.

The key is `(invoice_no, dist_item_id, uom, qty, unit_price)` - the generator's own notion of
the same line, and the right one. mvp.md proposed `(invoice_no, dist_item_id, qty,
ext_amount)`, which would call a seeded mixed-UOM line and its untouched `CS` sibling
duplicates of each other: the defect changes `uom` and `pack_size`, leaving the quantity and
the extension identical, so two genuinely different lines would collide.

`uom` and `unit_price` are in the key because a case line and an each line of the same item at
the same quantity are different lines, and a price change between them means a re-key rather
than a duplicate.

Blocking, per the oracle: a duplicated line is duplicated money, and a rebate paid twice on a
re-keyed line is precisely the error this tool exists to prevent. `affected_amount` is the
**extra** copies' value - the group total minus one copy - because that is the overstatement,
not the whole group.
"""

from __future__ import annotations

from decimal import Decimal

from src.features.readiness.codes import DUPLICATE_LINE
from src.features.readiness.rows import (
    MAX_EXAMPLES,
    CheckResult,
    Finding,
    ReadinessRow,
    clean,
    findings,
)

Key = tuple[str, str, str, Decimal, Decimal]


def key_for(row: ReadinessRow) -> Key:
    return (row.invoice_no, row.dist_item_id, row.uom, row.qty, row.unit_price)


class DuplicateLine:
    code = DUPLICATE_LINE

    def __init__(self) -> None:
        self._groups: dict[Key, list[tuple[int, Decimal]]] = {}
        self._examined = 0

    def examine(self, row: ReadinessRow) -> None:
        self._examined += 1
        self._groups.setdefault(key_for(row), []).append((row.source_row_number, row.ext_amount))

    def finish(self) -> CheckResult:
        rows: list[int] = []
        overstatement = Decimal("0.00")
        groups = 0
        for members in self._groups.values():
            if len(members) < 2:
                continue
            groups += 1
            rows.extend(number for number, _ in members)
            # One copy is legitimate. The rest is what the file overstates by.
            overstatement += sum((amount for _, amount in members[1:]), Decimal("0.00"))

        if not rows:
            return clean(self.code, self._examined, distinctLines=len(self._groups))
        rows.sort()
        return findings(
            self.code,
            self._examined,
            Finding(
                affected_rows=len(rows),
                affected_amount=overstatement,
                detail={
                    "duplicateGroups": groups,
                    "overstatementUsd": f"{overstatement:f}",
                },
                example_row_numbers=tuple(rows[:MAX_EXAMPLES]),
            ),
            distinctLines=len(self._groups),
        )
