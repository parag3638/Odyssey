"""Readiness rules: when the checks may run, and how their results are ordered.

No HTTP, no SQL (BACKEND.md §2). The ordering lives here rather than in the query because
`order by severity` only happens to put `blocking` before `warning` - it is alphabetical luck,
and a third severity would silently break it. An explicit index cannot.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.canonical.model import CANONICAL_SCHEMA_VERSION
from src.features.readiness import checklist, repo
from src.features.readiness.codes import (
    AFFECTED_UNIT,
    CHECK_CODES,
    SEVERITY,
    TITLES,
    WHAT_HAPPENED,
    WHO_FIXES,
)
from src.jobs.tasks import defer_readiness
from src.lib import filenames, logger
from src.lib.errors import NotFound, NotMaterialized, ReadinessNotRun
from src.lib.timeutil import today_utc

log = logger.get_logger(__name__)

#: Blocking before warning, and within a severity the vocabulary's own order - so a checklist
#: reads the same way every time and a reader learns where to look.
_SEVERITY_RANK: Final = {"blocking": 0, "warning": 1}
_CODE_RANK: Final = {code: index for index, code in enumerate(CHECK_CODES)}


async def request_readiness(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    """Queue a readiness pass over this run's canonical rows."""
    run = await repo.run_row(session, run_id)
    if run is None:
        raise NotFound("That run does not exist.")
    if not await repo.has_canonical_rows(session, run_id):
        raise NotMaterialized(
            "This run has no canonical rows yet. Materialise it, then run the checks."
        )

    await defer_readiness(run_id)
    return {"ingest_run_id": run_id, "status": str(run["status"])}


async def get_readiness(session: AsyncSession, run_id: UUID) -> dict[str, Any]:
    """The whole readiness answer: counts, findings, and every check that ran."""
    run = await repo.run_row(session, run_id)
    if run is None:
        raise NotFound("That run does not exist.")

    checks = await repo.check_runs(session, run_id)
    if not checks:
        if not await repo.has_canonical_rows(session, run_id):
            raise NotMaterialized(
                "This run has no canonical rows yet. Materialise it, then run the checks."
            )
        raise ReadinessNotRun(
            "The readiness checks have not finished for this run yet. They run automatically "
            "once the canonical rows are written."
        )

    found = sorted(await repo.findings(session, run_id), key=_finding_order)
    blocking = sum(1 for row in found if str(row["severity"]) == "blocking")
    warnings = sum(1 for row in found if str(row["severity"]) == "warning")

    return {
        "ingest_run_id": run_id,
        # `blocked` the moment anything blocking was found, whatever the run's row says: the
        # two agree, and deriving it here means the API cannot report `ready` beside four
        # blocking findings if a status write was ever lost.
        "status": "blocked" if blocking else "ready",
        "run_status": str(run["status"]),
        "readiness_at": run["readiness_at"],
        "customer_name": str(run["customer_name"]),
        "blocking_count": blocking,
        "warning_count": warnings,
        "rows_examined": max((int(row["rows_examined"]) for row in checks), default=0),
        "findings": [_finding_out(row) for row in found],
        "checks_run": [_check_out(row) for row in sorted(checks, key=_check_order)],
    }


async def checklist_markdown(session: AsyncSession, run_id: UUID) -> tuple[str, str]:
    """The markdown checklist and the filename it downloads as.

    Built from the same `get_readiness` payload the screen renders, so the document and the
    page cannot disagree about a count, a sentence or an outcome.
    """
    data = await get_readiness(session, run_id)
    rejected = await repo.rejected_row_count(session, run_id)
    document = checklist.render(
        customer_name=str(data["customer_name"]),
        run_id=run_id,
        generated_on=today_utc(),
        canonical_schema_version=CANONICAL_SCHEMA_VERSION,
        rows_examined=int(data["rows_examined"]),
        rows_rejected=rejected,
        status=str(data["status"]),
        findings=data["findings"],
        checks_run=data["checks_run"],
    )
    name = filenames.download_name(
        "data-readiness-checklist-", str(data["customer_name"]), run_id, "md"
    )
    return document, name


def _finding_order(row: dict[str, Any]) -> tuple[int, int]:
    return (
        _SEVERITY_RANK.get(str(row["severity"]), len(_SEVERITY_RANK)),
        _CODE_RANK.get(str(row["check_code"]), len(_CODE_RANK)),
    )


def _check_order(row: dict[str, Any]) -> int:
    return _CODE_RANK.get(str(row["check_code"]), len(_CODE_RANK))


def _finding_out(row: dict[str, Any]) -> dict[str, Any]:
    code = str(row["check_code"])
    amount = row["affected_amount"]
    return {
        "check_code": code,
        "title": TITLES[code],
        "severity": str(row["severity"]),
        "affected_rows": int(row["affected_rows"]),
        "affected_unit": AFFECTED_UNIT[code],
        # A decimal string, never a JSON number (§3), and null where a figure would be
        # invented rather than measured.
        "affected_amount": None if amount is None else f"{amount:f}",
        "what_happened": WHAT_HAPPENED[code],
        "who_fixes": WHO_FIXES[code],
        "detail": dict(row["detail"]),
        "example_row_numbers": list(row["example_row_numbers"] or []),
    }


def _check_out(row: dict[str, Any]) -> dict[str, Any]:
    code = str(row["check_code"])
    return {
        "check_code": code,
        "title": TITLES.get(code, code),
        "severity": SEVERITY.get(code, "warning"),
        "outcome": str(row["outcome"]),
        "not_applicable_reason": row["not_applicable_reason"],
        "errored_message": row["errored_message"],
        "rows_examined": int(row["rows_examined"]),
        "context": dict(row["context"] or {}),
    }
