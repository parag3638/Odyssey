"""Which calendar days a distributor is expected to invoice on.

A gap check that counted every quiet Saturday would report ~100 findings on a six-month file
and teach its reader to ignore the whole checklist. So Saturday and Sunday are excluded by
rule, and a named holiday list is excluded by data - "no invoices on Independence Day" is not
a data problem.

The holidays here are US federal dates for the demo's window, which is a **decision a real
customer conversation would change**: a distributor shipping on Veterans Day belongs to a
different list, and the list is therefore passed in through `ReadinessContext` rather than
read from here by the checks.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Final

#: US federal holidays across the demo's 2026 window. Fixed dates and observed Mondays, as
#: the calendar actually falls - not computed, because a wrong rule is harder to see than a
#: wrong date.
OBSERVED_HOLIDAYS: Final[frozenset[date]] = frozenset(
    {
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # Martin Luther King Jr. Day
        date(2026, 2, 16),  # Washington's Birthday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day, observed (the 4th is a Saturday)
        date(2026, 9, 7),  # Labor Day
        date(2026, 10, 12),  # Columbus Day
        date(2026, 11, 11),  # Veterans Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas Day
    }
)

#: Monday-Friday. `date.weekday()` is 0-6 from Monday.
LAST_BUSINESS_WEEKDAY: Final = 4


def is_business_day(day: date, holidays: frozenset[date]) -> bool:
    return day.weekday() <= LAST_BUSINESS_WEEKDAY and day not in holidays


def business_days_between(first: date, last: date, holidays: frozenset[date]) -> Iterator[date]:
    """Every business day from `first` to `last` inclusive.

    The range is **the file's own** min and max invoice date, not a calendar month: a file
    covering the 3rd to the 27th says nothing about the 1st, and reporting the 1st as missing
    would be inventing a gap outside the evidence.
    """
    day = first
    while day <= last:
        if is_business_day(day, holidays):
            yield day
        day += timedelta(days=1)
