"""Streaming CSV row count, with the row cap enforced as we go.

Streaming matters for the cap. Loading a 300,000-row file to discover it exceeds a
250,000-row limit is the resource exhaustion the limit exists to prevent, so the count
is checked per row and the read abandoned the moment it is breached.

A ragged row - more or fewer fields than the header - is refused with its line number.
`BACKEND.md` §6 says errors say what happened and how to fix it, and "row 412 has 23
fields, the header has 21" is something a customer's IT team can act on. Silently
padding or truncating would hide a real export defect and corrupt every number
downstream.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from src.lib.errors import TooManyRows, Unparseable

#: Delimiters the sniffer is allowed to consider. Left unrestricted, `csv.Sniffer` will
#: happily decide a column of dates is tab-delimited because a digit recurs in a fixed
#: position. These four are what an ERP actually exports.
CANDIDATE_DELIMITERS: Final = ",;\t|"

#: Bytes of the file handed to the sniffer. Enough for the header plus many rows, which
#: is what the sniffer's consistency check needs, without reading a 60 MB file to decide
#: on one character.
SNIFF_BYTES: Final = 8_192


@dataclass(frozen=True)
class CsvDialect:
    delimiter: str
    quote_char: str


def sniff_dialect(path: Path) -> CsvDialect:
    """Detect the delimiter and quote character, falling back to comma.

    The fallback is not a guess dressed up as a detection: a one-column file has no
    delimiter to find, and `csv.Sniffer` raises rather than returning. Comma is then
    both the conventional default and indistinguishable from any other answer, because
    there is nothing in the file to delimit.
    """
    with path.open("r", encoding="ascii", newline="") as handle:
        head = handle.read(SNIFF_BYTES)
    try:
        sniffed = csv.Sniffer().sniff(head, delimiters=CANDIDATE_DELIMITERS)
    except csv.Error:
        return CsvDialect(delimiter=",", quote_char='"')
    return CsvDialect(delimiter=sniffed.delimiter, quote_char=sniffed.quotechar or '"')


@dataclass(frozen=True)
class CsvShape:
    header: list[str]
    #: Data rows only - the header is not one.
    row_count: int
    dialect: CsvDialect


def read_csv_shape(path: Path, *, max_rows: int) -> CsvShape:
    dialect = sniff_dialect(path)
    with path.open("r", encoding="ascii", newline="") as handle:
        reader = csv.reader(handle, delimiter=dialect.delimiter, quotechar=dialect.quote_char)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise Unparseable("That file has no header row.") from exc

        expected = len(header)
        count = 0
        for row in reader:
            # csv yields [] for a blank line; a trailing newline is not a defect.
            if not row:
                continue
            if len(row) != expected:
                # `reader.line_num` is the physical line, which is what a person
                # looking at the file in an editor will see.
                raise Unparseable(
                    f"Line {reader.line_num} has {len(row)} fields but the header has "
                    f"{expected}. Fix that line, or re-export the file."
                )
            count += 1
            if count > max_rows:
                raise TooManyRows(
                    f"That file has more than {max_rows:,} rows. Split it by month and "
                    "upload each part."
                )
    return CsvShape(header=header, row_count=count, dialect=dialect)


def iter_csv_rows(path: Path) -> Iterator[tuple[list[str], Iterator[list[str]]]]:
    """Yield the header and a generator over the data rows, once.

    Shaped this way so the caller gets the column names before deciding what to do with
    the rows, without the reader having to buffer any of them. Used by the profiler,
    which needs one pass and bounded memory.
    """
    dialect = sniff_dialect(path)
    with path.open("r", encoding="ascii", newline="") as handle:
        reader = csv.reader(handle, delimiter=dialect.delimiter, quotechar=dialect.quote_char)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise Unparseable("That file has no header row.") from exc
        yield header, (row for row in reader if row)
