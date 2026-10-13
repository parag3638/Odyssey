"""Readiness response shapes. camelCase on the wire (BACKEND.md §3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ReadinessAcceptedOut(BaseModel):
    ingest_run_id: UUID = Field(serialization_alias="ingestRunId")
    status: str
    status_url: str = Field(serialization_alias="statusUrl")

    model_config = {"populate_by_name": True}


class ReadinessAcceptedEnvelope(BaseModel):
    data: ReadinessAcceptedOut


class FindingOut(BaseModel):
    check_code: str = Field(serialization_alias="checkCode")
    title: str
    severity: str
    #: What the check found, in its own unit: lines for most, business days for a date gap,
    #: one file for the footer mismatch. `affectedUnit` names it so a UI never has to guess.
    affected_rows: int = Field(serialization_alias="affectedRows")
    affected_unit: str = Field(serialization_alias="affectedUnit")
    #: A decimal string or null. Null means the figure is not knowable, not zero - the value
    #: of invoices missing from a file is not a number anybody has.
    affected_amount: str | None = Field(default=None, serialization_alias="affectedAmount")
    what_happened: str = Field(serialization_alias="whatHappened")
    who_fixes: str = Field(serialization_alias="whoFixes")
    detail: dict[str, Any]
    example_row_numbers: list[int] = Field(serialization_alias="exampleRowNumbers")

    model_config = {"populate_by_name": True}


class CheckRunOut(BaseModel):
    """One check's outcome, whether or not it found anything.

    Present for every check on every run: that is what makes the checklist demonstrably
    generated from this file. `notApplicableReason` is the difference between "we checked and
    it was fine" and "there was nothing to check", which a template cannot express.
    """

    check_code: str = Field(serialization_alias="checkCode")
    title: str
    severity: str
    outcome: str
    not_applicable_reason: str | None = Field(
        default=None, serialization_alias="notApplicableReason"
    )
    errored_message: str | None = Field(default=None, serialization_alias="erroredMessage")
    rows_examined: int = Field(serialization_alias="rowsExamined")
    context: dict[str, Any] = Field(default_factory=dict[str, Any])

    model_config = {"populate_by_name": True}


class ReadinessOut(BaseModel):
    ingest_run_id: UUID = Field(serialization_alias="ingestRunId")
    status: str
    blocking_count: int = Field(serialization_alias="blockingCount")
    warning_count: int = Field(serialization_alias="warningCount")
    rows_examined: int = Field(serialization_alias="rowsExamined")
    readiness_at: datetime | None = Field(default=None, serialization_alias="readinessAt")
    findings: list[FindingOut]
    checks_run: list[CheckRunOut] = Field(serialization_alias="checksRun")

    model_config = {"populate_by_name": True}


class ReadinessEnvelope(BaseModel):
    data: ReadinessOut
