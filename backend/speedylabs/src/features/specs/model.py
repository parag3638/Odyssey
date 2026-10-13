"""The spec document as a model, so an unknown key fails loudly instead of being ignored.

Phase 9's plan asked for a committed `canonical/mapping_spec.schema.json` generated from a Pydantic
model, and the first pass shipped `build_document` returning a plain dict instead - no model, no
schema, no validation. The Phase 8-10 tester found the gap and was generous about it, guessing that
`extra="forbid"` elsewhere preserved the safety property. It did not: nothing validated a spec
document, on the way in or out.

Why it matters more than most missing validation
------------------------------------------------
This document decides how every later file from a customer is read. A key nobody recognises in it is
either a spec written by a newer version of this service - in which case reading it as if the
key were
not there is the dangerous outcome, not the safe one - or a corrupted row. Both deserve
`spec_unreadable` and a human, which is what `check_fit` already does with anything it cannot parse.

`extra="forbid"` at every level is the whole mechanism. The models are deliberately a mirror of
`build_document`'s output rather than a second opinion about it: a test asserts a built document
validates, so the two cannot drift apart silently.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.features.layouts.document import KIND_CSV, KIND_FIXED_WIDTH


class SpecTransform(BaseModel):
    """A transform instruction. Open on purpose - the **kinds** are a closed set validated by
    `mapping/transforms.parse`, which is the one place that knows their parameters, and duplicating
    eleven parameter shapes here would be a second definition to keep in step."""

    kind: str

    model_config = {"extra": "allow"}


class SpecMappingEntry(BaseModel):
    source_column: str = Field(alias="sourceColumn")
    transform: SpecTransform

    model_config = {"populate_by_name": True, "extra": "forbid"}


class SpecField(BaseModel):
    """One fixed-width field's boundaries, as the spec remembers them."""

    index: int = Field(ge=0)
    name: str = Field(min_length=1)
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    implied_scale: int | None = Field(default=None, alias="impliedScale")
    quirks: list[str] = Field(default_factory=list[str])

    model_config = {"populate_by_name": True, "extra": "forbid"}


class FixedWidthSpecLayout(BaseModel):
    encoding: str
    record_width: int = Field(alias="recordWidth", ge=1)
    footer_row_count: int = Field(alias="footerRowCount", ge=0)
    fields: list[SpecField] = Field(min_length=1)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class CsvSpecLayout(BaseModel):
    encoding: str
    delimiter: str | None = None
    quote_char: str | None = Field(default=None, alias="quoteChar")
    has_header: bool = Field(alias="hasHeader")
    header_row_index: int | None = Field(default=None, alias="headerRowIndex")
    footer_row_count: int = Field(alias="footerRowCount", ge=0)
    #: The file's full ordered header, not only the mapped columns - a column that appears,
    #: disappears or is renamed is what P10 compares.
    columns: list[str] = Field(min_length=1)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class FixedWidthSpecDocument(BaseModel):
    canonical_schema_version: str = Field(alias="canonicalSchemaVersion")
    kind: Literal["fixed_width"]
    layout: FixedWidthSpecLayout
    mapping: dict[str, SpecMappingEntry] = Field(min_length=1)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class CsvSpecDocument(BaseModel):
    canonical_schema_version: str = Field(alias="canonicalSchemaVersion")
    kind: Literal["csv"]
    layout: CsvSpecLayout
    mapping: dict[str, SpecMappingEntry] = Field(min_length=1)

    model_config = {"populate_by_name": True, "extra": "forbid"}


def validate_document(document: dict[str, Any]) -> None:
    """Raise if this is not a spec document. The kind chooses the model.

    A discriminated union would be tidier in one model, but the two layouts share no fields and the
    error a reader gets from the wrong branch of a union is much worse than "this is not a CSV
    spec".
    """
    kind = document.get("kind")
    if kind == KIND_FIXED_WIDTH:
        FixedWidthSpecDocument.model_validate(document)
    elif kind == KIND_CSV:
        CsvSpecDocument.model_validate(document)
    else:
        raise ValueError(f"{kind!r} is not a layout kind a spec can describe")


def schema_document() -> dict[str, Any]:
    """The published JSON Schema: one `oneOf` over the two kinds.

    Generated from the models, so `make canonical` and a test keep the committed file honest - the
    arrangement `invoice_line.schema.json` already uses.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://speedy-onboard.example/canonical/mapping_spec/1.0.0",
        "title": "Mapping spec",
        "description": (
            "How one customer's export of a given format is read: the layout and the canonical "
            "mapping, as approved. Every object forbids unknown keys - a key this service does not "
            "recognise means the document was written by a different version, which a reader must "
            "refuse rather than silently ignore."
        ),
        "oneOf": [
            FixedWidthSpecDocument.model_json_schema(mode="validation", by_alias=True),
            CsvSpecDocument.model_json_schema(mode="validation", by_alias=True),
        ],
    }
