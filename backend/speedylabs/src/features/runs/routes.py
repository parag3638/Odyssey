"""Run list and detail. Parse, delegate, shape (BACKEND.md §2)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.features.runs import service
from src.features.runs.schemas import (
    RunDetail,
    RunDetailEnvelope,
    RunListEnvelope,
    RunSummary,
    SpecApplicationOut,
    StageTimestamps,
    is_settled,
)
from src.lib import db

router = APIRouter(prefix="/v1", tags=["runs"])


def _summary(row: dict[str, Any]) -> RunSummary:
    return RunSummary(
        id=row["id"],
        source_file_id=row["source_file_id"],
        customer_id=row["customer_id"],
        customer_name=row["customer_name"],
        status=row["status"],
        kind=row["kind"],
        original_filename=row["original_filename"],
        file_format=row["format"],
        byte_size=row["byte_size"],
        row_count=row["row_count"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        is_settled=is_settled(row["status"]),
    )


@router.get("/ingest-runs")
async def list_ingest_runs(
    session: Annotated[AsyncSession, Depends(db.sessions)],
    customer_id: Annotated[UUID | None, Query(alias="customerId")] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> RunListEnvelope:
    """Newest first, cursor-paginated. `nextCursor` is null on the last page."""
    rows, next_cursor = await service.list_runs(
        session, customer_id=customer_id, limit=limit, cursor=cursor
    )
    return RunListEnvelope(data=[_summary(row) for row in rows], next_cursor=next_cursor)


@router.get("/ingest-runs/{run_id}")
async def get_ingest_run(
    run_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> RunDetailEnvelope:
    """One run with its stage timeline. The URL the upload's 202 hands back."""
    row = await service.get_run(session, run_id)
    summary = _summary(row)
    spec = SpecApplicationOut(
        # Applied means "found and it fitted": a spec that was found and skipped has an id too,
        # which is why the boolean cannot simply be "is there an id".
        applied=row["considered_spec_id"] is not None and row["spec_skip_code"] is None,
        spec_id=row["considered_spec_id"],
        skip_code=row["spec_skip_code"],
        skip_detail=row["spec_skip_detail"],
    )
    return RunDetailEnvelope(
        data=RunDetail(
            **summary.model_dump(),
            stages=StageTimestamps(
                parsed_at=row["parsed_at"],
                profiled_at=row["profiled_at"],
                mapped_at=row["mapped_at"],
                materialized_at=row["materialized_at"],
                readiness_at=row["readiness_at"],
                calculated_at=row["calculated_at"],
                first_variance_at=row["first_variance_at"],
            ),
            elapsed_ms_to_first_variance=row["elapsed_ms_to_first_variance"],
            spec=spec,
        )
    )
