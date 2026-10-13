"""Apply a mapping to rows, one row at a time, reporting failures per row.

Shared by validation (5.3), the both-formats equivalence proof (5.6) and later the
materialiser (P7), which is the reason it is a module of its own rather than a private
helper inside the validator: those three must agree about what a mapping *means*, and the
only way to guarantee that is for them to call the same function.

One bad value costs one row. A transform raises `RuleBroken` naming the offending value;
this catches it per field, records it against the row, and carries on. A file where row
412 has a malformed date still tells you everything about the other 49,999 rows, which is
the difference between a report a customer can act on and a dead end.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field

from src.features.mapping.transforms import Transform, TransformOutput
from src.features.mapping.transforms import apply as apply_transform
from src.lib.errors import RuleBroken


@dataclass(frozen=True)
class FieldMapping:
    """One canonical field's instruction: which source column, read which way."""

    source_column: str
    transform: Transform


@dataclass(frozen=True)
class AppliedRow:
    """One source row, read as canonical values.

    `row_number` is the physical line number in the source file - what a person sees in
    an editor - so a failure can be pointed at.
    """

    row_number: int
    values: dict[str, TransformOutput] = field(default_factory=dict[str, TransformOutput])
    #: canonical field -> the message from the transform that refused it.
    failures: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def ok(self) -> bool:
        return not self.failures


def apply_mapping(
    header: list[str],
    rows: Iterable[list[str]],
    mapping: Mapping[str, FieldMapping],
    *,
    limit: int | None = None,
    first_row_number: int = 2,
) -> Iterator[AppliedRow]:
    """Yield one `AppliedRow` per source row.

    `first_row_number` defaults to 2 because a CSV's first data row is physical line 2;
    a fixed-width file has no header and passes 1.

    A canonical field whose `sourceColumn` is not in the header is skipped rather than
    failed per row: that is a mapping error, not a data error, and the routes refuse it
    with `422 unknown_source_column` long before anything is applied. Skipping keeps this
    function honest if it is ever called with a stale mapping - the field simply has no
    values, which the validator then reports as unmapped rather than as 50,000 failures.
    """
    index_of = {name: position for position, name in enumerate(header)}
    # Resolved once, not per row: a 50,000-row file would otherwise do this 50,000 times.
    resolved = [
        (canonical_field, index_of[instruction.source_column], instruction.transform)
        for canonical_field, instruction in mapping.items()
        if instruction.source_column in index_of
    ]

    for offset, row in enumerate(rows):
        if limit is not None and offset >= limit:
            return
        values: dict[str, TransformOutput] = {}
        failures: dict[str, str] = {}
        for canonical_field, position, transform in resolved:
            raw = row[position] if position < len(row) else ""
            try:
                values[canonical_field] = apply_transform(transform, raw)
            except RuleBroken as error:
                failures[canonical_field] = str(error)
        yield AppliedRow(row_number=first_row_number + offset, values=values, failures=failures)


def canonical_values(row: AppliedRow) -> dict[str, TransformOutput]:
    """Normalise an applied row to canonical types.

    Two renderings of the same file arrive at the same canonical line by different
    routes: a CSV already carries `"9.00"`, while the ERP's own fixed-width output packs
    it as `"00000900"` with an implied scale. Both land on `Decimal("9.00")` only after
    this step - which is why it lives here, beside `apply_mapping`, rather than inside
    whichever caller happened to need it first. 5.6's equivalence proof and P7's
    materialiser both read it.

    Absent is `None`, never `""`: `canonical/FORMAT.md` says an empty field means absent,
    and a transform that hands back a trimmed empty string has not said otherwise.
    """
    from src.features.mapping.validators import (
        DECIMAL_SCALES,
        as_canonical_decimal,
        is_absent,
    )

    values: dict[str, TransformOutput] = {}
    for name, value in row.values.items():
        if is_absent(value):
            values[name] = None
        elif name in DECIMAL_SCALES:
            values[name] = as_canonical_decimal(value)
        else:
            values[name] = value
    return values
