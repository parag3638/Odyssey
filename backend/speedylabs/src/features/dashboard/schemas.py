"""Dashboard shapes. camelCase on the wire (BACKEND.md §3)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class MoneyOut(BaseModel):
    amount: str
    currency: str


class StageTimingOut(BaseModel):
    ms: int
    attempts: int


class RunRowOut(BaseModel):
    ingest_run_id: UUID = Field(serialization_alias="ingestRunId")
    original_filename: str = Field(serialization_alias="originalFilename")
    status: str
    row_count: int | None = Field(default=None, serialization_alias="rowCount")
    created_at: datetime = Field(serialization_alias="createdAt")
    elapsed_ms_to_first_variance: int | None = Field(
        default=None, serialization_alias="elapsedMsToFirstVariance"
    )

    model_config = {"populate_by_name": True}


class LatestRunOut(RunRowOut):
    customer_name: str = Field(serialization_alias="customerName")
    #: Per stage, as measured. A stage that has not run is **absent**, never zero.
    stage_timings: dict[str, StageTimingOut] = Field(serialization_alias="stageTimings")
    #: The sum of the stages inside the upload-to-first-variance window. **Null, never partial**,
    #: when any of them is missing - a partial sum presented as a total is not a total.
    machine_ms: int | None = Field(default=None, serialization_alias="machineMs")

    model_config = {"populate_by_name": True}


class QueueOut(BaseModel):
    #: `draining` | `stale` | `unknown`. Never `stuck`: that would be a judgement about work nobody
    #: is doing, which cannot be told from work queued behind a long job. A heartbeat is a fact.
    status: Literal["draining", "stale", "unknown"]
    worker_last_heartbeat_seconds: int | None = Field(
        default=None, serialization_alias="workerLastHeartbeatSeconds"
    )
    waiting_count: int = Field(serialization_alias="waitingCount")
    running_count: int = Field(serialization_alias="runningCount")
    worker_stale_threshold_seconds: int = Field(serialization_alias="workerStaleThresholdSeconds")

    model_config = {"populate_by_name": True}


class EngineOut(BaseModel):
    """The engine that produced the stored figures, read from the rows themselves."""

    name: str
    version: str


class DashboardOut(BaseModel):
    latest_run: LatestRunOut | None = Field(default=None, serialization_alias="latestRun")
    rows_processed: int = Field(serialization_alias="rowsProcessed")
    blocking_findings: int = Field(serialization_alias="blockingFindings")
    explained_pct: str | None = Field(default=None, serialization_alias="explainedPct")
    absolute_variance: MoneyOut = Field(serialization_alias="absoluteVariance")
    engine: EngineOut | None = None
    runs: list[RunRowOut]
    queue: QueueOut

    model_config = {"populate_by_name": True}


class DashboardEnvelope(BaseModel):
    data: DashboardOut
