"""Attack item 4, readiness half: "one readiness check raising must leave the other seven
reporting and must not fail the run." `checks/__init__.py` claims exactly this in its own
docstring ("A check that raises does not take the other seven with it"). Pure unit test, no DB -
`run_checks` is a pure function of (rows, context), so this is the right level to prove it, and
it is the direction opposite the drift bug in `test_drift_fails_closed_adversarial.py`: here a
broken rule must not cost the other seven; there, a broken comparison must not let a bad file
through. Both claims matter and neither had a test before this one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from src.features.readiness.checks import run_checks
from src.features.readiness.codes import CHECK_CODES
from src.features.readiness.context import ReadinessContext
from src.features.readiness.rows import ReadinessRow


def _row(**overrides: object) -> ReadinessRow:
    base: dict[str, object] = {
        "source_row_number": 1,
        "invoice_no": "INV1",
        "invoice_date": date(2026, 1, 5),
        "dist_item_id": "ITEM1",
        "brand_manufacturer": "Northvale Foods",
        "invoice_vendor": "300145",
        "qty": Decimal("10"),
        "uom": "CS",
        "pack_size": 12,
        "catch_weight_lbs": None,
        "unit_price": Decimal("5.00"),
        "ext_amount": Decimal("50.00"),
    }
    base.update(overrides)
    return ReadinessRow(**base)  # type: ignore[arg-type]


def test_one_check_raising_in_examine_leaves_the_other_seven_reporting() -> None:
    rows = [_row(source_row_number=i) for i in range(1, 6)]
    context = ReadinessContext()

    with patch(
        "src.features.readiness.checks.mixed_uom.MixedUomWithoutPackSize.examine",
        side_effect=RuntimeError("boom"),
    ):
        results = run_checks(rows, context)

    by_code = {r.code for r in results}
    assert by_code == set(CHECK_CODES), "every one of the eight codes must still produce a result"

    broken = next(r for r in results if r.code == "mixed_uom_without_pack_size")
    assert broken.outcome == "errored"
    assert broken.errored_message is not None
    assert "boom" in broken.errored_message

    others = [r for r in results if r.code != "mixed_uom_without_pack_size"]
    assert all(r.outcome in ("clean", "findings", "not_applicable") for r in others), (
        "the other seven checks must have completed normally, not been dropped or errored "
        "alongside the broken one"
    )


def test_one_check_raising_in_finish_also_leaves_the_other_seven_reporting() -> None:
    rows = [_row(source_row_number=i) for i in range(1, 6)]
    context = ReadinessContext()

    with patch(
        "src.features.readiness.checks.duplicates.DuplicateLine.finish",
        side_effect=RuntimeError("boom-in-finish"),
    ):
        results = run_checks(rows, context)

    by_code = {r.code for r in results}
    assert by_code == set(CHECK_CODES)
    broken = next(r for r in results if r.code == "duplicate_line")
    assert broken.outcome == "errored"
    assert "boom-in-finish" in (broken.errored_message or "")
    others = [r for r in results if r.code != "duplicate_line"]
    assert all(r.outcome in ("clean", "findings", "not_applicable") for r in others)


def test_zero_rows_every_check_reports_clean_or_not_applicable_no_raise() -> None:
    """Sanity companion: the plan's own 8.1 criterion, checked here because it is the first
    thing an empty-file adversarial input would hit."""
    results = run_checks([], ReadinessContext())
    assert {r.code for r in results} == set(CHECK_CODES)
    assert all(r.outcome in ("clean", "not_applicable") for r in results)
