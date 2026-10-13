"""Mapping-spec request and response shapes. camelCase on the wire (BACKEND.md §3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

#: Matches `mapping_specs.approved_note`'s own check constraint.
MAX_NOTE_LENGTH = 2000


class SpecApproveIn(BaseModel):
    """`force` is explicit and defaults to false: overriding a failing verdict is a decision
    somebody makes, never something a client does by omission."""

    source_file_id: UUID = Field(validation_alias="sourceFileId")
    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)
    force: bool = False

    model_config = {"populate_by_name": True, "extra": "forbid"}


class SpecSummaryOut(BaseModel):
    id: UUID
    customer_id: UUID = Field(serialization_alias="customerId")
    kind: Literal["csv", "fixed_width"]
    version: int
    status: Literal["approved", "superseded"]
    canonical_schema_version: str = Field(serialization_alias="canonicalSchemaVersion")
    #: The content hash. A client comparing two versions needs no document to know they differ.
    spec_sha256: str = Field(serialization_alias="specSha256")
    approved_from_source_file_id: UUID | None = Field(
        default=None, serialization_alias="approvedFromSourceFileId"
    )
    approved_note: str | None = Field(default=None, serialization_alias="approvedNote")
    approved_forced: bool = Field(serialization_alias="approvedForced")
    approved_at: datetime = Field(serialization_alias="approvedAt")

    model_config = {"populate_by_name": True}


class SpecOut(SpecSummaryOut):
    """One spec with its document. The document is published as-is: it is the contract."""

    spec: dict[str, Any]


class SpecEnvelope(BaseModel):
    data: SpecOut


class SpecListEnvelope(BaseModel):
    data: list[SpecSummaryOut]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")

    model_config = {"populate_by_name": True}
