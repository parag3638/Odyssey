"""Streaming fixed-width record count and width detection.

The record width is the file's most important structural fact: every field boundary P4
infers is an offset into it. It is taken as the **modal** line length rather than the
first line's, because a trailer record is legitimately shorter and taking line one would
be defeated by a file that happens to start with one.

A trailer is identified by its **record-type marker**, not by being shorter. That is
deliberate: our own writer pads the trailer to the full record width so that "every
record is exactly the declared width" is true of the whole file, which means length
cannot distinguish it. Real AS/400 extracts pad their trailers too.

So the reader looks for a conventional marker (`TRLR`, `TRAILER`, `FOOTER`, `END`) at
the start of the final record. This is a convention rather than a certainty: a file whose
trailer uses some other marker will count one row too many. That is visible - the count
is off by exactly one against the footer total - and P4 gives a human the override. The
alternative, guessing from the proportion of trailing spaces, would be wrong in ways
nobody could see.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from src.lib.errors import TooManyRows, Unparseable

#: Conventional record-type markers for a trailer record. Matched case-insensitively
#: against the start of the final record only.
TRAILER_MARKERS: tuple[str, ...] = ("TRLR", "TRAILER", "FOOTER", "END")


def looks_like_trailer(record: str) -> bool:
    upper = record.upper()
    return any(upper.startswith(marker) for marker in TRAILER_MARKERS)


@dataclass(frozen=True)
class FixedWidthShape:
    record_width: int
    #: Detail records only - any trailer is excluded.
    row_count: int
    has_trailer: bool
    #: The trailer record, verbatim, or `None`. Carried because **this** function is the
    #: only one that sees the end of the file: `fwf_infer.infer_layout` samples 5,000
    #: records, so on a 48,151-line export its "last record" is a detail line and it reports
    #: no trailer at all. The layout's footer facts have to come from here.
    trailer_record: str | None = None


def read_fixed_width_shape(path: Path, *, max_rows: int) -> FixedWidthShape:
    widths: Counter[int] = Counter()
    total = 0
    last_width = 0
    last_record = ""

    with path.open("r", encoding="ascii", newline="") as handle:
        for line in handle:
            stripped = line.rstrip("\n").rstrip("\r")
            if not stripped:
                continue
            total += 1
            last_width = len(stripped)
            last_record = stripped
            widths[last_width] += 1
            if total > max_rows + 1:  # +1 so a trailer cannot trip the cap by itself
                raise TooManyRows(
                    f"That file has more than {max_rows:,} rows. Split it by month and "
                    "upload each part."
                )

    if total == 0:
        raise Unparseable("That file has no records in it.")

    record_width = widths.most_common(1)[0][0]

    # Either a marker on the final record, or a final record that is simply shorter -
    # both are real conventions, and a file using the second is not wrong.
    has_trailer = total > 1 and (looks_like_trailer(last_record) or last_width != record_width)
    off_width = sum(count for width, count in widths.items() if width != record_width)
    if off_width > (1 if (has_trailer and last_width != record_width) else 0):
        raise Unparseable(
            f"Records are not all {record_width} characters wide "
            f"({off_width} differ). A fixed-width file with ragged records cannot be "
            "parsed reliably - re-export it."
        )

    return FixedWidthShape(
        record_width=record_width,
        row_count=total - (1 if has_trailer else 0),
        has_trailer=has_trailer,
        trailer_record=last_record if has_trailer else None,
    )


@dataclass(frozen=True)
class SliceField:
    """A field to cut out of each record. `start`/`end` are 1-based and inclusive."""

    name: str
    start: int
    end: int


def iter_fixed_width_rows(
    path: Path, fields: Sequence[SliceField]
) -> Iterator[tuple[list[str], Iterator[list[str]]]]:
    """Yield field names and a generator over sliced records, shaped like `iter_csv_rows`.

    Deliberately the same shape as the CSV iterator so `profile_rows` works on both
    without knowing which it has - the profiler's job is columns of strings, and which
    file format produced them is not its concern.

    Values are stripped. Padding is not part of a value: a profile that reported
    `"ACME CORP   "` and `"ACME CORP"` as two distinct values would be describing the
    column's width, not its contents. That padding existed is recorded on the layout as
    the `right_padded` quirk, so nothing is lost by stripping it here.

    A trailer is skipped, not sliced: its fields are totals in different positions, and
    folding it into the columns would corrupt every statistic.
    """
    names = [field.name for field in fields]

    def rows() -> Iterator[list[str]]:
        with path.open("r", encoding="ascii", newline="") as handle:
            for raw in handle:
                record = raw.rstrip("\n").rstrip("\r")
                if not record.strip() or looks_like_trailer(record):
                    continue
                yield [record[field.start - 1 : field.end].strip() for field in fields]

    yield names, rows()
