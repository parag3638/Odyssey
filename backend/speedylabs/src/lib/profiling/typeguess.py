"""Guessing a column's type from evidence, never from its name.

The name is the one thing we cannot trust - that is the premise of the whole project.
So every judgement here is made from the values.

A guess is made only when **every** non-null value fits. One `N/A` in a numeric column
makes it text, and that is the right answer: a column that is 99.9% integers is not an
integer column, it is an integer column with a problem, and calling it `integer` would
hide exactly the defect the readiness checklist exists to find.

Order matters. `boolean` is checked before `integer` because `0`/`1` are both; `integer`
before `decimal` because every integer parses as a decimal; `date` after the numerics
because `20260103` is also a valid integer, and the date reading is only preferred when
*every* value also falls in a plausible date range.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final

TYPE_EMPTY: Final = "empty"
TYPE_BOOLEAN: Final = "boolean"
TYPE_INTEGER: Final = "integer"
TYPE_DECIMAL: Final = "decimal"
TYPE_DATE: Final = "date"
TYPE_TEXT: Final = "text"

_BOOLEAN_TOKENS: Final = frozenset({"y", "n", "yes", "no", "true", "false", "t", "f", "0", "1"})

#: Date encodings this project actually meets. CYYMMDD is the AS/400 one; the rest are
#: what an ERP's CSV export tends to produce.
DATE_FORMAT_ISO: Final = "YYYY-MM-DD"
DATE_FORMAT_YYYYMMDD: Final = "YYYYMMDD"
DATE_FORMAT_CYYMMDD: Final = "CYYMMDD"


def _is_integer(value: str) -> bool:
    text = value.lstrip("+-")
    return text.isdigit() and text != ""


def _is_decimal(value: str) -> bool:
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return False
    # NaN and Infinity parse as Decimal and are never a column type (see lib/money).
    return parsed.is_finite()


def _valid_date_parts(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def date_format_of(value: str) -> str | None:
    """The date encoding this value fits, or None.

    CYYMMDD is only accepted for a leading `0`-`2`, which is what the convention
    defines (19xx, 20xx, 21xx). Without that bound, any 7-digit number would look like
    a date and every quantity column would be mis-typed.
    """
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        head, mid, tail = text[:4], text[5:7], text[8:]
        digits = head.isdigit() and mid.isdigit() and tail.isdigit()
        if digits and _valid_date_parts(int(head), int(mid), int(tail)):
            return DATE_FORMAT_ISO
        return None
    if len(text) == 8 and text.isdigit():
        if _valid_date_parts(int(text[:4]), int(text[4:6]), int(text[6:])):
            return DATE_FORMAT_YYYYMMDD
        return None
    if len(text) == 7 and text.isdigit() and text[0] in "012":
        year = 1900 + int(text[0]) * 100 + int(text[1:3])
        if _valid_date_parts(year, int(text[3:5]), int(text[5:])):
            return DATE_FORMAT_CYYMMDD
        return None
    return None


class TypeEvidence:
    """Running evidence for one column's type. Fixed size regardless of row count.

    Counters, not samples: a column of 50,000 values costs the same here as a column of
    five. The guess is made from the counters at the end, so nothing has to be kept.
    """

    def __init__(self) -> None:
        self.non_null = 0
        self.integers = 0
        self.decimals = 0
        self.booleans = 0
        #: date format -> how many values fitted it.
        self.date_formats: dict[str, int] = {}

    def add(self, value: str) -> None:
        self.non_null += 1
        if value.strip().lower() in _BOOLEAN_TOKENS:
            self.booleans += 1
        if _is_integer(value):
            self.integers += 1
        if _is_decimal(value):
            self.decimals += 1
        fmt = date_format_of(value)
        if fmt is not None:
            self.date_formats[fmt] = self.date_formats.get(fmt, 0) + 1

    @property
    def dominant_date_format(self) -> str | None:
        """The one date encoding every non-null value fits, if there is exactly one."""
        fitting = [fmt for fmt, count in self.date_formats.items() if count == self.non_null]
        if len(fitting) == 1:
            return fitting[0]
        # More than one format fits every value (an 8-digit string can be YYYYMMDD and
        # nothing else here, so this is rare) - prefer the most specific by name order
        # so the answer is deterministic rather than dict-order dependent.
        return sorted(fitting)[0] if fitting else None

    def guess(self) -> str:
        """The column's type, or `text` when the values do not agree.

        Every non-null value must fit. A single `N/A` in a numeric column yields `text`,
        which is the honest answer: the column is not an integer column, it is an integer
        column with a defect, and naming it `integer` would hide what the readiness
        checklist exists to surface.
        """
        if self.non_null == 0:
            return TYPE_EMPTY
        if self.booleans == self.non_null:
            return TYPE_BOOLEAN
        # Dates before the numerics: `20260103` parses as an integer too, and the date
        # reading only wins because *every* value also lands in a real calendar range.
        if self.dominant_date_format is not None:
            return TYPE_DATE
        if self.integers == self.non_null:
            return TYPE_INTEGER
        if self.decimals == self.non_null:
            return TYPE_DECIMAL
        return TYPE_TEXT
