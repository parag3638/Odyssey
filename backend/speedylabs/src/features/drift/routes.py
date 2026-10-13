"""The drift routes. Parse, delegate, shape (BACKEND.md §2)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.features.drift import service
from src.features.drift.schemas import (
    BaselineOut,
    ColumnComparisonOut,
    ColumnSideOut,
    ComparisonOut,
    DecisionEnvelope,
    DecisionIn,
    DecisionOut,
    DriftCheckOut,
    DriftEnvelope,
    DriftFindingOut,
    DriftOut,
)
from src.lib import db, idempotency
from src.lib.clientip import client_ip_hash

router = APIRouter(prefix="/v1", tags=["drift"])


@router.get("/ingest-runs/{run_id}/drift")
async def get_drift(
    run_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> DriftEnvelope:
    """What changed since the last approved file, and what a human decided about it.

    `200` with `comparable: false` for a customer's first file, rather than a `409`: having
    nothing to
    compare against is a state this screen renders in a sentence, and turning it into an error would
    make every client translate it back.
    """
    data = await service.get_drift(session, run_id)
    return DriftEnvelope(data=_drift(data))


@router.post("/ingest-runs/{run_id}/drift/accept", status_code=201)
async def accept_drift(
    run_id: UUID,
    body: DecisionIn,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> DecisionEnvelope:
    """Approve this file's mapping as a new spec version and release the run.

    `201` for a new version; `200` when the mapping canonicalises to the spec already approved,
    which
    is P9's rule reached through this door rather than a second opinion about it.
    """
    settings = config.load()
    result = await service.accept(
        session,
        run_id,
        note=body.note,
        force=body.force,
        client_ip_hash=client_ip_hash(request, settings.trusted_proxy_hops, settings.ip_hash_salt),
        idempotency_key=idempotency.clean_key(request.headers.get("Idempotency-Key")),
    )
    if not result["created"]:
        response.status_code = 200
    spec = result["spec"]
    return DecisionEnvelope(
        data=DecisionOut(
            ingest_run_id=run_id,
            decision="accepted",
            spec_id=spec["id"],
            spec_version=int(spec["version"]),
        )
    )


@router.post("/ingest-runs/{run_id}/drift/reject")
async def reject_drift(
    run_id: UUID,
    body: DecisionIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> DecisionEnvelope:
    """Mark this file unusable. Nothing is deleted - a rejection has to stay reviewable."""
    settings = config.load()
    await service.reject(
        session,
        run_id,
        note=body.note,
        client_ip_hash=client_ip_hash(request, settings.trusted_proxy_hops, settings.ip_hash_salt),
    )
    return DecisionEnvelope(data=DecisionOut(ingest_run_id=run_id, decision="rejected"))


def _drift(data: dict[str, Any]) -> DriftOut:
    baseline = data["baseline"]
    return DriftOut(
        ingest_run_id=data["ingest_run_id"],
        comparable=bool(data["comparable"]),
        reason=data["reason"],
        status=str(data["status"]),
        decision=data["decision"],
        blocking_count=int(data.get("blocking_count", 0)),
        warning_count=int(data.get("warning_count", 0)),
        baseline=None if baseline is None else BaselineOut(**baseline),
        findings=[
            DriftFindingOut(
                drift_code=str(row["drift_code"]),
                title=str(row["title"]),
                severity=row["severity"],
                column_name=row["column_name"],
                baseline_column_name=row["baseline_column_name"],
                detail=dict(row["detail"]),
            )
            for row in data["findings"]
        ],
        checks_run=[
            DriftCheckOut(
                drift_code=str(row["drift_code"]),
                title=str(row["title"]),
                outcome=str(row["outcome"]),
                reason=row["reason"],
                columns_examined=int(row["columns_examined"]),
            )
            for row in data["checks_run"]
        ],
        comparison=ComparisonOut(
            columns=[
                ColumnComparisonOut(
                    name=str(column["name"]),
                    status=column["status"],
                    baseline=(
                        None if column["baseline"] is None else ColumnSideOut(**column["baseline"])
                    ),
                    current=(
                        None if column["current"] is None else ColumnSideOut(**column["current"])
                    ),
                )
                for column in data["comparison"]["columns"]
            ]
        ),
    )
