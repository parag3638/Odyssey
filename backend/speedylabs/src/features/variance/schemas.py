"""Variance and legacy-import shapes. camelCase on the wire (BACKEND.md §3).

Every amount is `{"amount","currency"}` and `varianceAmount` **carries its sign**: the convention
is `legacy - calculated`, so a positive variance means the legacy system paid more than we
calculate. Two of the demo's eighteen are negative, because the legacy period cutoff moved March's
lines into April, and a client assuming positives would render them wrong.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class MoneyOut(BaseModel):
    amount: str
    currency: str


class ComputeIn(BaseModel):
    legacy_source_file_id: UUID = Field(validation_alias="legacySourceFileId")

    model_config = {"populate_by_name": True, "extra": "forbid"}


class ComputeAcceptedOut(BaseModel):
    ingest_run_id: UUID = Field(serialization_alias="ingestRunId")
    status: str
    status_url: str = Field(serialization_alias="statusUrl")

    model_config = {"populate_by_name": True}


class ComputeAcceptedEnvelope(BaseModel):
    data: ComputeAcceptedOut


class LegacyFileOut(BaseModel):
    source_file_id: UUID = Field(serialization_alias="sourceFileId")
    original_filename: str = Field(serialization_alias="originalFilename")
    uploaded_at: datetime = Field(serialization_alias="uploadedAt")
    status: str
    error_code: str | None = Field(default=None, serialization_alias="errorCode")
    line_count: int = Field(serialization_alias="lineCount")
    matched_count: int = Field(serialization_alias="matchedCount")

    model_config = {"populate_by_name": True}


class LegacyFilesEnvelope(BaseModel):
    data: list[LegacyFileOut]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")

    model_config = {"populate_by_name": True}


class UnmatchedLineOut(BaseModel):
    """A legacy row naming a program this customer does not have. Reported, never dropped."""

    source_row_number: int = Field(serialization_alias="sourceRowNumber")
    program_external_ref: str = Field(serialization_alias="programExternalRef")
    program_name_text: str | None = Field(default=None, serialization_alias="programNameText")
    period_start: date = Field(serialization_alias="periodStart")
    amount: MoneyOut

    model_config = {"populate_by_name": True}


class VarianceSummaryOut(BaseModel):
    variance_count: int = Field(serialization_alias="varianceCount")
    legacy_total: MoneyOut = Field(serialization_alias="legacyTotal")
    calculated_total: MoneyOut = Field(serialization_alias="calculatedTotal")
    #: Signed. The demo's net is +4,035.14.
    net_variance: MoneyOut = Field(serialization_alias="netVariance")
    #: The sum of absolute values - 9,539.64 on the demo. Shown beside the net because the net
    #: alone reads as six quiet months when the two largest rows are in the two that cancel.
    absolute_variance: MoneyOut = Field(serialization_alias="absoluteVariance")
    computed_at: datetime | None = Field(default=None, serialization_alias="computedAt")
    calculated_at: datetime | None = Field(default=None, serialization_alias="calculatedAt")
    legacy_source_file_id: UUID | None = Field(
        default=None, serialization_alias="legacySourceFileId"
    )
    #: True when the rebates were recalculated after this comparison was made. A flag with a
    #: "Recompute" action, not a refusal - the same choice P11 made for `engineStale`.
    variances_stale: bool = Field(serialization_alias="variancesStale")
    unmatched: list[UnmatchedLineOut]
    unmatched_count: int = Field(serialization_alias="unmatchedCount")

    model_config = {"populate_by_name": True}


class VarianceSummaryEnvelope(BaseModel):
    data: VarianceSummaryOut


class VarianceOut(BaseModel):
    variance_id: UUID = Field(serialization_alias="varianceId")
    program_id: UUID = Field(serialization_alias="programId")
    external_ref: str | None = Field(default=None, serialization_alias="externalRef")
    program_name: str = Field(serialization_alias="programName")
    kind: str
    period_start: date = Field(serialization_alias="periodStart")
    period_end: date = Field(serialization_alias="periodEnd")
    legacy_amount: MoneyOut = Field(serialization_alias="legacyAmount")
    calculated_amount: MoneyOut = Field(serialization_alias="calculatedAmount")
    variance_amount: MoneyOut = Field(serialization_alias="varianceAmount")
    presence: Literal["both", "legacy_only", "calculated_only"]
    root_cause: str = Field(serialization_alias="rootCause")
    explained: bool

    model_config = {"populate_by_name": True}


class VariancesEnvelope(BaseModel):
    data: list[VarianceOut]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")

    model_config = {"populate_by_name": True}


class LegacyLineOut(BaseModel):
    """One row as the import read it, with what it matched."""

    source_row_number: int = Field(serialization_alias="sourceRowNumber")
    program_external_ref: str = Field(serialization_alias="programExternalRef")
    #: The name the file itself carried. Never a matching key - see the importer.
    program_name_text: str | None = Field(default=None, serialization_alias="programNameText")
    program_id: UUID | None = Field(default=None, serialization_alias="programId")
    #: Our program's name, when one matched.
    program_name: str | None = Field(default=None, serialization_alias="programName")
    period_start: date = Field(serialization_alias="periodStart")
    period_end: date = Field(serialization_alias="periodEnd")
    amount: MoneyOut
    match_status: Literal["matched", "unmatched_program"] = Field(serialization_alias="matchStatus")

    model_config = {"populate_by_name": True}


class LegacyLinesEnvelope(BaseModel):
    data: list[LegacyLineOut]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")

    model_config = {"populate_by_name": True}
