"""The three rebate vocabularies and the sentences that explain them. A one-way door.

These strings reach `fixtures/oracle/expected_rebate_calcs.json`, the API, the UI's badges and
P13's attribution rules, so the sets are fixed and additions are additive (BACKEND.md §3).

**Why `out_of_basis_scope` is not a kind of `skipped`.** A per-pound billback pays on pounds, and
this distributor also buys 12,666 cases of that same brand. Those case lines are not defects, not
warnings and not anybody's problem - the program was never about them. Measured over the committed
fixture, calling them skipped reports **17,550 skipped lines** where **195** are real, which buries
the 195 that someone has to act on. `skipped` means "this program should have paid on this line and
the data would not say how much"; `out_of_basis_scope` means "wrong unit, nothing to pay".

The import-time guard is the readiness pattern: a status with no sentence is a status that cannot be
reported, and finding that out when a user opens the drill-down is too late.
"""

from __future__ import annotations

from typing import Final, Literal

LineStatus = Literal["contributed", "skipped", "out_of_basis_scope"]
Outcome = Literal["calculated", "no_lines_matched", "out_of_effective_range", "errored"]

CONTRIBUTED: Final = "contributed"
SKIPPED: Final = "skipped"
OUT_OF_BASIS_SCOPE: Final = "out_of_basis_scope"

LINE_STATUSES: Final = (CONTRIBUTED, SKIPPED, OUT_OF_BASIS_SCOPE)

#: Why a line the program *should* have paid on could not be computed. Both of these are seeded
#: defects P13 prices: `missing_pack_size` is its `case_each_conversion` (125 lines) and
#: `missing_catch_weight` is its own cause (70 lines).
MISSING_PACK_SIZE: Final = "missing_pack_size"
MISSING_CATCH_WEIGHT: Final = "missing_catch_weight"

SKIPPED_REASONS: Final = (MISSING_PACK_SIZE, MISSING_CATCH_WEIGHT)

CALCULATED: Final = "calculated"
NO_LINES_MATCHED: Final = "no_lines_matched"
OUT_OF_EFFECTIVE_RANGE: Final = "out_of_effective_range"
ERRORED: Final = "errored"

OUTCOMES: Final = (CALCULATED, NO_LINES_MATCHED, OUT_OF_EFFECTIVE_RANGE, ERRORED)

LINE_STATUS_TITLES: Final[dict[str, str]] = {
    CONTRIBUTED: "Earned a rebate",
    SKIPPED: "Could not be computed",
    OUT_OF_BASIS_SCOPE: "Not in this program's unit",
}

#: DESIGN.md §9: what happened and how to fix it, no apology. These are shown per line in the
#: drill-down sheet, so they name the field the customer has to change.
SKIPPED_REASON_SENTENCES: Final[dict[str, str]] = {
    MISSING_PACK_SIZE: (
        "Priced by the each with no pack size, so the case count cannot be worked out. "
        "Add the pack size for this item."
    ),
    MISSING_CATCH_WEIGHT: (
        "A catch-weight item with no weight on the line, so there are no pounds to pay on. "
        "Add the shipped weight for this line."
    ),
}

OUTCOME_SENTENCES: Final[dict[str, str]] = {
    CALCULATED: "Calculated.",
    NO_LINES_MATCHED: (
        "No lines on this file are for this program's manufacturer. Check the brand column's "
        "mapping, or the manufacturer list."
    ),
    OUT_OF_EFFECTIVE_RANGE: (
        "This program was not in effect during any period on this file. Check its effective dates."
    ),
    ERRORED: "The calculation did not finish for this program. Start it again.",
}

if (
    set(LINE_STATUSES) != set(LINE_STATUS_TITLES)
    or set(SKIPPED_REASONS) != set(SKIPPED_REASON_SENTENCES)
    or set(OUTCOMES) != set(OUTCOME_SENTENCES)
):
    raise RuntimeError("rebate statuses, reasons, outcomes and their sentences disagree")
