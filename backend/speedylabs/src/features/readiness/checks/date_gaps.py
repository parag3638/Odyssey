"""Business days inside the file's range with no invoices at all.

An absence, not a row-level defect: the finding has no example rows because the rows that
would prove it are the ones that are missing. `affected_rows` therefore counts **business
days**, which is what the oracle publishes and what `AFFECTED_UNIT` names.

The range is the file's own min and max invoice date. A file covering the 3rd to the 27th says
nothing about the 1st, and reporting the 1st would be inventing a gap outside the evidence.
Weekends are excluded by rule and holidays by data - see `business_days.py` for why a checker
that reported every quiet Saturday would be ignored wholesale.
"""

from __future__ import annotations

from datetime import date

from src.features.readiness.business_days import business_days_between
from src.features.readiness.codes import DATE_GAP
from src.features.readiness.rows import CheckResult, Finding, ReadinessRow, clean, findings

#: Dates listed in the finding. Enough to see the shape of a gap; the count carries the rest.
MAX_LISTED_DATES = 20


class DateGap:
    code = DATE_GAP

    def __init__(self, holidays: frozenset[date]) -> None:
        self._holidays = holidays
        self._seen: set[date] = set()
        self._first: date | None = None
        self._last: date | None = None
        self._examined = 0

    def examine(self, row: ReadinessRow) -> None:
        self._examined += 1
        day = row.invoice_date
        self._seen.add(day)
        self._first = day if self._first is None else min(self._first, day)
        self._last = day if self._last is None else max(self._last, day)

    def finish(self) -> CheckResult:
        if self._first is None or self._last is None:
            # No rows at all. Not a gap: there is no range to have a gap inside.
            return clean(self.code, self._examined)

        missing = [
            day
            for day in business_days_between(self._first, self._last, self._holidays)
            if day not in self._seen
        ]
        context = {
            "firstInvoiceDate": self._first.isoformat(),
            "lastInvoiceDate": self._last.isoformat(),
            "holidaysExcluded": len(self._holidays),
        }
        if not missing:
            return clean(self.code, self._examined, **context)

        return findings(
            self.code,
            self._examined,
            Finding(
                affected_rows=len(missing),
                # No dollar figure. The value of invoices that are not in the file is not a
                # number we have, and inventing an estimate would be the worst kind of
                # precision.
                affected_amount=None,
                detail={
                    "unit": "business days",
                    "missingDates": [day.isoformat() for day in missing[:MAX_LISTED_DATES]],
                    "missingDateCount": len(missing),
                    "firstInvoiceDate": self._first.isoformat(),
                    "lastInvoiceDate": self._last.isoformat(),
                },
                example_row_numbers=(),
            ),
            **context,
        )
