"""The readiness routes. Parse, delegate, shape (BACKEND.md §2)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.features.readiness import service
from src.features.readiness.schemas import (
    CheckRunOut,
    FindingOut,
    ReadinessAcceptedEnvelope,
    ReadinessAcceptedOut,
    ReadinessEnvelope,
    ReadinessOut,
)
from src.lib import db

router = APIRouter(prefix="/v1", tags=["readiness"])


@router.post("/ingest-runs/{run_id}/readiness", status_code=202)
async def run_readiness(
    run_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> ReadinessAcceptedEnvelope:
    """202: the checks are queued.

    Rarely needed by hand - readiness is chained automatically after a materialise - but it
    exists so a run whose checks were interrupted can be re-checked without re-materialising
    48,150 rows.
    """
    accepted = await service.request_readiness(session, run_id)
    return ReadinessAcceptedEnvelope(
        data=ReadinessAcceptedOut(
            ingest_run_id=run_id,
            status=str(accepted["status"]),
            status_url=f"/v1/ingest-runs/{run_id}",
        )
    )


@router.get("/ingest-runs/{run_id}/readiness")
async def get_readiness(
    run_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> ReadinessEnvelope:
    """Every finding, and every check that ran - including the ones that found nothing."""
    data = await service.get_readiness(session, run_id)
    return ReadinessEnvelope(
        data=ReadinessOut(
            ingest_run_id=run_id,
            status=str(data["status"]),
            blocking_count=int(data["blocking_count"]),
            warning_count=int(data["warning_count"]),
            rows_examined=int(data["rows_examined"]),
            readiness_at=data["readiness_at"],
            findings=[_finding(row) for row in data["findings"]],
            checks_run=[_check(row) for row in data["checks_run"]],
        )
    )


@router.get("/ingest-runs/{run_id}/readiness/checklist.md")
async def download_checklist(
    run_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> Response:
    """The checklist a customer's IT team receives, as markdown.

    Markdown rather than PDF: it prints to PDF from any browser, it diffs between two runs,
    and it pastes into a ticket - none of which needs a new dependency. The filename uses the
    mandated vocabulary verbatim, because the words have to reach the artefact and not only
    the screen.
    """
    document, filename = await service.checklist_markdown(session, run_id)
    return Response(
        content=document,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # A checklist is a snapshot of a run that can be re-checked; a cached copy would
            # show yesterday's findings in a tab nobody reloaded.
            "Cache-Control": "no-store",
        },
    )


def _finding(row: dict[str, Any]) -> FindingOut:
    return FindingOut(
        check_code=str(row["check_code"]),
        title=str(row["title"]),
        severity=str(row["severity"]),
        affected_rows=int(row["affected_rows"]),
        affected_unit=str(row["affected_unit"]),
        affected_amount=row["affected_amount"],
        what_happened=str(row["what_happened"]),
        who_fixes=str(row["who_fixes"]),
        detail=dict(row["detail"]),
        example_row_numbers=list(row["example_row_numbers"]),
    )


def _check(row: dict[str, Any]) -> CheckRunOut:
    return CheckRunOut(
        check_code=str(row["check_code"]),
        title=str(row["title"]),
        severity=str(row["severity"]),
        outcome=str(row["outcome"]),
        not_applicable_reason=row["not_applicable_reason"],
        errored_message=row["errored_message"],
        rows_examined=int(row["rows_examined"]),
        context=dict(row["context"]),
    )
