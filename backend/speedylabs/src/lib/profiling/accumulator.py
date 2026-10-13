"""Per-column statistics, accumulated in one pass with bounded memory.

The shape of this module is driven by one requirement: profiling a 50,000-row file with
ninety columns must not grow with the data. So nothing here keeps values - it keeps
counters, a capped set of digests, five truncated samples and a capped signature counter.
Peak memory per column is a few hundred kilobytes whatever arrives.

Two specific hostile cases it has to survive, both from the plan's own criteria:

- **A high-cardinality column.** 20,000 distinct values against a 10,000 cap reports
  `distinct_capped` and stops growing. An exact count past that buys nothing for mapping
  and costs memory without limit.
- **A 2 MB cell.** The stored sample is 64 characters and the full value is never
  retained: the distinct set holds a 16-byte digest, not the text, so a column of
  thousand-page cells costs the same as a column of product codes.

The digest is what makes both true at once. Storing truncated *values* in the distinct
set would have made two different 2 MB cells with the same first 64 characters look
identical, understating the count; a digest of the whole value keeps the count exact
while keeping the memory flat.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from src.lib.profiling.patterns import PatternCounter
from src.lib.profiling.typeguess import TYPE_EMPTY, TypeEvidence

#: Exact distinct counts up to here, then reported as capped.
DISTINCT_CAP: Final = 10_000

#: How much of a value is kept for display. 64 characters is enough to recognise a code
#: or a name and short enough that ninety columns of samples stay readable.
SAMPLE_LENGTH: Final = 64

#: How many sample values to show per column.
SAMPLE_COUNT: Final = 5

#: Distinct values a column may have before per-value counts stop being kept. Five above P10's
#: enumeration test of 20, deliberately: a column with 20-25 distinct values can then answer "not an
#: enumeration" *from recorded data* rather than from an absence, which is the difference between a
#: `clean` check and a `not_comparable` one.
VALUE_COUNT_CAP: Final = 25

#: 16 bytes per distinct value: at the 10,000 cap that is 160 KB per column, and it does
#: not grow with cell size.
_DIGEST_BYTES: Final = 16


def truncate(value: str) -> str:
    """Shorten for display, marking that it was shortened."""
    if len(value) <= SAMPLE_LENGTH:
        return value
    return value[: SAMPLE_LENGTH - 1] + "…"


def _digest(value: str) -> bytes:
    return hashlib.blake2b(value.encode("utf-8", "replace"), digest_size=_DIGEST_BYTES).digest()


@dataclass(frozen=True)
class ColumnProfile:
    """One column's finished profile, ready to store or serialise."""

    column_index: int
    column_name: str
    type_guess: str
    row_count: int
    null_count: int
    null_pct: Decimal
    distinct_count: int
    distinct_capped: bool
    min_text: str | None
    max_text: str | None
    sample_values: list[str]
    patterns: list[dict[str, object]]
    #: The date encoding every value fitted, when the type is `date`. Recorded because
    #: P5's transform needs to know *which* encoding to decode, and re-deriving it later
    #: would mean reading the file again.
    date_format: str | None
    #: Exact per-value counts, but **only** for a column with at most `VALUE_COUNT_CAP` distinct
    #: non-null values. `None` above that, which is not the same as "no values": P10's enumeration
    #: and mix-share checks need to tell a legitimately-capped column ("not an enumeration") from a
    #: profile written before this existed ("cannot compare"), and `distinct_count` is what
    #: separates them. Bounded by the cap, so it never grows with the row count.
    value_counts: dict[str, int] | None = None
    #: How many non-null values parsed as a finite number, and their exact sum. A mean needs both:
    #: storing only the mean would throw away the count a reader needs to trust it. `Decimal`,
    #: because every money rule in this project applies here too.
    numeric_count: int = 0
    numeric_sum: Decimal = Decimal(0)

    def to_api(self) -> dict[str, Any]:
        return {
            "columnIndex": self.column_index,
            "columnName": self.column_name,
            "typeGuess": self.type_guess,
            "rowCount": self.row_count,
            "nullCount": self.null_count,
            # A decimal string, not a float: §3 sends ratios as decimal strings.
            "nullPct": f"{self.null_pct:f}",
            "distinctCount": self.distinct_count,
            "distinctCapped": self.distinct_capped,
            "minText": self.min_text,
            "maxText": self.max_text,
            "sampleValues": self.sample_values,
            "patterns": self.patterns,
            "dateFormat": self.date_format,
            "valueCounts": self.value_counts,
            "numericCount": self.numeric_count,
            # A decimal string, like every other amount on the wire (§3). The *mean* is not sent:
            # a client that wants one divides by the count, and sending a pre-divided number would
            # hide how many values it came from.
            "numericSum": f"{self.numeric_sum:f}",
        }


