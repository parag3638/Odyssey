"""Canonical-schema response shapes."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CanonicalFieldOut(BaseModel):
    name: str
    #: The JSON Schema type as published: "string", "integer", "boolean". Money and
    #: quantities are "string" by design - see `features/canonical/model.py`.
    type: str
    required: bool
    #: Present only for an enum field (`uom`).
    enum: list[str] | None = None
    #: The decimal-string pattern, when the field has one. A client that wants to
    #: validate input without fetching the whole JSON Schema can use it directly.
    pattern: str | None = None
    description: str | None = None


class CanonicalSchemaOut(BaseModel):
    version: str
    #: In CSV column order, which is the contract (`canonical/FORMAT.md`).
    fields: list[CanonicalFieldOut]

    model_config = {"populate_by_name": True}


class CanonicalSchemaEnvelope(BaseModel):
    data: CanonicalSchemaOut = Field()


# --------------------------------------------------------------------------------------
# Output of a materialised run
# --------------------------------------------------------------------------------------


class MaterializeAcceptedOut(BaseModel):
    """What a `202` says: where to watch, not what happened (BACKEND.md §3)."""

    ingest_run_id: UUID = Field(serialization_alias="ingestRunId")
    status: str
    status_url: str = Field(serialization_alias="statusUrl")

    model_config = {"populate_by_name": True}


class MaterializeAcceptedEnvelope(BaseModel):
    data: MaterializeAcceptedOut


class OutputSummaryOut(BaseModel):
    ingest_run_id: UUID = Field(serialization_alias="ingestRunId")
    status: str
    canonical_schema_version: str = Field(serialization_alias="canonicalSchemaVersion")
    rows_written: int = Field(serialization_alias="rowsWritten")
    rows_rejected: int = Field(serialization_alias="rowsRejected")
    materialized_at: datetime | None = Field(default=None, serialization_alias="materializedAt")

    model_config = {"populate_by_name": True}


class OutputSummaryEnvelope(BaseModel):
    data: OutputSummaryOut


class CanonicalLineOut(BaseModel):
    """One canonical row, on the wire.

    Every money, quantity and price field is a **string**, as §3 requires: a JSON number
    would be parsed back into a float by every client, which is the precise error the whole
    pipeline exists to avoid. The frontend formats these strings without ever calling
    `Number` on them.
    """

    source_row_number: int = Field(serialization_alias="sourceRowNumber")
    invoice_no: str = Field(serialization_alias="invoiceNo")
    invoice_date: date = Field(serialization_alias="invoiceDate")
    customer_id: str = Field(serialization_alias="customerId")
    customer_name: str = Field(serialization_alias="customerName")
    dist_item_id: str = Field(serialization_alias="distItemId")
    mfr_item_code: str = Field(serialization_alias="mfrItemCode")
    brand_manufacturer: str = Field(serialization_alias="brandManufacturer")
    invoice_vendor: str = Field(serialization_alias="invoiceVendor")
    qty: str
    uom: str
    pack_size: int | None = Field(default=None, serialization_alias="packSize")
    catch_weight_lbs: str | None = Field(default=None, serialization_alias="catchWeightLbs")
    unit_price: str = Field(serialization_alias="unitPrice")
    ext_amount: str = Field(serialization_alias="extAmount")
    is_deviated: bool = Field(serialization_alias="isDeviated")
    deviated_price: str | None = Field(default=None, serialization_alias="deviatedPrice")

    model_config = {"populate_by_name": True}


class CanonicalLinesEnvelope(BaseModel):
    data: list[CanonicalLineOut]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")

    model_config = {"populate_by_name": True}


class MaterializationErrorOut(BaseModel):
    source_row_number: int = Field(serialization_alias="sourceRowNumber")
    canonical_field: str = Field(serialization_alias="canonicalField")
    raw_value: str = Field(serialization_alias="rawValue")
    error_code: str = Field(serialization_alias="errorCode")
    message: str

    model_config = {"populate_by_name": True}


class MaterializationErrorsEnvelope(BaseModel):
    data: list[MaterializationErrorOut]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")

    model_config = {"populate_by_name": True}
