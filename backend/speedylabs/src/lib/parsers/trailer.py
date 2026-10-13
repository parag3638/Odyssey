"""Reading a fixed-width trailer's declared row count and total.

The trailer is the file's own statement of what it contains, and P8's
`footer_total_mismatch` check is the comparison between that statement and the detail
lines. Real ERP extracts disagree with themselves - usually because the trailer was
computed before a late adjustment - and the disagreement is exactly what a customer's IT
team needs told to them.

**Unrecognised beats guessed.** `parse_trailer` recognises one documented convention and
returns `None` for everything else, which becomes a `not_applicable` finding with a reason.
The alternative - hunting for "the last long run of digits" - would silently produce a
delta from a number that was never a total, and a wrong dollar figure in a checklist is
worse than an absent one, because somebody would go looking for it.

The convention (Harbor Point's AS/400 extract, `src/generator/writers.py`):

    TRLR 0000000500 000003017414725 <padding to the record width>
    ^marker  ^rows      ^total, 2 implied decimals

A trailing sign is accepted on the total because the same ERP writes credit amounts that
way elsewhere in the file; a leading minus is not, because this convention never emits one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

#: Record-type markers that introduce a trailer, longest first so `TRAILER` is not read as
#: `TRLR`-plus-rubbish. Matched case-insensitively. Kept in step with `fwf_reader`'s own
#: list, which decides *whether* the last record is a trailer; this module decides what it
#: says.
TRAILER_MARKERS: Final = ("TRAILER", "FOOTER", "TRLR", "END")

#: Widths the convention fixes. The total's final column is the **sign**, not a digit.
ROW_COUNT_WIDTH: Final = 10
TOTAL_WIDTH: Final = 15

#: Implied decimals in the total. The ERP writes cents with no point.
TOTAL_SCALE: Final = 2

#: What the sign column may hold. A blank means positive - which is why this parser reads
#: the body by **position** and never strips it: `rstrip()` eats the sign of every positive
#: total, and the first version of this file did exactly that and then refused the real
#: trailer as unrecognised.
SIGNS: Final = {" ": "", "+": "", "-": "-"}


@dataclass(frozen=True)
class TrailerValues:
    """What the trailer says about the file. Not what the file actually contains."""

    declared_rows: int
    total: Decimal


def _marker_length(record: str) -> int | None:
    upper = record.upper()
    for marker in TRAILER_MARKERS:
        if upper.startswith(marker):
            return len(marker)
    return None


def parse_trailer(record: str) -> TrailerValues | None:
    """The trailer's declared count and total, or `None` if it is not the known shape."""
    marker = _marker_length(record)
    if marker is None:
        return None

    body = record[marker : marker + ROW_COUNT_WIDTH + TOTAL_WIDTH]
    if len(body) < ROW_COUNT_WIDTH + TOTAL_WIDTH:
        return None

    rows, amount = body[:ROW_COUNT_WIDTH], body[ROW_COUNT_WIDTH:]
    digits, sign_column = amount[:-1], amount[-1]
    if not rows.isdigit() or not digits.isdigit() or sign_column not in SIGNS:
        return None

    # Scaled by shifting, never by dividing: `Decimal(digits) / 100` is exact here, but
    # writing it that way invites the same line to be copied somewhere a float appears.
    total = Decimal(f"{SIGNS[sign_column]}{digits}").scaleb(-TOTAL_SCALE)
    return TrailerValues(declared_rows=int(rows), total=total)