def _as_number(value: str) -> Decimal | None:
    """The value as an exact number, or `None` if it is not one.

    Skipped, never coerced: a `"N/A"` counted as zero would drag a mean towards it silently, which
    is the whole class of error this project exists to catch. `NaN` and `Infinity` parse as
    `Decimal` and are deliberately not numbers here - the same rule `lib/money` applies.
    """
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


class ColumnAccumulator:
    """Ingest one column's values one at a time; ask for a profile at the end."""

    def __init__(self, column_index: int, column_name: str) -> None:
        self.column_index = column_index
        self.column_name = column_name
        self._rows = 0
        self._nulls = 0
        self._digests: set[bytes] = set()
        self._distinct_capped = False
        self._samples: list[str] = []
        self._seen_samples: set[bytes] = set()
        self._min: str | None = None
        self._max: str | None = None
        self._patterns = PatternCounter()
        self._evidence = TypeEvidence()
        #: Per-value counts while the column still looks like an enumeration. Dropped - and the
        #: flag set - the moment it exceeds the cap, so memory is bounded by the cap rather than by
        #: the data. Keys are truncated for the same reason `min_text` is: a 2 MB cell must not be
        #: retained here either.
        self._value_counts: dict[str, int] = {}
        self._value_counts_capped = False
        self._numeric_count = 0
        self._numeric_sum = Decimal(0)

    def add(self, raw: str | None) -> None:
        self._rows += 1

        # A null and an empty string are the same absence in a text export: a CSV cannot
        # distinguish them, and a fixed-width field of spaces is blank, not "the string
        # of spaces". Treating them differently would split one column's statistics in
        # two for no reader benefit.
        value = "" if raw is None else raw.strip()
        if value == "":
            self._nulls += 1
            return

        self._evidence.add(value)
        self._patterns.add(value)

        if not self._distinct_capped:
            if len(self._digests) < DISTINCT_CAP:
                self._digests.add(_digest(value))
            else:
                self._distinct_capped = True

        if len(self._samples) < SAMPLE_COUNT:
            # Distinct samples: five copies of the same code teaches nothing.
            marker = _digest(value)
            if marker not in self._seen_samples:
                self._seen_samples.add(marker)
                self._samples.append(truncate(value))

        if not self._value_counts_capped:
            key = truncate(value)
            if key in self._value_counts:
                self._value_counts[key] += 1
            elif len(self._value_counts) < VALUE_COUNT_CAP:
                self._value_counts[key] = 1
            else:
                # Past the cap: this is not an enumeration, and holding a partial tally would be
                # worse than holding none - a mix share computed from 25 of 20,000 values would
                # look like an answer.
                self._value_counts_capped = True
                self._value_counts = {}

        number = _as_number(value)
        if number is not None:
            self._numeric_count += 1
            self._numeric_sum += number

        # Lexicographic, on the truncated form, so a 2 MB cell cannot be retained here
        # either. The bound is for display, and a truncated comparison is the honest
        # thing to show next to it.
        short = truncate(value)
        if self._min is None or short < self._min:
            self._min = short
        if self._max is None or short > self._max:
            self._max = short

    def finish(self) -> ColumnProfile:
        type_guess = self._evidence.guess()
        null_pct = (
            Decimal(self._nulls) / Decimal(self._rows) if self._rows else Decimal(0)
        ).quantize(Decimal("0.000001"))
        return ColumnProfile(
            column_index=self.column_index,
            column_name=self.column_name,
            type_guess=type_guess,
            row_count=self._rows,
            null_count=self._nulls,
            null_pct=null_pct,
            distinct_count=len(self._digests),
            distinct_capped=self._distinct_capped,
            min_text=self._min,
            max_text=self._max,
            sample_values=list(self._samples),
            patterns=self._patterns.top(),
            date_format=(self._evidence.dominant_date_format if type_guess != TYPE_EMPTY else None),
            value_counts=None if self._value_counts_capped else dict(self._value_counts),
            numeric_count=self._numeric_count,
            numeric_sum=self._numeric_sum,
        )


def profile_rows(
    column_names: list[str], rows: Any, *, max_rows: int | None = None
) -> list[ColumnProfile]:
    """Profile an iterable of row sequences in a single pass.

    A pure function over an iterator: it does not know whether the rows came from a CSV,
    a fixed-width file or a database, which is what lets P10's drift detection reuse it
    unchanged. `rows` is typed loosely on purpose - any iterable of sequences will do.
    """
    accumulators = [ColumnAccumulator(index, name) for index, name in enumerate(column_names)]
    for seen, row in enumerate(rows):
        if max_rows is not None and seen >= max_rows:
            break
        for index, accumulator in enumerate(accumulators):
            accumulator.add(row[index] if index < len(row) else None)
    return [accumulator.finish() for accumulator in accumulators]
