"""What a drift comparison reads, and what it returns. Pure data.

The comparison is a function of two **profiles**, never of two files: the baseline file's
bytes may be gone (`approved_from_source_file_id` is `on delete set null`), and re-reading
rows on a request path is O(rows) for an answer the profiler already computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.features.drift.codes import Outcome, Severity


@dataclass(frozen=True)
class ColumnStats:
    """One column's profile, as drift reads it."""

    column_index: int
    column_name: str
    type_guess: str
    row_count: int
    null_count: int
    null_pct: Decimal
    distinct_count: int
    distinct_capped: bool
    value_counts: dict[str, int] | None
    numeric_count: int | None
    numeric_sum: Decimal | None

    @property
    def mean(self) -> Decimal | None:
        """The arithmetic mean of this column's numeric values, or `None`.

        Computed here rather than stored, so a reader always sees the count it came from. `None`
        when nothing parsed as a number, which is different from a mean of zero.
        """
        if not self.numeric_count or self.numeric_sum is None:
            return None
        return self.numeric_sum / Decimal(self.numeric_count)

    def share(self, value: str) -> Decimal | None:
        """What fraction of the non-null values are this one, or `None` if unknowable."""
        if self.value_counts is None:
            return None
        total = sum(self.value_counts.values())
        if total == 0:
            return Decimal(0)
        return Decimal(self.value_counts.get(value, 0)) / Decimal(total)


@dataclass(frozen=True)
class DriftFinding:
    drift_code: str
    severity: Severity
    column_name: str | None
    baseline_column_name: str | None
    detail: dict[str, Any]


@dataclass(frozen=True)
class DriftCheck:
    """One check's whole answer, including "I could not run".

    `not_comparable` is the outcome this phase needed that P8 did not: a check can be applicable in
    principle and still have nothing to compare, because the baseline profile predates the statistic
    it needs. Calling that `clean` would claim a comparison nobody made.
    """

    drift_code: str
    outcome: Outcome
    reason: str | None = None
    columns_examined: int = 0
    findings: tuple[DriftFinding, ...] = ()


@dataclass(frozen=True)
class ComparisonInput:
    """Everything a comparison needs. Assembled by the caller; no session, no file, no clock."""

    baseline: tuple[ColumnStats, ...]
    current: tuple[ColumnStats, ...]
    #: Source columns the baseline spec's mapping cites, which is what makes a structural finding
    #: blocking rather than a note.
    cited_columns: frozenset[str] = frozenset()
    kind: str = "csv"
    #: Set only for a fixed-width file whose record width changed, promoted from P9's own fit check
    #: rather than recomputed - there is one decision about the width and it already happened.
    record_width_change: tuple[int, int] | None = None
    #: Columns the comparison could not line up at all, with the reason. A fixed-width file whose
    #: width changed is the whole-file case of this.
    not_comparable_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict[str, Any])
