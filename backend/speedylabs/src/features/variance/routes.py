"""The variance routes. Parse, delegate, shape (BACKEND.md §2)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.features.variance import service
from src.features.variance.schemas import (
    ComputeAcceptedEnvelope,
    ComputeAcceptedOut,
    ComputeIn,
    LegacyFileOut,
    LegacyFilesEnvelope,
    LegacyLineOut,
    LegacyLinesEnvelope,
    MoneyOut,
    UnmatchedLineOut,
    VarianceOut,
    VariancesEnvelope,
    VarianceSummaryEnvelope,
    VarianceSummaryOut,
)
from src.lib import db, idempotency
from src.lib.money import DEFAULT_CURRENCY, Money

router = APIRouter(prefix="/v1", tags=["variance"])

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def _limit(requested: int | None) -> int:
    if requested is None:
        return DEFAULT_LIMIT
    return max(1, min(requested, MAX_LIMIT))


def _money(value: Any) -> MoneyOut:
    return MoneyOut(**Money(amount=value, currency=DEFAULT_CURRENCY).to_api())


@router.get("/customers/{customer_id}/legacy-files")
async def list_legacy_files(
    customer_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> LegacyFilesEnvelope:
    """The legacy exports this customer has uploaded, newest first.

    Every upload is its own file and its own import, so a second upload of the same export does not
    replace the first - the user picks which one to compare against, and the list says how many of
    each one's rows matched a program.
    """
    rows = await service.legacy_files(session, customer_id)
    return LegacyFilesEnvelope(
        data=[
            LegacyFileOut(
                source_file_id=row["source_file_id"],
                original_filename=str(row["original_filename"]),
                uploaded_at=row["uploaded_at"],
                status=str(row["status"]),
                error_code=row["error_code"],
                line_count=int(row["line_count"]),
                matched_count=int(row["matched_count"]),
            )
            for row in rows
        ]
    )


@router.post("/ingest-runs/{run_id}/variances", status_code=202)
async def compute_variances(
    run_id: UUID,
    body: ComputeIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> ComputeAcceptedEnvelope:
    """Compare this run's calculated rebates against an imported legacy export."""
    data = await service.start_computation(
        session,
        run_id,
        legacy_source_file_id=body.legacy_source_file_id,
        idempotency_key=idempotency.clean_key(request.headers.get("Idempotency-Key")),
    )
    return ComputeAcceptedEnvelope(
        data=ComputeAcceptedOut(
            ingest_run_id=data["ingest_run_id"],
            status=str(data["status"]),
            status_url=str(data["status_url"]),
        )
    )


@router.get("/ingest-runs/{run_id}/variance-summary")
async def get_variance_summary(
    run_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> VarianceSummaryEnvelope:
    """The totals, the staleness flag, and the legacy rows nobody could match."""
    data = await service.summary(session, run_id)
    return VarianceSummaryEnvelope(
        data=VarianceSummaryOut(
            variance_count=int(data["variance_count"]),
            legacy_total=_money(data["legacy_total"]),
            calculated_total=_money(data["calculated_total"]),
            net_variance=_money(data["net_variance"]),
            absolute_variance=_money(data["absolute_variance"]),
            computed_at=data["computed_at"],
            calculated_at=data["calculated_at"],
            legacy_source_file_id=data["legacy_source_file_id"],
            variances_stale=bool(data["variances_stale"]),
            unmatched=[
                UnmatchedLineOut(
                    source_row_number=int(row["source_row_number"]),
                    program_external_ref=str(row["program_external_ref"]),
                    program_name_text=row["program_name_text"],
                    period_start=row["period_start"],
                    amount=_money(row["amount"]),
                )
                for row in data["unmatched"]
            ],
            unmatched_count=int(data["unmatched_count"]),
        )
    )


@router.get("/ingest-runs/{run_id}/variances")
async def list_variances(
    run_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
    cause: Annotated[str | None, Query()] = None,
    presence: Annotated[str | None, Query()] = None,
    link_cause: Annotated[str | None, Query(alias="linkCause")] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> VariancesEnvelope:
    """One row per program per month: what they said, what we computed, and the difference.

    `?presence=legacy_only` finds the months the legacy system paid for and we calculated nothing
    against - a different question from `?cause=`, and the one asked when a total looks short.
    """
    data = await service.page(
        session,
        run_id,
        limit=_limit(limit),
        cause=cause,
        presence=presence,
        link_cause=link_cause,
        raw_cursor=cursor,
    )
    return VariancesEnvelope(
        data=[
            VarianceOut(
                variance_id=row["variance_id"],
                program_id=row["program_id"],
                external_ref=row["external_ref"],
                program_name=str(row["program_name"]),
                kind=str(row["kind"]),
                period_start=row["period_start"],
                period_end=row["period_end"],
                legacy_amount=_money(row["legacy_amount"]),
                calculated_amount=_money(row["calculated_amount"]),
                variance_amount=_money(row["variance_amount"]),
                presence=row["presence"],
                root_cause=str(row["root_cause"]),
                explained=bool(row["explained"]),
            )
            for row in data["rows"]
        ],
        next_cursor=data["next_cursor"],
    )


@router.get("/source-files/{file_id}/legacy-lines")
async def list_legacy_lines(
    file_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
    match: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> LegacyLinesEnvelope:
    """Every row this import read out of one legacy export, matched and unmatched alike.

    The variance summary carries the unmatched rows, capped, for the file a comparison used. This
    answers the different question - what did you actually read out of my file - which is the one
    asked when a number looks wrong.
    """
    data = await service.legacy_lines(
        session, file_id, limit=_limit(limit), match=match, raw_cursor=cursor
    )
    return LegacyLinesEnvelope(
        data=[
            LegacyLineOut(
                source_row_number=int(row["source_row_number"]),
                program_external_ref=str(row["program_external_ref"]),
                program_name_text=row["program_name_text"],
                program_id=row["program_id"],
                program_name=row["program_name"],
                period_start=row["period_start"],
                period_end=row["period_end"],
                amount=_money(row["amount"]),
                match_status=row["match_status"],
            )
            for row in data["rows"]
        ],
        next_cursor=data["next_cursor"],
    )
