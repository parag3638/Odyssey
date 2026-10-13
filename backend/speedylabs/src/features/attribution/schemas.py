"""Attribution shapes. camelCase on the wire (BACKEND.md §3)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MoneyOut(BaseModel):
    amount: str
    currency: str


class AttributeAcceptedOut(BaseModel):
    ingest_run_id: UUID = Field(serialization_alias="ingestRunId")
    status: str
    status_url: str = Field(serialization_alias="statusUrl")

    model_config = {"populate_by_name": True}


class AttributeAcceptedEnvelope(BaseModel):
    data: AttributeAcceptedOut


class CauseShareOut(BaseModel):
    root_cause: str = Field(serialization_alias="rootCause")
    title: str
    #: Signed. `date_cutoff` nets to zero because it relocates money between periods.
    net_amount: MoneyOut = Field(serialization_alias="netAmount")
    #: What the chart is sorted and sized by - a cause that nets to zero still did work.
    absolute_amount: MoneyOut = Field(serialization_alias="absoluteAmount")
    link_count: int = Field(serialization_alias="linkCount")
    line_count: int = Field(serialization_alias="lineCount")

    model_config = {"populate_by_name": True}


class RuleFailureOut(BaseModel):
    rule: str
    line_count: int = Field(serialization_alias="lineCount")

    model_config = {"populate_by_name": True}


class AttributionSummaryOut(BaseModel):
    attributed: bool
    variance_count: int = Field(serialization_alias="varianceCount")
    link_count: int = Field(serialization_alias="linkCount")
    #: Over **absolute** dollars - see the service. Null when nothing has been attributed yet,
    #: rather than 0% or 100%, both of which would be a claim.
    explained_pct: str | None = Field(default=None, serialization_alias="explainedPct")
    explained_amount: MoneyOut = Field(serialization_alias="explainedAmount")
    unexplained_amount: MoneyOut = Field(serialization_alias="unexplainedAmount")
    absolute_variance: MoneyOut = Field(serialization_alias="absoluteVariance")
    by_cause: list[CauseShareOut] = Field(serialization_alias="byCause")
    attributed_at: datetime | None = Field(default=None, serialization_alias="attributedAt")
    #: True when the comparison was recomputed after this explanation was made. A flag with a
    #: "Re-explain" action, not a refusal - the pattern `engineStale` and `variancesStale` set.
    attribution_stale: bool = Field(default=False, serialization_alias="attributionStale")
    #: Empty on a clean run. Non-empty means some dollars are in residue because a rule could not
    #: run, which is a different thing from no rule applying.
    rule_failures: list[RuleFailureOut] = Field(
        default_factory=list["RuleFailureOut"], serialization_alias="ruleFailures"
    )

    model_config = {"populate_by_name": True}


class AttributionSummaryEnvelope(BaseModel):
    data: AttributionSummaryOut


class AttributedLineOut(BaseModel):
    link_id: UUID = Field(serialization_alias="linkId")
    root_cause: str = Field(serialization_alias="rootCause")
    title: str
    contribution_amount: MoneyOut = Field(serialization_alias="contributionAmount")
    #: What the rule saw, so a reader can check the judgement rather than take it. Carries
    #: `estimated: true` wherever the amount is an estimate.
    evidence: dict[str, object]
    canonical_line_id: UUID | None = Field(default=None, serialization_alias="canonicalLineId")
    source_row_number: int | None = Field(default=None, serialization_alias="sourceRowNumber")
    invoice_no: str | None = Field(default=None, serialization_alias="invoiceNo")
    invoice_date: date | None = Field(default=None, serialization_alias="invoiceDate")
    dist_item_id: str | None = Field(default=None, serialization_alias="distItemId")
    qty: str | None = None
    uom: str | None = None

    model_config = {"populate_by_name": True}


class AttributedLinesEnvelope(BaseModel):
    data: list[AttributedLineOut]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")

    model_config = {"populate_by_name": True}
