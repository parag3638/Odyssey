"""Layout response shapes."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StrictInt

from src.lib.parsers.quirks import QUIRK_CODES

LayoutKind = Literal["csv", "fixed_width"]
Confidence = Literal["high", "low"]

#: Mirrors the `file_layouts_fields_count_check` constraint and P4's API contract.
MAX_FIELDS = 200
MAX_NAME_LENGTH = 64
MIN_IMPLIED_SCALE = 0
MAX_IMPLIED_SCALE = 6


class QuirkEvidenceOut(BaseModel):
    code: str
    matched: int
    considered: int
    #: A decimal string, like every other ratio in this API.
    match_fraction: str = Field(serialization_alias="matchFraction")
    claimed: bool

    model_config = {"populate_by_name": True}


class LayoutFieldOut(BaseModel):
    index: int
    name: str
    #: 1-based inclusive character range. Null for CSV, where a column has an ordinal.
    start: int | None = None
    end: int | None = None
    implied_scale: int | None = Field(default=None, serialization_alias="impliedScale")
    #: What to pre-select in the editor's scale picker. Never applied by the server.
    proposed_scale: int | None = Field(default=None, serialization_alias="proposedScale")
    #: "proposed" when the scale is a default rather than a reading - the UI must not
    #: present a proposal as a detection.
    scale_source: str | None = Field(default=None, serialization_alias="scaleSource")
    quirks: list[str] = Field(default_factory=list)
    confidence: Confidence = "high"
    note: str | None = None
    quirk_evidence: list[QuirkEvidenceOut] = Field(
        default_factory=list[QuirkEvidenceOut], serialization_alias="quirkEvidence"
    )

    model_config = {"populate_by_name": True}


class AmbiguousRegionOut(BaseModel):
    field_index: int = Field(serialization_alias="fieldIndex")
    start: int
    end: int
    reason: str

    model_config = {"populate_by_name": True}


class InferenceEvidenceOut(BaseModel):
    records_sampled: int = Field(serialization_alias="recordsSampled")
    record_width: int = Field(serialization_alias="recordWidth")
    threshold: str
    #: Blank share per character position, index 0 = position 1, as decimal strings.
    #: This is what lets the ruler show where the evidence for a boundary actually is.
    blank_fractions: list[str] = Field(serialization_alias="blankFractions")
    ambiguous_regions: list[AmbiguousRegionOut] = Field(serialization_alias="ambiguousRegions")
    confidence: Confidence
    notes: list[str]

    model_config = {"populate_by_name": True}


class LayoutOut(BaseModel):
    source_file_id: UUID = Field(serialization_alias="sourceFileId")
    version: int
    kind: LayoutKind
    encoding: str
    delimiter: str | None = None
    quote_char: str | None = Field(default=None, serialization_alias="quoteChar")
    has_header: bool = Field(serialization_alias="hasHeader")
    header_row_index: int | None = Field(default=None, serialization_alias="headerRowIndex")
    footer_row_count: int = Field(serialization_alias="footerRowCount")
    #: What the trailer declares the file contains. Published as a **decimal string** (§3),
    #: like every other amount, and null when there is no trailer or its shape is not one we
    #: recognise. Read-only: the editor cannot set it, because it is the file's own statement
    #: and the readiness check's whole job is to compare it with the detail lines.
    footer_declared_rows: int | None = Field(default=None, serialization_alias="footerDeclaredRows")
    footer_total: str | None = Field(default=None, serialization_alias="footerTotal")
    record_width: int | None = Field(default=None, serialization_alias="recordWidth")
    fields: list[LayoutFieldOut]
    inference_evidence: InferenceEvidenceOut | None = Field(
        default=None, serialization_alias="inferenceEvidence"
    )
    #: Verbatim records the editor's ruler draws its cut markers over. Present on every
    #: version, including a human-authored one that carries no inference evidence.
    sample_records: list[str] = Field(
        default_factory=list[str], serialization_alias="sampleRecords"
    )
    inferred: bool
    edited_by_human: bool = Field(serialization_alias="editedByHuman")

    model_config = {"populate_by_name": True}


class LayoutEnvelope(BaseModel):
    data: LayoutOut


# --------------------------------------------------------------------------------------
# PATCH input
# --------------------------------------------------------------------------------------


class LayoutFieldIn(BaseModel):
    """One proposed field. Bounds are 1-based and inclusive.

    Pydantic enforces types and the cheap bounds so a malformed body is a 400 before any
    layout rule runs; `features/layouts/validate.py` owns the rules that need to see the
    whole field list at once (overlap, duplicate names, the record width).
    """

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    start: StrictInt = Field(ge=1)
    end: StrictInt = Field(ge=1)
    #: `StrictInt`, so `"2"` is refused rather than quietly coerced. An implied scale is
    #: the single most consequential number in a layout - wrong by one is wrong by 10x on
    #: every rebate dollar - and a client sending it as a string is a client with a bug
    #: that should be told, not accommodated.
    implied_scale: StrictInt | None = Field(default=None, validation_alias="impliedScale")
    #: Bounded by the vocabulary itself: there are five quirk codes, so a field cannot
    #: meaningfully claim more than five. An unbounded list let a 1.65 MB body through
    #: made entirely of valid, duplicated codes.
    quirks: list[str] = Field(default_factory=list[str], max_length=len(QUIRK_CODES))

    model_config = {"populate_by_name": True, "extra": "forbid"}


class LayoutPatchIn(BaseModel):
    """A complete replacement field list, not a diff.

    Whole-list rather than per-field edits because the rules that matter - overlap,
    coverage, uniqueness - are properties of the set. A diff would let a client remove
    one field and leave the server guessing whether the resulting gap was intended.
    """

    fields: list[LayoutFieldIn] = Field(min_length=1, max_length=MAX_FIELDS)
    footer_row_count: int | None = Field(default=None, ge=0, validation_alias="footerRowCount")

    model_config = {"populate_by_name": True, "extra": "forbid"}


class LayoutPatchAccepted(BaseModel):
    source_file_id: UUID = Field(serialization_alias="sourceFileId")
    version: int
    #: Where to read the recomputed profile once the job finishes.
    profile_url: str = Field(serialization_alias="profileUrl")
    layout_url: str = Field(serialization_alias="layoutUrl")

    model_config = {"populate_by_name": True}


class LayoutPatchEnvelope(BaseModel):
    data: LayoutPatchAccepted


def _field_from_document(entry: dict[str, Any]) -> LayoutFieldOut:
    # `entry` came out of jsonb, so every value is `Any`. Binding the sub-documents to
    # declared types here is what keeps the rest of this module honestly typed instead
    # of silently inferring `Unknown` through five constructor arguments.
    quirks: list[str] = list(entry.get("quirks") or [])
    evidence: list[dict[str, Any]] = list(entry.get("quirkEvidence") or [])
    return LayoutFieldOut(
        index=entry["index"],
        name=entry["name"],
        start=entry.get("start"),
        end=entry.get("end"),
        implied_scale=entry.get("impliedScale"),
        proposed_scale=entry.get("proposedScale"),
        scale_source=entry.get("scaleSource"),
        quirks=quirks,
        confidence=entry.get("confidence") or "high",
        note=entry.get("note"),
        quirk_evidence=[
            QuirkEvidenceOut(
                code=item["code"],
                matched=item["matched"],
                considered=item["considered"],
                match_fraction=item["matchFraction"],
                claimed=item["claimed"],
            )
            for item in evidence
        ],
    )


def _evidence_from_document(document: dict[str, Any] | None) -> InferenceEvidenceOut | None:
    if not document:
        return None
    blanks: list[str] = list(document["blankFractions"])
    regions: list[dict[str, Any]] = list(document.get("ambiguousRegions") or [])
    notes: list[str] = list(document.get("notes") or [])
    return InferenceEvidenceOut(
        records_sampled=document["recordsSampled"],
        record_width=document["recordWidth"],
        threshold=document["threshold"],
        blank_fractions=blanks,
        ambiguous_regions=[
            AmbiguousRegionOut(
                field_index=region["fieldIndex"],
                start=region["start"],
                end=region["end"],
                reason=region["reason"],
            )
            for region in regions
        ],
        confidence=document["confidence"],
        notes=notes,
    )


def layout_from_row(row: dict[str, Any]) -> LayoutOut:
    field_documents: list[dict[str, Any]] = list(row["fields"])
    return LayoutOut(
        source_file_id=row["source_file_id"],
        version=row["version"],
        kind=row["kind"],
        encoding=row["encoding"],
        delimiter=row["delimiter"],
        quote_char=row["quote_char"],
        has_header=row["has_header"],
        header_row_index=row["header_row_index"],
        footer_row_count=row["footer_row_count"],
        footer_declared_rows=row["footer_declared_rows"],
        footer_total=(None if row["footer_total"] is None else f"{row['footer_total']:f}"),
        record_width=row["record_width"],
        fields=[_field_from_document(entry) for entry in field_documents],
        inference_evidence=_evidence_from_document(row["inference_evidence"]),
        sample_records=list(row["sample_records"] or []),
        inferred=row["inferred"],
        edited_by_human=row["edited_by_human"],
    )
