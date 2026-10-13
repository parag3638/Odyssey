"""The check vocabulary and its severities. A one-way door.

These eight strings reach `fixtures/oracle/expected_readiness.json`, the API, the markdown
checklist a customer's IT team receives, and the UI's filter. Renaming one invalidates a
committed oracle and any checklist already sent, so the set is fixed and additions are
additive (BACKEND.md §3).

**Severity comes from the oracle, not from prose.** `duplicate_line` is `blocking` here
because that is what `src/generator/oracle.py` publishes, and because 120 duplicated lines
are duplicated money: paying a rebate twice on a re-keyed line is the error class this tool
exists to prevent. mvp.md's Phase 8 text called it a warning; a test asserts this map equals
the oracle's, so the disagreement cannot be settled by eye or drift back.
"""

from __future__ import annotations

from typing import Final, Literal

Severity = Literal["blocking", "warning"]
Outcome = Literal["findings", "clean", "not_applicable", "errored"]

MIXED_UOM: Final = "mixed_uom_without_pack_size"
CATCH_WEIGHT_MISSING: Final = "catch_weight_missing"
EXTENSION_MISMATCH: Final = "extension_mismatch"
DUPLICATE_LINE: Final = "duplicate_line"
DATE_GAP: Final = "date_gap"
FOOTER_TOTAL_MISMATCH: Final = "footer_total_mismatch"
REDISTRIBUTOR_VENDOR_MISMATCH: Final = "redistributor_vendor_mismatch"
UNKNOWN_MANUFACTURER: Final = "unknown_manufacturer"

#: Every code, in the order the checklist presents them: the four that make a file
#: unusable first, then the four that make it questionable.
CHECK_CODES: Final = (
    MIXED_UOM,
    CATCH_WEIGHT_MISSING,
    EXTENSION_MISMATCH,
    DUPLICATE_LINE,
    FOOTER_TOTAL_MISMATCH,
    DATE_GAP,
    REDISTRIBUTOR_VENDOR_MISMATCH,
    UNKNOWN_MANUFACTURER,
)

SEVERITY: Final[dict[str, Severity]] = {
    MIXED_UOM: "blocking",
    CATCH_WEIGHT_MISSING: "blocking",
    EXTENSION_MISMATCH: "blocking",
    DUPLICATE_LINE: "blocking",
    FOOTER_TOTAL_MISMATCH: "blocking",
    DATE_GAP: "warning",
    REDISTRIBUTOR_VENDOR_MISMATCH: "warning",
    UNKNOWN_MANUFACTURER: "warning",
}

#: What a reader is told the check looked for. One sentence, DESIGN.md §9 voice: what
#: happened and what to do, never an internal code as the label.
TITLES: Final[dict[str, str]] = {
    MIXED_UOM: "Items sold in more than one unit, with no pack size to convert them",
    CATCH_WEIGHT_MISSING: "Pound-priced lines with no shipped weight",
    EXTENSION_MISMATCH: "Line totals that do not equal quantity times price",
    DUPLICATE_LINE: "Invoice lines that appear more than once",
    FOOTER_TOTAL_MISMATCH: "The file's own total does not match the sum of its lines",
    DATE_GAP: "Business days inside the file's range with no invoices at all",
    REDISTRIBUTOR_VENDOR_MISMATCH: "Lines billed direct for a brand bought through a redistributor",
    UNKNOWN_MANUFACTURER: "Brands that are not in this customer's manufacturer list",
}

#: What happened, in one sentence, for a reader who is not us. The API serves these and the
#: markdown export reads the same table, because a checklist and a screen that described the
#: same finding differently would both be distrusted. §3 already makes user-facing sentences
#: the backend's job - `error.message` is the same rule.
WHAT_HAPPENED: Final[dict[str, str]] = {
    MIXED_UOM: (
        "These items are sold in more than one unit and the lines carry no pack size, so cases "
        "and eaches cannot be reconciled."
    ),
    CATCH_WEIGHT_MISSING: (
        "These lines are priced per pound but carry no shipped weight, so their extension "
        "cannot be checked or rebated."
    ),
    EXTENSION_MISMATCH: (
        "On these lines the extended amount is not the unit price times the quantity billed, "
        "beyond a one cent tolerance."
    ),
    DUPLICATE_LINE: (
        "These lines appear more than once with the same invoice, item, unit, quantity and price."
    ),
    FOOTER_TOTAL_MISMATCH: (
        "The total in the file's own footer record is not the sum of the file's lines."
    ),
    DATE_GAP: (
        "These business days fall inside the file's own date range and carry no invoice lines "
        "at all."
    ),
    REDISTRIBUTOR_VENDOR_MISMATCH: (
        "These lines are billed under the manufacturer's own vendor number for brands this "
        "distributor only buys through a redistributor."
    ),
    UNKNOWN_MANUFACTURER: (
        "These lines name a brand that is not in this customer's manufacturer list, so no "
        "rebate program can be matched to them."
    ),
}

#: Who fixes it, and how. Named separately from `WHAT_HAPPENED` because a checklist item that
#: says what is wrong without saying whose job it is gets read and then put down.
WHO_FIXES: Final[dict[str, str]] = {
    MIXED_UOM: (
        "The distributor's IT team: add the eaches-per-case for these items to the export."
    ),
    CATCH_WEIGHT_MISSING: (
        "The distributor's IT team: include the shipped weight for catch-weight items."
    ),
    EXTENSION_MISMATCH: (
        "The distributor's billing team: re-export these lines from the invoice of record. A "
        "late adjustment usually explains them."
    ),
    DUPLICATE_LINE: (
        "The distributor's billing team: remove the repeated lines, or give genuinely separate "
        "shipments distinct line references."
    ),
    FOOTER_TOTAL_MISMATCH: (
        "The distributor's IT team: re-export so the trailer is computed after every "
        "adjustment, or tell us which total is authoritative."
    ),
    DATE_GAP: (
        "The distributor's IT team: confirm whether the export missed these days or the "
        "business was closed. A missed day understates every rebate."
    ),
    REDISTRIBUTOR_VENDOR_MISMATCH: (
        "The distributor's purchasing team: correct the remit-to vendor so the rebate is "
        "attributed to the program that applies."
    ),
    UNKNOWN_MANUFACTURER: (
        "Us, with the customer: add these brands to the manufacturer list, or correct the brand "
        "code in the export."
    ),
}

#: What the affected count counts, per check. Named because it is not "rows" everywhere:
#: a date gap counts business days and a footer mismatch counts one file-level fact, and a
#: checklist that said "5 rows" for five missing dates would be wrong.
AFFECTED_UNIT: Final[dict[str, str]] = {
    MIXED_UOM: "lines",
    CATCH_WEIGHT_MISSING: "lines",
    EXTENSION_MISMATCH: "lines",
    DUPLICATE_LINE: "lines",
    FOOTER_TOTAL_MISMATCH: "file",
    DATE_GAP: "business days",
    REDISTRIBUTOR_VENDOR_MISMATCH: "lines",
    UNKNOWN_MANUFACTURER: "lines",
}

if (
    set(CHECK_CODES) != set(SEVERITY)
    or set(CHECK_CODES) != set(TITLES)
    or set(CHECK_CODES) != set(WHAT_HAPPENED)
    or set(CHECK_CODES) != set(WHO_FIXES)
):
    # Import-time, because a code with no severity or no title is a check that cannot be
    # reported, and finding that out when a customer downloads the checklist is too late.
    raise RuntimeError("readiness check codes, severities and sentences disagree")
