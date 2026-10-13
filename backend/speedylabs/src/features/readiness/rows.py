"""The row the checks read, and the result they return.

`ReadinessRow` carries twelve columns, not the canonical seventeen: §5 forbids `SELECT *`,
and the four the checks never look at (`customer_id`, `customer_name`, `mfr_item_code`,
`is_deviated`, `deviated_price`) would be 48,150 strings fetched to be ignored. The row is
frozen so a check cannot modify what the next accumulator sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from src.features.readiness.codes import Outcome

#: Example rows carried on a finding. Five, capped in three places - here, in the engine,
#: and in the table's own check constraint - because a 50,000-element array in a JSON
#: response is a denial of service against our own UI.
MAX_EXAMPLES = 5


@dataclass(frozen=True)
class ReadinessRow:
    source_row_number: int
    invoice_no: str
    invoice_date: date
    dist_item_id: str
    brand_manufacturer: str
    invoice_vendor: str
    qty: Decimal
    uom: str
    pack_size: int | None
    catch_weight_lbs: Decimal | None
    unit_price: Decimal
    ext_amount: Decimal


@dataclass(frozen=True)
class Finding:
    """What a check found. Amounts are `Decimal`; the route renders them as strings."""

    affected_rows: int
    affected_amount: Decimal | None
    detail: dict[str, Any]
    example_row_numbers: tuple[int, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    """One check's whole answer, including the answer "I could not run".

    A result exists for every check on every run, which is what makes "generated from this
    file" provable: a check that examined 48,010 rows and found nothing is recorded as
    `clean` with that number, and a check that could not run says why.
    """

    code: str
    outcome: Outcome
    rows_examined: int = 0
    finding: Finding | None = None
    not_applicable_reason: str | None = None
    errored_message: str | None = None
    #: Facts the check wants recorded even when it found nothing - what it compared, what
    #: tolerance it used. Shown next to the finding so a human can judge it.
    context: dict[str, Any] = field(default_factory=dict[str, Any])


def clean(code: str, rows_examined: int, **context: Any) -> CheckResult:
    return CheckResult(code=code, outcome="clean", rows_examined=rows_examined, context=context)


def findings(code: str, rows_examined: int, finding: Finding, **context: Any) -> CheckResult:
    return CheckResult(
        code=code,
        outcome="findings",
        rows_examined=rows_examined,
        finding=finding,
        context=context,
    )


def not_applicable(code: str, reason: str, rows_examined: int = 0) -> CheckResult:
    """A check that could not run. **Not** a pass: the distinction is the whole point of
    recording outcomes, and a template would have no way to express it."""
    return CheckResult(
        code=code,
        outcome="not_applicable",
        rows_examined=rows_examined,
        not_applicable_reason=reason,
    )
