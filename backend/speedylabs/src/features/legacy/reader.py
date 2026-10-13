"""Reading a legacy rebate export. Bytes in, rows or a typed refusal out.

Pure: no database, no HTTP, no session, no matching. Everything about the file's shape and its
number grammar lives here, and nothing else needs to know either.

**One documented shape, and no sniffer.** The spec puts "TrackMax+ and other formats Speedy already
parses natively" permanently out of scope, so this reads *one* small rebate-summary shape and
refuses everything else by design. It is not a step toward a general parser, and a test walks every
module in this package asserting none of them imports the mapping machinery, the spec machinery, the
profiler or the transform registry. If a real customer's export arrives in a shape this cannot read,
the answer is to document that shape - not to teach this module to guess.

The columns, as the committed fixture actually has them
-------------------------------------------------------
mvp.md describes a "four-field shape: program reference, period, amount, and a period-end date".
The generated file has **five** columns and **no period-end date**: `PERIOD` is `2026-01`, a month
string. The generator is the source of truth for what a legacy file looks like, so the documented
shape is adapted to the file.

===============  ==========  ===========================================
column           status      read into
===============  ==========  ===========================================
PROGRAM          required    the match key
PERIOD           required    both period bounds, derived
REBATE_PAID      required    the amount
PROGRAM_NAME     permitted   `raw`, and the unmatched panel's label
BASIS_UNITS      permitted   `raw` only - **never read**
anything else    refused     `legacy_unmapped`, naming both sets
===============  ==========  ===========================================

**`BASIS_UNITS` is permitted and never read**, and `LegacyRow` has no field for it, which is the
enforcement rather than a convention. The column does not contain the legacy system's quantity: in
`generator/legacy.py` the `units` dict is accumulated from *our* basis under the comment "true
totals, as the new calculation will produce them", and none of the five seeded causes adjust it. It
equals our `basisQty` in all 18 committed program-periods, which the narrative says is impossible.
So it is kept in `raw` as provenance and trusted by nothing.

**The amount grammar is strict, and three decimals is a refusal rather than a rounding.** This
module's job is to record what the customer's system said; quantising their number would change it.
A silently-zeroed legacy amount is worse still - it manufactures a 100% variance and corrupts the
one figure this whole phase exists to produce.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final

from src.lib.errors import LegacyUnmapped, Unparseable

COL_PROGRAM: Final = "PROGRAM"
COL_PERIOD: Final = "PERIOD"
COL_AMOUNT: Final = "REBATE_PAID"
COL_PROGRAM_NAME: Final = "PROGRAM_NAME"
COL_BASIS_UNITS: Final = "BASIS_UNITS"

#: Without all three there is nothing to compare.
REQUIRED_COLUMNS: Final = (COL_PROGRAM, COL_PERIOD, COL_AMOUNT)
#: Allowed to be present, and the only other names allowed to be present. `BASIS_UNITS` appears
#: here, and in `raw`, and nowhere else in this package - see the module docstring.
PERMITTED_COLUMNS: Final = (COL_PROGRAM_NAME, COL_BASIS_UNITS)

#: A legacy export is a summary: one row per program per period. The seeded file has 18. The cap
#: turns a pathological file into a named refusal rather than an unbounded read, and is published
#: rather than hidden so nobody has to read the source to find out why a file was refused.
MAX_LEGACY_ROWS: Final = 10_000

#: `2026-01`. Not a date - the file carries months, and a day would be a guess.
_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
#: Optional leading minus, no thousands separators, no currency symbol, no parenthesised negative,
#: at most two decimal places. Deliberately narrower than a spreadsheet's habits.
_AMOUNT = re.compile(r"^-?\d{1,15}(\.\d{1,2})?$")

_AMOUNT_SHAPE: Final = (
    "Amounts must look like 8930.71 or -100.00: digits, an optional leading minus, and at most two "
    "decimal places. No thousands separators and no currency symbols."
)


@dataclass(frozen=True)
class LegacyRow:
    """One row of the export, parsed and validated.

    Deliberately carries **no** basis, units or quantity field of any kind: see the module
    docstring on `BASIS_UNITS`.
    """

    #: The physical line in the file, so the first data row of a file with a header is 2. The
    #: convention `canonical_lines.source_row_number` uses, so a row number means one thing here.
    source_row_number: int
    program_external_ref: str
    #: The name the file carried, for the unmatched report. Never a matching key.
    program_name_text: str | None
    period_start: date
    period_end: date
    amount: Decimal
    #: Every column the file had, including the ones nothing reads. Provenance, not input.
    raw: dict[str, str]


def _month_end(start: date) -> date:
    following = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return date.fromordinal(following.toordinal() - 1)


def _check_header(header: list[str]) -> None:
    """Refuse anything that is not the documented shape, naming both sets.

    The message is the scope guard's whole point: it tells the reader what this importer accepts, so
    the answer to an unreadable file is to document its shape rather than to make this module guess.
    """
    seen = [name.strip() for name in header]
    # A repeated column name is neither missing nor unknown, so the two checks below both pass it -
    # and then the per-row dict keeps whichever value came last, dropping the other in silence.
    # That is the same "imports cleanly, carrying a wrong number" failure the field-count check
    # already closes, reached through the header instead of through a row.
    duplicates = sorted({name for name in seen if seen.count(name) > 1})
    if duplicates:
        raise LegacyUnmapped(
            f"That file repeats the column {', '.join(duplicates)}. Every column must appear once, "
            "or there is no way to tell which value is meant."
        )
    missing = [name for name in REQUIRED_COLUMNS if name not in seen]
    unknown = [name for name in seen if name not in (*REQUIRED_COLUMNS, *PERMITTED_COLUMNS)]
    if not missing and not unknown:
        return
    required = ", ".join(REQUIRED_COLUMNS)
    permitted = ", ".join(PERMITTED_COLUMNS)
    if missing:
        detail = f"It is missing {', '.join(missing)}."
    else:
        detail = f"It has {', '.join(unknown)}, which this import does not read."
    raise LegacyUnmapped(
        f"That file is not a legacy rebate export this import understands. {detail} "
        f"A legacy export needs {required}, and may also carry {permitted}."
    )


def _period(value: str, row_number: int) -> tuple[date, date]:
    match = _PERIOD.match(value.strip())
    if match is None:
        raise Unparseable(
            f"Row {row_number}: {value!r} is not a period. Periods look like 2026-01 - "
            "a four-digit year, a dash, and a two-digit month."
        )
    start = date(int(match.group(1)), int(match.group(2)), 1)
    return start, _month_end(start)


def _amount(value: str, row_number: int) -> Decimal:
    text = value.strip()
    if not _AMOUNT.match(text):
        raise Unparseable(
            f"Row {row_number}: {value!r} is not an amount this import can read. {_AMOUNT_SHAPE}"
        )
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - the grammar already excludes this
        raise Unparseable(f"Row {row_number}: {value!r} is not an amount. {_AMOUNT_SHAPE}") from exc


def read(content: bytes) -> list[LegacyRow]:
    """Parse a legacy rebate export, or raise.

    Refuses the **whole file** rather than skipping a bad row, unlike the ERP materialiser which
    records per-row rejections. The two are different on purpose: an invoice file is tens of
    thousands of lines where one bad row is noise, while this is a summary of eighteen totals where
    a missing one silently changes the comparison. A partial legacy import produces a variance table
    that looks complete and is not.
    """
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Unparseable("That legacy export is not UTF-8 text.") from exc

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise Unparseable("That legacy export is empty.") from None
    _check_header(header)

    names = [name.strip() for name in header]
    rows: list[LegacyRow] = []
    # Starts at 2: the header is line 1, so the first data row is the second physical line.
    for row_number, record in enumerate(reader, start=2):
        if not any(cell.strip() for cell in record):
            continue
        if len(rows) >= MAX_LEGACY_ROWS:
            raise Unparseable(
                f"That legacy export has more than {MAX_LEGACY_ROWS:,} rows. A rebate summary is "
                "one row per program per period; this looks like a different kind of file."
            )
        if len(record) != len(names):
            # A row whose field count differs from the header is refused, never zipped to the
            # names it happens to line up with. Found by measurement: an unquoted `1,234.50`
            # splits into two fields, every later value shifts by one, and the amount is read
            # from the wrong cell - the file imports cleanly carrying a wrong number, which is
            # the exact failure this module's strictness exists to prevent.
            raise Unparseable(
                f"Row {row_number} has {len(record)} values where the header names "
                f"{len(names)}. Check for an unquoted comma inside a value."
            )
        raw = {name: record[index].strip() for index, name in enumerate(names)}
        ref = raw.get(COL_PROGRAM, "")
        if not ref:
            raise Unparseable(
                f"Row {row_number}: the {COL_PROGRAM} column is empty. Every row needs the "
                "program reference it was paid under."
            )
        start, end = _period(raw.get(COL_PERIOD, ""), row_number)
        name_text = raw.get(COL_PROGRAM_NAME) or None
        rows.append(
            LegacyRow(
                source_row_number=row_number,
                program_external_ref=ref,
                program_name_text=name_text,
                period_start=start,
                period_end=end,
                amount=_amount(raw.get(COL_AMOUNT, ""), row_number),
                raw=raw,
            )
        )

    if not rows:
        raise Unparseable("That legacy export has no data rows.")
    return rows
