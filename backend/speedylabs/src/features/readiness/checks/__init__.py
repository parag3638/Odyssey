"""The eight checks, and the one pass that runs them.

`run_checks` is a pure function: rows in, results out. No connection, no file, no clock - so
every acceptance criterion is a unit test over hand-written rows, and a failure is a failure of
the rule rather than of the plumbing around it.

**One pass, eight accumulators.** Each row is offered to every check in turn, which is what
keeps 48,150 rows inside a 20 second budget: eight passes would be eight streaming reads of the
same table. The job asserts this by counting statements on its connection.

**A check that raises does not take the other seven with it.** It is dropped from the live list
and recorded `errored` with its message, because one broken rule hiding six good reports is a
worse failure than the broken rule itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from src.features.readiness.checks.catch_weight import CatchWeightMissing
from src.features.readiness.checks.date_gaps import DateGap
from src.features.readiness.checks.duplicates import DuplicateLine
from src.features.readiness.checks.extension_identity import ExtensionMismatch
from src.features.readiness.checks.footer_total import FooterTotalMismatch
from src.features.readiness.checks.mixed_uom import MixedUomWithoutPackSize
from src.features.readiness.checks.redistributor_vendor import RedistributorVendorMismatch
from src.features.readiness.checks.unknown_manufacturer import UnknownManufacturer
from src.features.readiness.codes import CHECK_CODES
from src.features.readiness.context import ReadinessContext
from src.features.readiness.rows import CheckResult, ReadinessRow
from src.lib import logger

log = logger.get_logger(__name__)


class Check(Protocol):
    """What every check implements. Structural, so a check inherits nothing and imports
    nothing from here - each one is a plain class with these three members."""

    code: str

    def examine(self, row: ReadinessRow) -> None: ...

    def finish(self) -> CheckResult: ...


def build_checks(context: ReadinessContext) -> list[Check]:
    """One instance of each check, in the order the checklist presents them."""
    checks: list[Check] = [
        MixedUomWithoutPackSize(),
        CatchWeightMissing(),
        ExtensionMismatch(context.extension_tolerance_usd),
        DuplicateLine(),
        FooterTotalMismatch(context.footer, context.footer_tolerance_usd),
        DateGap(context.holidays),
        RedistributorVendorMismatch(context.manufacturers),
        UnknownManufacturer(context.manufacturers),
    ]
    built = tuple(check.code for check in checks)
    if built != CHECK_CODES:
        # Import-time-ish guard, for the same reason `codes.py` has one: a check present in
        # the vocabulary but absent from this list would silently never run, and a readiness
        # report missing a check is indistinguishable from a file that passed it.
        raise RuntimeError(f"built checks {built} do not match the vocabulary {CHECK_CODES}")
    return checks


def run_checks(rows: Iterable[ReadinessRow], context: ReadinessContext) -> list[CheckResult]:
    """Every check's result, in vocabulary order, after one pass over the rows."""
    live = build_checks(context)
    failed: dict[str, str] = {}

    for row in rows:
        for check in list(live):
            try:
                check.examine(row)
            except Exception as error:
                # Broad on purpose, and the one place in this codebase where that is right:
                # the alternative is that an unanticipated bug in one rule costs the customer
                # the other seven findings. The check is recorded as errored, with its type
                # and message, and dropped.
                failed[check.code] = f"{type(error).__name__}: {error}"
                live.remove(check)
                log.error("readiness_check_errored", check=check.code, error=str(error))

    results: dict[str, CheckResult] = {}
    for check in live:
        try:
            results[check.code] = check.finish()
        except Exception as error:
            failed[check.code] = f"{type(error).__name__}: {error}"
            log.error(
                "readiness_check_errored",
                check=check.code,
                error=str(error),
                phase="finish",
            )

    for code, message in failed.items():
        results[code] = CheckResult(code=code, outcome="errored", errored_message=message)

    # Vocabulary order, not completion order, so a checklist reads the same way every time.
    return [results[code] for code in CHECK_CODES if code in results]
