"""Build the stored layout document. No HTTP, no SQL, no settings.

One shape for both formats. A CSV's delimiter, quote character and header row are a
layout exactly as much as a fixed-width record's field offsets are, and giving them one
table means P5's mapping asks "what are this file's fields?" without first asking what
kind of file it is. The difference shows up as nulls: `record_width` is null for CSV,
`delimiter` is null for fixed-width, and `start`/`end` are null on a CSV field because a
CSV column has an ordinal, not a character range.

The jsonb keys are camelCase, matching P4's API contract (`{"name","start","end",
"impliedScale","quirks"}`) rather than the snake_case of the columns around them. That is
deliberate: the PATCH body, the stored document and the GET response are then the same
shape, and a field list that travels from a form to the database and back without being
renamed twice has nowhere to lose a key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from src.lib.parsers.csv_reader import CsvDialect
from src.lib.parsers.fwf_infer import InferredLayout
from src.lib.parsers.fwf_reader import FixedWidthShape
from src.lib.parsers.quirks import FieldQuirks
from src.lib.parsers.trailer import parse_trailer

KIND_CSV = "csv"
KIND_FIXED_WIDTH = "fixed_width"


@dataclass(frozen=True)
class LayoutDocument:
    """Exactly the columns `file_layouts` needs, ready to insert."""

    kind: str
    fields: list[dict[str, Any]]
    inferred: bool
    encoding: str = "ascii"
    delimiter: str | None = None
    quote_char: str | None = None
    has_header: bool = True
    header_row_index: int | None = None
    footer_row_count: int = 0
    #: What the trailer *says* the file contains. Evidence, never an input: the readiness
    #: check compares it against the detail lines, and a file that disagrees with itself is
    #: the finding. Null for a CSV, and null for a trailer whose shape we do not recognise -
    #: which is reported as not-applicable rather than as a delta from a guessed number.
    footer_declared_rows: int | None = None
    footer_total: Decimal | None = None
    #: The approved spec whose boundaries became this layout's `fields`, when that is what
    #: happened. Null for an inferred layout and null for a CSV even on a run where a spec was
    #: applied - a CSV's columns come from its own header row, and crediting a document that did
    #: not produce them would be a lie in a foreign key.
    fields_from_spec_id: UUID | None = None
    record_width: int | None = None
    inference_evidence: dict[str, Any] | None = field(default=None)
    #: Verbatim records for the editor's ruler. The file's content, not evidence for any
    #: particular set of boundaries, so every version carries them - including one a
    #: human authored, which stores no inference evidence at all.
    sample_records: list[str] = field(default_factory=list[str])


def _decimal_str(value: Decimal) -> str:
    """Ratios cross the wire as decimal strings (§3), so they are stored that way too.

    Storing a jsonb number would hand the browser a float for a value the server
    computed exactly, which is the drift the decimal-string rule exists to stop.
    """
    return f"{value:f}"


def build_csv_document(header: list[str], dialect: CsvDialect) -> LayoutDocument:
    """A CSV layout: the header names in order, and how the rows were split."""
    return LayoutDocument(
        kind=KIND_CSV,
        delimiter=dialect.delimiter,
        quote_char=dialect.quote_char,
        has_header=True,
        header_row_index=0,
        record_width=None,
        inferred=True,
        inference_evidence=None,
        fields=[
            {
                "index": index,
                "name": name,
                "start": None,
                "end": None,
                "impliedScale": None,
                "quirks": [],
                "confidence": "high",
                "note": None,
            }
            for index, name in enumerate(header)
        ],
    )


def _has_trailer(layout: InferredLayout, shape: FixedWidthShape | None) -> bool:
    """The streamed answer when we have it, the sampled one otherwise."""
    return shape.has_trailer if shape is not None else layout.has_trailer


def build_fixed_width_document(
    layout: InferredLayout,
    quirks_by_index: dict[int, FieldQuirks],
    *,
    shape: FixedWidthShape | None = None,
) -> LayoutDocument:
    """A fixed-width layout: inferred boundaries plus the quirks detected per field.

    `shape` comes from `read_fixed_width_shape`, which streams the whole file. The trailer
    facts are taken from it rather than from `layout`, because inference only ever sees a
    5,000-record window and therefore reports no trailer on a file long enough to matter.
    It is optional so a caller with only an inference result still gets a usable document -
    with no footer facts, which is the truthful answer in that case.
    """
    ambiguous_indexes = {region.field_index for region in layout.evidence.ambiguous_regions}
    fields: list[dict[str, Any]] = []
    for inferred in layout.fields:
        quirks = quirks_by_index.get(inferred.index)
        # A field the inference believes is several fields has no settled convention to
        # claim: `implied_decimal` on a slice that is really a date, a filler and an
        # account number is a transform that is certainly wrong, and offering it invites
        # a human to accept it. The evidence is still carried, so nothing is hidden - it
        # simply does not become a claim until the field is split and re-detected.
        is_ambiguous = inferred.index in ambiguous_indexes
        fields.append(
            {
                "index": inferred.index,
                "name": inferred.name,
                "start": inferred.start,
                "end": inferred.end,
                "impliedScale": None
                if is_ambiguous
                else (quirks.implied_scale if quirks else None),
                "proposedScale": (
                    None if is_ambiguous else (quirks.proposed_scale if quirks else None)
                ),
                "scaleSource": None if is_ambiguous else (quirks.scale_source if quirks else None),
                "quirks": [] if is_ambiguous or not quirks else list(quirks.claimed),
                "confidence": inferred.confidence,
                "note": inferred.note,
                "quirkEvidence": [
                    {
                        "code": item.code,
                        "matched": item.matched,
                        "considered": item.considered,
                        "matchFraction": _decimal_str(item.match_fraction),
                        "claimed": item.claimed,
                    }
                    for item in (quirks.evidence if quirks else ())
                ],
            }
        )

    evidence = layout.evidence
    trailer = (
        None
        if shape is None or shape.trailer_record is None
        else parse_trailer(shape.trailer_record)
    )
    return LayoutDocument(
        kind=KIND_FIXED_WIDTH,
        delimiter=None,
        quote_char=None,
        # A fixed-width export carries no header row - the names are in a record layout
        # document the ERP team keeps, not in the file. `FIELD_01` placeholders stand in
        # until a human names them.
        has_header=False,
        header_row_index=None,
        footer_row_count=1 if _has_trailer(layout, shape) else 0,
        footer_declared_rows=None if trailer is None else trailer.declared_rows,
        footer_total=None if trailer is None else trailer.total,
        record_width=layout.record_width,
        inferred=True,
        fields=fields,
        inference_evidence={
            "recordsSampled": evidence.records_sampled,
            "recordWidth": evidence.record_width,
            "threshold": _decimal_str(evidence.threshold),
            "blankFractions": [_decimal_str(value) for value in evidence.blank_fractions],
            "ambiguousRegions": [
                {
                    "fieldIndex": region.field_index,
                    "start": region.start,
                    "end": region.end,
                    "reason": region.reason,
                }
                for region in evidence.ambiguous_regions
            ],
            "confidence": evidence.confidence,
            "notes": list(evidence.notes),
        },
        sample_records=list(evidence.sample_records),
    )
