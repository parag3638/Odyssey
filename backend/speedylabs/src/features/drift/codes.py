"""The drift vocabulary. Nine codes, each with a reachable honest finding.

The set is a one-way door: it reaches the API, the UI's filter and a stored row that a decision was
recorded against. Two codes mvp.md proposes are **absent**, and both absences were decided by
measurement rather than by taste:

* **`boundary_shifted`** is unreachable. Boundary inference cuts on a 99% blank histogram, so
  `month2_nodrift_invoices.txt` - byte-identical in layout to month 1 - infers 14 fields where month
  1 infers 12, with 11 of 12 ranges different. A positional inferred-vs-inferred diff would
  report 11
  shifts on a file whose layout did not change. A fixed-width width change emits
  `record_width_changed` instead, and the field editor's ruler is the side-by-side.
* **`pattern_changed`** was rejected on evidence too: `BILTNM`'s dominant signature differs between
  months because a different *customer name* happens to be most common, which is not drift.

Severity is a per-finding decision, not a per-code one, for the four structural codes: a column the
spec's mapping **cites** is one a canonical field reads, so losing or retyping it breaks the
mapping;
an uncited one is a note. That distinction is read from the spec document, never guessed.
"""

from __future__ import annotations

from typing import Final, Literal

Severity = Literal["blocking", "warning"]
Outcome = Literal["findings", "clean", "not_applicable", "not_comparable", "errored"]

COLUMN_ADDED: Final = "column_added"
COLUMN_REMOVED: Final = "column_removed"
COLUMN_RENAMED: Final = "column_renamed"
TYPE_CHANGED: Final = "type_changed"
RECORD_WIDTH_CHANGED: Final = "record_width_changed"
NULL_RATE_SPIKE: Final = "null_rate_spike"
VALUE_MIX_SHIFT: Final = "value_mix_shift"
MEAN_SHIFT: Final = "mean_shift"
NEW_ENUM_VALUE: Final = "new_enum_value"

#: Structural first, then distributional - the order a reader needs them in, since a structural
#: change can explain every distributional one below it.
DRIFT_CODES: Final = (
    RECORD_WIDTH_CHANGED,
    COLUMN_REMOVED,
    COLUMN_RENAMED,
    COLUMN_ADDED,
    TYPE_CHANGED,
    NULL_RATE_SPIKE,
    VALUE_MIX_SHIFT,
    MEAN_SHIFT,
    NEW_ENUM_VALUE,
)

#: Codes that can **never** block, whatever they find.
#:
#: Every distributional code is here, and that is the load-bearing decision of the phase. Measured:
#: `month2_nodrift_invoices.*` - the control, whose layout is byte-identical to month 1's - carries
#: month 2's *data*, so it shows `value_mix_shift` on `UMCODE` at 3.59x and `mean_shift` on `SHPWGT`
#: at 1.73x, **the same margins as the drifted file**. If a distribution check could block, every
#: month would block on ordinary seasonal variation, A3 would break alongside A4, and the gate would
#: be the thing users learned to click past.
ALWAYS_WARNING: Final = frozenset(
    {NULL_RATE_SPIKE, VALUE_MIX_SHIFT, MEAN_SHIFT, NEW_ENUM_VALUE, COLUMN_ADDED}
)

#: Codes that always block when they find anything.
ALWAYS_BLOCKING: Final = frozenset({RECORD_WIDTH_CHANGED})

#: The rest block only when the column is cited by the baseline spec's mapping.
CITED_SENSITIVE: Final = frozenset({COLUMN_REMOVED, COLUMN_RENAMED, TYPE_CHANGED})

#: Which codes each comparison stage owns. A stage that raises may only mark **its own** codes
#: errored: the first version marked every code not yet seen, and since the stages run in sequence
#: the second stage then appended its four codes again - a duplicate `drift_code` per run, which
#: `drift_checks_run_code_key` refuses, aborting the transaction the profile job shares with its own
#: writes. Found by the Phase 8-10 tester.
STRUCTURAL_CODES: Final = (
    RECORD_WIDTH_CHANGED,
    COLUMN_REMOVED,
    COLUMN_RENAMED,
    COLUMN_ADDED,
    TYPE_CHANGED,
)
DISTRIBUTION_CODES: Final = (NULL_RATE_SPIKE, VALUE_MIX_SHIFT, MEAN_SHIFT, NEW_ENUM_VALUE)

TITLES: Final[dict[str, str]] = {
    COLUMN_ADDED: "A column this file has and the approved file did not",
    COLUMN_REMOVED: "A column the approved file had and this file does not",
    COLUMN_RENAMED: "A column that appears to have been renamed",
    TYPE_CHANGED: "A column whose values changed kind",
    RECORD_WIDTH_CHANGED: "The records are a different width",
    NULL_RATE_SPIKE: "A column that is much emptier, or much fuller, than before",
    VALUE_MIX_SHIFT: "A column whose values are distributed differently",
    MEAN_SHIFT: "A column whose average moved",
    NEW_ENUM_VALUE: "A value this column has not carried before",
}


def severity_for(code: str, *, cited: bool) -> Severity:
    """Blocking or warning for one finding.

    `cited` decides the three structural codes that can go either way: a column a canonical field
    reads is one the mapping breaks without.
    """
    if code in ALWAYS_BLOCKING:
        return "blocking"
    if code in ALWAYS_WARNING:
        return "warning"
    if code in CITED_SENSITIVE:
        return "blocking" if cited else "warning"
    return "warning"


if set(STRUCTURAL_CODES) | set(DISTRIBUTION_CODES) != set(DRIFT_CODES):
    # Import-time: a code owned by neither stage would never be recorded, and one owned by both
    # would be recorded twice - which is the bug this split exists to make impossible.
    raise RuntimeError("the drift stages do not own exactly the drift codes between them")
if set(STRUCTURAL_CODES) & set(DISTRIBUTION_CODES):
    raise RuntimeError("a drift code is owned by both stages")
if set(DRIFT_CODES) != set(TITLES):
    raise RuntimeError("drift codes and titles disagree")
if ALWAYS_WARNING & ALWAYS_BLOCKING or ALWAYS_WARNING & CITED_SENSITIVE:
    # Import-time: a code in two severity classes would make `severity_for` depend on dict order.
    raise RuntimeError("a drift code is in more than one severity class")
