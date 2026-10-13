"""Mapping request and response shapes."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from src.features.canonical.model import CANONICAL_FIELD_NAMES

#: A canonical field name, as the schema publishes it. Validated against the committed
#: schema in the service - not here - so the error can be `422 unknown_canonical_field`
#: with the name, rather than Pydantic's generic "not a valid enum member".
CanonicalFieldName = Annotated[str, StringConstraints(min_length=1, max_length=64)]

#: Where a mapping entry came from. `manual` and `spec` exist now; `llm` and `cache`
#: arrive with P6. Stored, so the UI can show a human which choices were theirs.
Origin = Literal["manual", "llm", "cache", "spec"]

#: A source column name. Bounded for the same reason layout field names are: it is used
#: as an identifier downstream, never interpolated into SQL.
SourceColumnName = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class MappingEntryIn(BaseModel):
    source_column: SourceColumnName = Field(validation_alias="sourceColumn")
    #: The transform document. Parsed and range-checked by
    #: `features/mapping/transforms.parse`, which owns the closed set - not by Pydantic,
    #: so one module decides what a transform is.
    transform: dict[str, Any]
    origin: Origin = "manual"
    #: A decimal string, 0-1, when a model proposed this. Null for a human's choice.
    confidence: str | None = None
    reason: str | None = Field(default=None, max_length=2_000)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class MappingPatchIn(BaseModel):
    """A partial update: the canonical fields named here, and no others.

    Partial on purpose (BACKEND.md §3's PATCH semantics). The UI saves one row at a time,
    and P6 proposes a whole mapping; both go through the same merge, and neither can
    clobber a field it did not mention.
    """

    fields: dict[CanonicalFieldName, MappingEntryIn] = Field(
        min_length=1, max_length=len(CANONICAL_FIELD_NAMES)
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}


class MappingEntryOut(BaseModel):
    source_column: str = Field(serialization_alias="sourceColumn")
    transform: dict[str, Any]
    origin: str
    confidence: str | None = None
    reason: str | None = None

    model_config = {"populate_by_name": True}


class ValidationOut(BaseModel):
    canonical_field: str = Field(serialization_alias="canonicalField")
    verdict: str
    checked_rows: int = Field(serialization_alias="checkedRows")
    failed_rows: int = Field(serialization_alias="failedRows")
    detail: dict[str, Any]
    example_row_numbers: list[int] = Field(serialization_alias="exampleRowNumbers")

    model_config = {"populate_by_name": True}


class MappingOut(BaseModel):
    source_file_id: UUID = Field(serialization_alias="sourceFileId")
    layout_version: int = Field(serialization_alias="layoutVersion")
    canonical_schema_version: str = Field(serialization_alias="canonicalSchemaVersion")
    #: canonical field -> entry. Empty for a file nobody has mapped yet, which is a 200
    #: and not a 404: "no mapping yet" is a state, not a missing resource.
    fields: dict[str, MappingEntryOut]
    #: Every required canonical field with no entry, so the UI's footer does not have to
    #: re-derive requiredness from the schema.
    unmapped_required: list[str] = Field(serialization_alias="unmappedRequired")
    #: The verdicts from the last validate call, empty until one has run.
    validations: list[ValidationOut]
    #: Whether those verdicts describe a **different** mapping from the one above - true after
    #: an edit, false for a draft nobody has validated (there is nothing to be out of date).
    #: Published rather than re-derived in the client, because the server is what refuses an
    #: approval on stale verdicts and the two answers must be the same answer.
    validations_stale: bool = Field(default=False, serialization_alias="validationsStale")

    model_config = {"populate_by_name": True}


class MappingEnvelope(BaseModel):
    data: MappingOut


class ValidationSummary(BaseModel):
    source_file_id: UUID = Field(serialization_alias="sourceFileId")
    layout_version: int = Field(serialization_alias="layoutVersion")
    checked_rows: int = Field(serialization_alias="checkedRows")
    validations: list[ValidationOut]

    model_config = {"populate_by_name": True}


class ValidationEnvelope(BaseModel):
    data: ValidationSummary
