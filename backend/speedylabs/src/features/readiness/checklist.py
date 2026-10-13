"""The data-readiness checklist, as markdown.

This is the artefact a customer's IT team actually receives, so the vocabulary mvp.md fixes -
"data-readiness checklist" - is used verbatim, and every item says what happened and who fixes
it, in DESIGN.md §9's voice. The check code appears only in parentheses: a reader fixing an
export does not care that we call it `mixed_uom_without_pack_size`, but a reader comparing two
checklists does.

The sentences come from `codes.py`, the same table the API serves, because a checklist and a
screen that described one finding differently would both be distrusted.

**The footer is the point of the document.** "Checks run against this file" lists all eight
codes with their outcomes, and every `not_applicable` carries its reason - so a reader can tell
"we checked and it was fine" from "we could not check this". That distinction is what makes
this a generated report rather than a template, and a checklist with no findings is still worth
sending because of it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from src.features.readiness.codes import CHECK_CODES
from src.lib import md_safe

#: Example rows listed per item. The API caps the stored array at five; this is the same cap
#: stated where a reader sees it.
MAX_EXAMPLES = 5

_OUTCOME_WORDS = {
    "findings": "issues found",
    "clean": "no issues found",
    "not_applicable": "could not be checked",
    "errored": "did not run",
}


def render(
    *,
    customer_name: str,
    run_id: object,
    generated_on: date,
    canonical_schema_version: str,
    rows_examined: int,
    rows_rejected: int,
    status: str,
    findings: list[dict[str, Any]],
    checks_run: list[dict[str, Any]],
) -> str:
    """The whole document. Pure: values in, markdown out, no clock and no database."""
    blocking = [f for f in findings if str(f["severity"]) == "blocking"]
    warnings = [f for f in findings if str(f["severity"]) == "warning"]

    lines: list[str] = [
        "# Data-readiness checklist",
        "",
        f"**Customer:** {md_safe.text(customer_name)}  ",
        f"**Run:** `{run_id}`  ",
        f"**Generated:** {generated_on.isoformat()}  ",
        f"**Canonical schema:** {canonical_schema_version}  ",
        f"**Lines checked:** {rows_examined:,}  ",
    ]
    if rows_rejected:
        lines.append(
            f"**Lines that could not be read:** {rows_rejected:,} — these are listed with the "
            "run's output and are not counted below."
        )
    lines += ["", _headline(status, len(blocking), len(warnings)), ""]

    lines += _section(
        "Blocking",
        "These stop a rebate calculation. Each one has to be fixed or explicitly waived.",
        blocking,
    )
    lines += _section(
        "Warnings",
        "These do not stop a calculation, but each one is money nobody can attribute yet.",
        warnings,
    )
    lines += _checks_footer(checks_run)
    return "\n".join(lines) + "\n"


def _grouped(amount: str) -> str:
    """A decimal string with thousands separators, still exact.

    The backend normally sends raw values and lets the frontend format them (DESIGN.md §7
    rule 6), but this document **is** the artefact a person reads - there is no frontend
    downstream of it - so the grouping has to happen here. `Decimal`, never `float`: a
    rebate figure printed through a float is wrong by cents and nobody notices until a
    customer reconciles it.
    """
    try:
        value = Decimal(amount)
    except InvalidOperation:
        return amount
    return f"{value:,}"


def _headline(status: str, blocking: int, warnings: int) -> str:
    if blocking:
        return (
            f"**{blocking} blocking issue{'s' if blocking != 1 else ''}** and "
            f"{warnings} warning{'s' if warnings != 1 else ''} were found in this file."
        )
    if warnings:
        return (
            f"No blocking issues. **{warnings} warning{'s' if warnings != 1 else ''}** are "
            "worth a look before the first calculation."
        )
    return "**This file is ready.** Every check below ran against it and found nothing to fix."


def _section(title: str, intent: str, findings: list[dict[str, Any]]) -> list[str]:
    lines = [f"## {title}", ""]
    if not findings:
        lines += [f"None. ({intent})", ""]
        return lines
    lines += [intent, ""]
    for finding in findings:
        lines += _item(finding)
    return lines


def _item(finding: dict[str, Any]) -> list[str]:
    code = str(finding["check_code"])
    count = int(finding["affected_rows"])
    unit = str(finding["affected_unit"])
    amount = finding["affected_amount"]
    examples = list(finding["example_row_numbers"])[:MAX_EXAMPLES]

    # An unticked checkbox, because the reader's next action is to tick it.
    head = f"- [ ] **{md_safe.text(str(finding['title']))}** ({code})"
    detail = [f"      - {count:,} {unit} affected"]
    if amount is not None:
        detail.append(f"      - ${_grouped(str(amount))} of invoiced value involved")
    detail.append(f"      - What happened: {md_safe.text(str(finding['what_happened']))}")
    detail.append(f"      - Who fixes it: {md_safe.text(str(finding['who_fixes']))}")
    if examples:
        shown = ", ".join(str(number) for number in examples)
        detail.append(f"      - Example source rows: {shown}")
    return [head, *detail, ""]


def _checks_footer(checks_run: list[dict[str, Any]]) -> list[str]:
    by_code = {str(row["check_code"]): row for row in checks_run}
    lines = [
        "## Checks run against this file",
        "",
        (
            "Every check is listed, including the ones that found nothing and the ones that "
            "could not run — a check that could not run is not a pass."
        ),
        "",
        "| Check | Outcome | Lines checked | Note |",
        "| --- | --- | --: | --- |",
    ]
    for code in CHECK_CODES:
        row = by_code.get(code)
        if row is None:
            # A code in the vocabulary with no recorded outcome. Said out loud rather than
            # omitted: a missing line is how a reader would conclude we checked it.
            lines.append(f"| {code} | not recorded | | this check did not report an outcome |")
            continue
        outcome = str(row["outcome"])
        note = str(row["not_applicable_reason"] or row["errored_message"] or "")
        lines.append(
            f"| {md_safe.cell(str(row['title']))} ({code}) "
            f"| {_OUTCOME_WORDS.get(outcome, outcome)} "
            f"| {int(row['rows_examined']):,} "
            f"| {md_safe.cell(note)} |"
        )
    lines.append("")
    return lines
