"""The dashboard route. Parse, delegate, shape (BACKEND.md §2)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.features.dashboard import service
from src.features.dashboard.schemas import (
    DashboardEnvelope,
    DashboardOut,
    EngineOut,
    LatestRunOut,
    MoneyOut,
    QueueOut,
    RunRowOut,
    StageTimingOut,
)
from src.lib import db
from src.lib.money import DEFAULT_CURRENCY, Money

router = APIRouter(prefix="/v1", tags=["dashboard"])


def _money(value: Any) -> MoneyOut:
    return MoneyOut(**Money(amount=value, currency=DEFAULT_CURRENCY).to_api())


def _run_row(row: dict[str, Any]) -> RunRowOut:
    return RunRowOut(
        ingest_run_id=row["ingest_run_id"],
        original_filename=str(row["original_filename"]),
        status=str(row["status"]),
        row_count=row["row_count"],
        created_at=row["created_at"],
        elapsed_ms_to_first_variance=row["elapsed_ms_to_first_variance"],
    )


@router.get("/dashboard")
async def get_dashboard(
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> DashboardEnvelope:
    """What this instance has done, and how long the machine actually spent on it.

    `elapsedMsToFirstVariance` is **wall clock** from the upload, including every human decision in
    between; `machineMs` is what this software spent. On the demo run those are 34,766 ms and
    3,535 ms. Both are reported, because reporting only the first and calling it a pipeline budget
    would be a claim this project cannot support.
    """
    data = await service.dashboard(session)
    latest = data["latest_run"]
    return DashboardEnvelope(
        data=DashboardOut(
            latest_run=(
                None
                if latest is None
                else LatestRunOut(
                    **_run_row(latest).model_dump(),
                    customer_name=str(latest["customer_name"]),
                    stage_timings={
                        name: StageTimingOut(ms=int(value["ms"]), attempts=int(value["attempts"]))
                        for name, value in latest["stage_timings"].items()
                    },
                    machine_ms=latest["machine_ms"],
                )
            ),
            rows_processed=int(data["rows_processed"]),
            blocking_findings=int(data["blocking_findings"]),
            explained_pct=data["explained_pct"],
            absolute_variance=_money(data["absolute_variance"]),
            engine=(None if data["engine"] is None else EngineOut(**data["engine"])),
            runs=[_run_row(row) for row in data["runs"]],
            queue=QueueOut(**data["queue"]),
        )
    )
