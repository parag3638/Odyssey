"""Drift request and response shapes. camelCase on the wire (BACKEND.md §3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

MAX_NOTE_LENGTH = 2000


class DecisionIn(BaseModel):
    """A note is required by the schema, not only by the service: a decision that changes how every
    later file is read, with no explanation, is one nobody can review."""

    note: str = Field(min_length=1, max_length=MAX_NOTE_LENGTH)
    #: Only meaningful on accept, and only when a verdict failed. Explicit, never implied.
    force: bool = False

    model_config = {"populate_by_name": True, "extra": "forbid"}


class BaselineOut(BaseModel):
    approved_spec_version: int = Field(serialization_alias="approvedSpecVersion")
    approved_at: datetime = Field(serialization_alias="approvedAt")
    source_file_id: UUID | None = Field(default=None, serialization_alias="sourceFileId")
    original_filename: str | None = Field(default=None, serialization_alias="originalFilename")
    uploaded_at: datetime | None = Field(default=None, serialization_alias="uploadedAt")

    model_config = {"populate_by_name": True}


class DriftFindingOut(BaseModel):
    drift_code: str = Field(serialization_alias="driftCode")
    title: str
    severity: Literal["blocking", "warning"]
    column_name: str | None = Field(default=None, serialization_alias="columnName")
    baseline_column_name: str | None = Field(default=None, serialization_alias="baselineColumnName")
    #: Baseline value, current value and the threshold that was applied - all three, because a
    #: reader judging "is a 34% heavier average a problem?" needs them and a verdict helps them not
    #: at all.
    detail: dict[str, Any]

    model_config = {"populate_by_name": True}


class DriftCheckOut(BaseModel):
    drift_code: str = Field(serialization_alias="driftCode")
    title: str
    outcome: str
    reason: str | None = None
    columns_examined: int = Field(serialization_alias="columnsExamined")

    model_config = {"populate_by_name": True}


class ColumnSideOut(BaseModel):
    column_index: int = Field(serialization_alias="columnIndex")
    type_guess: str = Field(serialization_alias="typeGuess")
    null_share: str = Field(serialization_alias="nullShare")
    distinct_count: int = Field(serialization_alias="distinctCount")
    #: A decimal string or null. Null means "no numeric values", not zero.
    mean: str | None = None

    model_config = {"populate_by_name": True}


class ColumnComparisonOut(BaseModel):
    name: str
    status: Literal["same", "changed", "added", "removed"]
    baseline: ColumnSideOut | None = None
    current: ColumnSideOut | None = None


class ComparisonOut(BaseModel):
    columns: list[ColumnComparisonOut]


class DriftOut(BaseModel):
    ingest_run_id: UUID = Field(serialization_alias="ingestRunId")
    #: False when there is nothing to compare against. A **state this screen renders**, not an
    #: error: "this is your first file from this customer" is not a failure.
    comparable: bool
    reason: str | None = None
    status: str
    decision: str | None = None
    blocking_count: int = Field(default=0, serialization_alias="blockingCount")
    warning_count: int = Field(default=0, serialization_alias="warningCount")
    baseline: BaselineOut | None = None
    findings: list[DriftFindingOut] = Field(default_factory=list[DriftFindingOut])
    checks_run: list[DriftCheckOut] = Field(
        default_factory=list[DriftCheckOut], serialization_alias="checksRun"
    )
    comparison: ComparisonOut

    model_config = {"populate_by_name": True}


class DriftEnvelope(BaseModel):
    data: DriftOut


class DecisionOut(BaseModel):
    ingest_run_id: UUID = Field(serialization_alias="ingestRunId")
    decision: Literal["accepted", "rejected"]
    #: Set on accept: the spec version this file's mapping became.
    spec_id: UUID | None = Field(default=None, serialization_alias="specId")
    spec_version: int | None = Field(default=None, serialization_alias="specVersion")

    model_config = {"populate_by_name": True}


class DecisionEnvelope(BaseModel):
    data: DecisionOut
