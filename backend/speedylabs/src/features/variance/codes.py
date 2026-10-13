"""The variance vocabularies: why a difference exists, and which side it came from. A one-way door.

These strings reach the API, the UI's filter and badges, and - for `root_cause` - the committed
`fixtures/oracle/expected_variance_attribution.json`, so the sets are fixed and additions are
additive (BACKEND.md §3).

**`root_cause` is defined here although P13 is what writes it.** The column and its default live in
migration 0026, so the vocabulary belongs with them; P13 then writes values rather than migrating a
constraint. It is a plain text column rather than a database enum for the reason
`readiness_findings.code` is: the rule set is still settling, and an import-time guard plus a test
pinning these names to the oracle's is the check that actually matters.

**`presence` is a named column rather than a convention inside `detail`.** `legacy_only` means we
calculated nothing for a month they paid for; `calculated_only` is the reverse. They have opposite
meanings for whoever has to act, and a jsonb convention can be neither check-constrained nor
filtered.
"""

from __future__ import annotations

from typing import Final, Literal

Presence = Literal["both", "legacy_only", "calculated_only"]

BOTH: Final = "both"
LEGACY_ONLY: Final = "legacy_only"
CALCULATED_ONLY: Final = "calculated_only"

PRESENCE_VALUES: Final = (BOTH, LEGACY_ONLY, CALCULATED_ONLY)

UNATTRIBUTED: Final = "unattributed"
CASE_EACH_CONVERSION: Final = "case_each_conversion"
MISSING_CATCH_WEIGHT: Final = "missing_catch_weight"
REDISTRIBUTOR_MISATTRIBUTION: Final = "redistributor_misattribution"
DUPLICATE_LINE: Final = "duplicate_line"
DATE_CUTOFF: Final = "date_cutoff"

#: `unattributed` plus the five seeded causes. A test asserts the five equal
#: `expected_variance_attribution.json`'s cause names by set equality - production never imports
#: `src.generator`, so the oracle comparison lives in a test rather than here.
ROOT_CAUSES: Final = (
    UNATTRIBUTED,
    CASE_EACH_CONVERSION,
    MISSING_CATCH_WEIGHT,
    REDISTRIBUTOR_MISATTRIBUTION,
    DUPLICATE_LINE,
    DATE_CUTOFF,
)

PRESENCE_SENTENCES: Final[dict[str, str]] = {
    BOTH: "Both sides reported this program-month.",
    LEGACY_ONLY: (
        "The legacy system paid for this program-month and our calculation produced nothing for "
        "it. Check that the ERP file covers this period."
    ),
    CALCULATED_ONLY: (
        "We calculated a rebate for this program-month and the legacy export does not list it. "
        "Check that the export covers this period."
    ),
}

#: DESIGN.md §9: what happened, in the user's words. P13 renders these beside each variance.
ROOT_CAUSE_TITLES: Final[dict[str, str]] = {
    UNATTRIBUTED: "Not yet explained",
    CASE_EACH_CONVERSION: "Eaches counted as cases",
    MISSING_CATCH_WEIGHT: "Catch weight missing from the file",
    REDISTRIBUTOR_MISATTRIBUTION: "Billed under the redistributor's rate",
    DUPLICATE_LINE: "A duplicated line paid twice",
    DATE_CUTOFF: "Period cutoff moved lines into the next month",
}

#: What a *link* can say, which is not what a *variance* can say. `variances.root_cause` uses
#: `unattributed`, meaning "nothing has run yet"; a link uses `unexplained`, meaning "the rules ran
#: and these dollars were left over". Two different facts about two different rows, and mvp.md uses
#: each word for both - which is how an unattributed variance and an unexplainable dollar end up
#: counted as the same thing.
UNEXPLAINED: Final = "unexplained"

LINK_CAUSES: Final = (*(cause for cause in ROOT_CAUSES if cause != UNATTRIBUTED), UNEXPLAINED)

#: The five shared titles come from `ROOT_CAUSE_TITLES` rather than being retyped, so a cause is
#: worded once however it is being displayed.
LINK_CAUSE_TITLES: Final[dict[str, str]] = {
    **{cause: ROOT_CAUSE_TITLES[cause] for cause in LINK_CAUSES if cause != UNEXPLAINED},
    UNEXPLAINED: "No rule claims these dollars",
}

if (
    set(PRESENCE_VALUES) != set(PRESENCE_SENTENCES)
    or set(ROOT_CAUSES) != set(ROOT_CAUSE_TITLES)
    or set(LINK_CAUSES) != set(LINK_CAUSE_TITLES)
):
    # Import-time, because a cause with no words is a row the UI cannot render, and finding that
    # out when someone opens the variance table is too late.
    raise RuntimeError(
        "variance presence values, root causes, link causes and their sentences disagree"
    )
