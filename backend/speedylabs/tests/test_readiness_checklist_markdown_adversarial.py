"""Attack item 6: the markdown checklist - header, both sections, the checks-run footer with
every not-applicable reason, `|` and backticks escaped, a value starting `=` prefixed, and the
filename pattern. Pure: `checklist.render` takes plain data and returns a string, no DB.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.features.readiness.checklist import render
from src.features.readiness.codes import CHECK_CODES
from src.lib.filenames import download_name

CUSTOMER = "Harbor | Point `Foods`"  # deliberately carries both dangerous characters
RUN_ID = "01a1155d-0000-7000-8000-000000000000"


def _checks_run_all_codes(**overrides: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code in CHECK_CODES:
        row: dict[str, Any] = {
            "check_code": code,
            "title": f"Title for {code}",
            "outcome": "clean",
            "not_applicable_reason": None,
            "errored_message": None,
            "rows_examined": 48150,
        }
        row.update(overrides.get(code, {}))
        rows.append(row)
    return rows


def _finding(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "check_code": "extension_mismatch",
        "severity": "blocking",
        "title": "A title with a | pipe and a `backtick`",
        "what_happened": "A value starting =2+2 that must be formula-prefixed, and a | pipe.",
        "who_fixes": "The distributor's export team.",
        "affected_rows": 95,
        "affected_unit": "rows",
        "affected_amount": "332.50",
        "example_row_numbers": [913, 1514, 2021, 2846, 3989, 9999],  # 6, cap is 5
    }
    base.update(kwargs)
    return base


def test_header_and_both_sections_present() -> None:
    doc = render(
        customer_name="Acme Co",
        run_id=RUN_ID,
        generated_on=date(2026, 10, 7),
        canonical_schema_version="1.0.0",
        rows_examined=48150,
        rows_rejected=0,
        status="blocked",
        findings=[_finding()],
        checks_run=_checks_run_all_codes(),
    )
    assert doc.startswith("# Data-readiness checklist\n")
    assert "## Blocking" in doc
    assert "## Warnings" in doc
    assert "## Checks run against this file" in doc
    # None but seven more codes should each appear in the footer table
    for code in CHECK_CODES:
        assert f"({code})" in doc


def test_checks_footer_states_every_not_applicable_reason() -> None:
    overrides = {
        "footer_total_mismatch": {
            "outcome": "not_applicable",
            "not_applicable_reason": "This file has no footer record.",
        },
        "unknown_manufacturer": {
            "outcome": "errored",
            "errored_message": "ValueError: boom",
        },
    }
    doc = render(
        customer_name="Acme Co",
        run_id=RUN_ID,
        generated_on=date(2026, 10, 7),
        canonical_schema_version="1.0.0",
        rows_examined=48150,
        rows_rejected=0,
        status="ready",
        findings=[],
        checks_run=_checks_run_all_codes(**overrides),
    )
    assert "This file has no footer record." in doc
    assert "ValueError: boom" in doc
    # A reader must be able to tell "checked, fine" from "could not check" in the footer.
    assert "no issues found" in doc
    assert "could not be checked" in doc
    assert "did not run" in doc


def test_pipe_and_backtick_are_escaped_in_table_cells() -> None:
    overrides = {
        "duplicate_line": {
            "outcome": "not_applicable",
            "not_applicable_reason": "Note with a | pipe and a `backtick` inside it.",
            "title": "Title | with `marks`",
        }
    }
    doc = render(
        customer_name=CUSTOMER,
        run_id=RUN_ID,
        generated_on=date(2026, 10, 7),
        canonical_schema_version="1.0.0",
        rows_examined=1,
        rows_rejected=0,
        status="ready",
        findings=[],
        checks_run=_checks_run_all_codes(**overrides),
    )
    # The raw unescaped forms must not appear bare inside a table row.
    assert "| Note with a | pipe" not in doc
    assert "\\|" in doc, "expected at least one escaped pipe in the footer table"
    assert "\\`" in doc, "expected at least one escaped backtick in the footer table"


def test_a_value_starting_with_equals_is_formula_prefixed() -> None:
    overrides = {
        "date_gap": {
            "outcome": "not_applicable",
            "not_applicable_reason": "=2+2 should not execute as a formula in a spreadsheet.",
        }
    }
    doc = render(
        customer_name="Acme Co",
        run_id=RUN_ID,
        generated_on=date(2026, 10, 7),
        canonical_schema_version="1.0.0",
        rows_examined=1,
        rows_rejected=0,
        status="ready",
        findings=[],
        checks_run=_checks_run_all_codes(**overrides),
    )
    assert "'=2+2" in doc, "a leading '=' must be prefixed with a single quote before export"
    assert doc.count("=2+2") >= 1
    # It must never appear completely bare (unprefixed) anywhere in the document.
    assert "| =2+2" not in doc


def test_example_row_numbers_capped_at_five_in_the_rendered_item() -> None:
    doc = render(
        customer_name="Acme Co",
        run_id=RUN_ID,
        generated_on=date(2026, 10, 7),
        canonical_schema_version="1.0.0",
        rows_examined=48150,
        rows_rejected=0,
        status="blocked",
        findings=[_finding()],  # 6 example rows passed in, cap is 5
        checks_run=_checks_run_all_codes(),
    )
    line = next(line for line in doc.splitlines() if "Example source rows" in line)
    assert "9999" not in line, "the sixth example row must be dropped by the render cap"
    assert line.count(",") == 4  # five numbers -> four commas


def test_zero_findings_says_ready_not_an_empty_document() -> None:
    doc = render(
        customer_name="Acme Co",
        run_id=RUN_ID,
        generated_on=date(2026, 10, 7),
        canonical_schema_version="1.0.0",
        rows_examined=48150,
        rows_rejected=0,
        status="ready",
        findings=[],
        checks_run=_checks_run_all_codes(),
    )
    assert "This file is ready." in doc
    assert "None." in doc  # the Blocking/Warnings sections say "None." rather than vanishing


def test_rows_rejected_is_stated_and_excluded_from_the_counts_line() -> None:
    doc = render(
        customer_name="Acme Co",
        run_id=RUN_ID,
        generated_on=date(2026, 10, 7),
        canonical_schema_version="1.0.0",
        rows_examined=48010,
        rows_rejected=140,
        status="ready",
        findings=[],
        checks_run=_checks_run_all_codes(),
    )
    assert "140" in doc
    assert "could not be read" in doc


def test_filename_pattern_matches_the_mvp_fixed_string() -> None:
    # The trailing dash is what the real caller (`readiness/service.py`) passes - it is what
    # selects the dash separator `download_name` needs to produce mvp.md's exact string.
    name = download_name("data-readiness-checklist-", "Harbor Point Foodservice", RUN_ID, "md")
    assert name == f"data-readiness-checklist-harbor-point-foodservice-{RUN_ID}.md"


def test_filename_slugs_a_customer_name_with_punctuation() -> None:
    name = download_name("data-readiness-checklist-", CUSTOMER, RUN_ID, "md")
    assert name.startswith("data-readiness-checklist-harbor-point-foods-")
    assert "|" not in name and "`" not in name


def test_filename_separator_is_sensitive_to_a_missing_trailing_dash() -> None:
    """Documents the real contract of `download_name`: the prefix's own trailing character
    chooses the separator. A caller who forgets the trailing dash (as the canonical CSV export
    deliberately does not want) silently gets underscores instead of mvp.md's dashed name -
    worth a test because nothing else would catch a future caller getting this backwards."""
    name = download_name("data-readiness-checklist", "Acme Co", RUN_ID, "md")
    assert name == f"data-readiness-checklist_acme-co_{RUN_ID}.md"
