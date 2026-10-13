"""Rebate response shapes. camelCase on the wire (BACKEND.md §3).

Every amount, quantity and rate is a **string**, as §3 requires: a JSON number would be parsed back
into a float by every client, which is the precise error this pipeline exists to avoid.

**A rate is a bare 6dp decimal string with a sibling `rateCurrency`, not a `Money` object.** Flagged
as a deliberate §3 deviation rather than left quiet: §3's money shape goes through `Money.to_api()`,
which quantises to cents - and a sub-cent rate quantised to cents silently changes a commercial term
($0.185 becomes $0.18, a 3.8% cut). `UNIT_PRICE_SCALE` is 6 for exactly this reason, and
`canonical_lines.unit_price` already serialises as a bare 6dp string.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class EngineOut(BaseModel):
    """Which engine produced a number. Rendered beside every amount it computed."""

    name: str
    version: str
    #: Always true today, and read from the engine rather than hardcoded in the UI, so swapping in a
    #: real engine relabels the page instead of leaving a false claim behind.
    stand_in: bool = Field(default=True, serialization_alias="standIn")

    model_config = {"populate_by_name": True}


class MoneyOut(BaseModel):
    amount: str
    currency: str


class CalculateAcceptedOut(BaseModel):
    ingest_run_id: UUID = Field(serialization_alias="ingestRunId")
    status: str
    status_url: str = Field(serialization_alias="statusUrl")

    model_config = {"populate_by_name": True}


class CalculateAcceptedEnvelope(BaseModel):
    data: CalculateAcceptedOut


class ProgramOutcomeOut(BaseModel):
    """One program's outcome on this run. The row that makes a `0.00` legible."""

    program_id: UUID = Field(serialization_alias="programId")
    external_ref: str | None = Field(default=None, serialization_alias="externalRef")
    program_name: str = Field(serialization_alias="programName")
    kind: str
    outcome: Literal["calculated", "no_lines_matched", "out_of_effective_range", "errored"]
    reason: str | None = None
    lines_examined: int = Field(serialization_alias="linesExamined")
    lines_matched: int = Field(serialization_alias="linesMatched")
    periods_written: int = Field(serialization_alias="periodsWritten")
    amount: MoneyOut
    engine: EngineOut

    model_config = {"populate_by_name": True}


class BrandCoverageOut(BaseModel):
    """What became of one brand spelling in the file.

    `noProgram` and `unknownBrand` are separate because they need separate actions: the first is a
    commercial gap, the second is almost always a mapping fault.
    """

    brand_value: str = Field(serialization_alias="brandValue")
    manufacturer_name: str | None = Field(default=None, serialization_alias="manufacturerName")
    resolution: Literal["program", "no_program", "unknown_brand"]
    line_count: int = Field(serialization_alias="lineCount")

    model_config = {"populate_by_name": True}


class RebateSummaryOut(BaseModel):
    engine: EngineOut
    #: True when the stored rows were produced by a different engine version from the one running
    #: now. The stored rows keep reporting the version that made them - recomputing silently would
    #: destroy the audit trail and move numbers under a variance already computed from them.
    engine_stale: bool = Field(serialization_alias="engineStale")
    current_engine: EngineOut = Field(serialization_alias="currentEngine")
    calculated: bool
    calculated_at: datetime | None = Field(default=None, serialization_alias="calculatedAt")
    total_amount: MoneyOut = Field(serialization_alias="totalAmount")
    contributed_line_count: int = Field(serialization_alias="contributedLineCount")
    skipped_line_count: int = Field(serialization_alias="skippedLineCount")
    out_of_scope_line_count: int = Field(serialization_alias="outOfScopeLineCount")
    programs: list[ProgramOutcomeOut]
    brands: list[BrandCoverageOut]
    brands_truncated: bool = Field(serialization_alias="brandsTruncated")

    model_config = {"populate_by_name": True}


class RebateSummaryEnvelope(BaseModel):
    data: RebateSummaryOut


class RebateCalcOut(BaseModel):
    rebate_calc_id: UUID = Field(serialization_alias="rebateCalcId")
    program_id: UUID = Field(serialization_alias="programId")
    external_ref: str | None = Field(default=None, serialization_alias="externalRef")
    program_name: str = Field(serialization_alias="programName")
    kind: str
    period_start: date = Field(serialization_alias="periodStart")
    period_end: date = Field(serialization_alias="periodEnd")
    basis_qty: str = Field(serialization_alias="basisQty")
    #: The program's basis, read from `rebate_programs` rather than copied onto every calc row -
    #: a copied column no constraint can police is one that eventually disagrees with its source.
    basis_uom: str = Field(serialization_alias="basisUom")
    amount: MoneyOut
    contributed_line_count: int = Field(serialization_alias="contributedLineCount")
    skipped_line_count: int = Field(serialization_alias="skippedLineCount")
    out_of_scope_line_count: int = Field(serialization_alias="outOfScopeLineCount")
    engine: EngineOut

    model_config = {"populate_by_name": True}


class RebateCalcsEnvelope(BaseModel):
    data: list[RebateCalcOut]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")

    model_config = {"populate_by_name": True}


class RebateCalcLineOut(BaseModel):
    canonical_line_id: UUID = Field(serialization_alias="canonicalLineId")
    #: A record position within this run, which is what the UI calls "Source row". Never "Line".
    source_row_number: int = Field(serialization_alias="sourceRowNumber")
    invoice_no: str = Field(serialization_alias="invoiceNo")
    invoice_date: date = Field(serialization_alias="invoiceDate")
    dist_item_id: str = Field(serialization_alias="distItemId")
    qty: str
    uom: str
    pack_size: int | None = Field(default=None, serialization_alias="packSize")
    catch_weight_lbs: str | None = Field(default=None, serialization_alias="catchWeightLbs")
    line_status: Literal["contributed", "skipped", "out_of_basis_scope"] = Field(
        serialization_alias="lineStatus"
    )
    skipped_reason: str | None = Field(default=None, serialization_alias="skippedReason")
    #: The sentence that says what to fix, from `codes.py`. Null unless skipped.
    skipped_detail: str | None = Field(default=None, serialization_alias="skippedDetail")
    basis_contribution: str = Field(serialization_alias="basisContribution")
    amount_contribution: MoneyOut = Field(serialization_alias="amountContribution")

    model_config = {"populate_by_name": True}


class RebateCalcLinesEnvelope(BaseModel):
    data: list[RebateCalcLineOut]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")

    model_config = {"populate_by_name": True}


class RebateProgramOut(BaseModel):
    program_id: UUID = Field(serialization_alias="programId")
    external_ref: str | None = Field(default=None, serialization_alias="externalRef")
    name: str
    kind: str
    brand_manufacturer: str = Field(serialization_alias="brandManufacturer")
    #: 6dp, bare - see the module docstring on why this is not a `Money`.
    rate: str
    rate_currency: str = Field(serialization_alias="rateCurrency")
    uom_basis: str = Field(serialization_alias="uomBasis")
    effective_from: date = Field(serialization_alias="effectiveFrom")
    effective_to: date | None = Field(default=None, serialization_alias="effectiveTo")

    model_config = {"populate_by_name": True}


class RebateProgramsEnvelope(BaseModel):
    data: list[RebateProgramOut]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")

    model_config = {"populate_by_name": True}
