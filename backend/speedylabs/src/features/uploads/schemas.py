"""Input and output shapes for uploads. One source for validation, docs and FE types."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

#: The two things a customer sends. A form field rather than two routes, so the caps,
#: the type sniff and the rate limit live in exactly one place.
UploadKind = Literal["erp_invoice_lines", "legacy_rebate"]


class UploadAccepted(BaseModel):
    """202: the file is stored and a job is queued. The work has not happened yet."""

    ingest_run_id: UUID = Field(serialization_alias="ingestRunId")
    source_file_id: UUID = Field(serialization_alias="sourceFileId")
    status_url: str = Field(serialization_alias="statusUrl")

    model_config = {"populate_by_name": True}


class UploadAcceptedEnvelope(BaseModel):
    data: UploadAccepted
