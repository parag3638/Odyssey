"""Does this spec fit this file? A pure decision, with a reason when the answer is no.

**Why a spec is applied at parse time, before profiling.** For a fixed-width export the spec's
field boundaries have to *become* layout version 1, not be compared with an inferred one:
`fwf_infer` names fields positionally (`FIELD_01`, `FIELD_02`, …) and cuts on a 99% blank
histogram, so a month whose data happens to leave two adjacent fields blank in every record merges
them and renumbers everything after. A mapping that says `FIELD_07` would then point at different
characters - silently, with money on them. mvp.md's "profile, then try the spec" ordering cannot
work for the format that most needs a spec.

The consequence is a narrowing of a documented invariant: `_write_layout_v1`'s "version 1 is what
inference said the first time" now holds for files with **no** spec.
`file_layouts.fields_from_spec_id` records which kind of version 1 it was.

**A spec is an optimisation, never a precondition.** Every answer here is either "apply it" or
"here is why not", and the caller carries on parsing either way. A file that cannot use a spec is
an ordinary file that needs mapping, which is where the product started.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from src.features.canonical.model import CANONICAL_SCHEMA_VERSION
from src.features.layouts.document import KIND_CSV, KIND_FIXED_WIDTH
from src.features.specs.model import validate_document

#: Why a spec that existed was not applied. Mirrors migration 0017's check constraint.
SCHEMA_VERSION_CHANGED: Final = "schema_version_changed"
MISSING_SOURCE_COLUMNS: Final = "missing_source_columns"
RECORD_WIDTH_CHANGED: Final = "record_width_changed"
SPEC_UNREADABLE: Final = "spec_unreadable"

#: Source columns named in a skip detail. Bounded: a spec pointed at a wholly different file would
#: otherwise put every one of its columns in a JSONB column and in a response.
MAX_NAMED_COLUMNS: Final = 20


@dataclass(frozen=True)
class Fit:
    """`ok` applies the spec; otherwise `code` and `detail` say why not."""

    ok: bool
    code: str | None = None
    detail: dict[str, Any] | None = None


def check_fit(
    spec_document: dict[str, Any],
    *,
    kind: str,
    header: list[str] | None,
    record_width: int | None,
) -> Fit:
    """Whether this spec can read a file of this shape.

    Three facts are compared, and nothing else: the canonical schema version (a spec written
    against an older canonical shape may name fields that no longer exist), the source columns the
    mapping cites, and - for fixed-width - the record width. Anything subtler is drift, which is
    P10's job to report rather than this function's to guess at.
    """
    try:
        # Validated before anything is read out of it. An unknown key means this document was
        # written by a version of the service that knew something this one does not - reading it as
        # if the key were absent is the dangerous outcome, not the safe one.
        validate_document(spec_document)
        document_kind = str(spec_document["kind"])
        schema_version = str(spec_document["canonicalSchemaVersion"])
        layout = dict(spec_document["layout"])
        mapping = dict(spec_document["mapping"])
    except (KeyError, TypeError, ValueError):
        # A stored document we cannot read. Named without echoing any of it: the message reaches a
        # user, and a half-parsed document is exactly the kind of thing that carries file paths.
        return Fit(
            ok=False,
            code=SPEC_UNREADABLE,
            detail={"reason": "this spec's stored document could not be read"},
        )

    if document_kind != kind:
        return Fit(
            ok=False,
            code=SPEC_UNREADABLE,
            detail={"reason": f"the spec describes a {document_kind} file, not a {kind} one"},
        )

    if schema_version != CANONICAL_SCHEMA_VERSION:
        return Fit(
            ok=False,
            code=SCHEMA_VERSION_CHANGED,
            detail={"specVersion": schema_version, "currentVersion": CANONICAL_SCHEMA_VERSION},
        )

    if kind == KIND_FIXED_WIDTH:
        expected = layout.get("recordWidth")
        if expected is not None and record_width is not None and int(expected) != record_width:
            # The one check that matters most for this format: every boundary in the spec is an
            # offset into a record of a particular width, so a different width makes all of them
            # wrong at once.
            return Fit(
                ok=False,
                code=RECORD_WIDTH_CHANGED,
                detail={"specRecordWidth": int(expected), "fileRecordWidth": record_width},
            )
        # A fixed-width file has no header to compare: the spec's own field names *become* the
        # columns, so the mapping cannot cite a column the layout does not define.
        defined = {str(field["name"]) for field in layout.get("fields", [])}
        missing = sorted(
            {
                str(entry["sourceColumn"])
                for entry in mapping.values()
                if str(entry["sourceColumn"]) not in defined
            }
        )
        if missing:
            return Fit(
                ok=False,
                code=SPEC_UNREADABLE,
                detail={
                    "reason": "this spec maps columns its own layout does not define",
                    "missingColumns": missing[:MAX_NAMED_COLUMNS],
                },
            )
        return Fit(ok=True)

    if kind == KIND_CSV:
        present = set(header or [])
        missing = sorted(
            {
                str(entry["sourceColumn"])
                for entry in mapping.values()
                if str(entry["sourceColumn"]) not in present
            }
        )
        if missing:
            # The drifted month: a renamed column means the spec cannot read this file, and
            # saying which one is the difference between a user fixing it and guessing.
            return Fit(
                ok=False,
                code=MISSING_SOURCE_COLUMNS,
                detail={
                    "missingColumns": missing[:MAX_NAMED_COLUMNS],
                    "missingColumnCount": len(missing),
                },
            )
        return Fit(ok=True)

    return Fit(
        ok=False,
        code=SPEC_UNREADABLE,
        detail={"reason": f"{kind} is not a format a spec can be applied to"},
    )


def layout_fields_from_spec(spec_document: dict[str, Any]) -> list[dict[str, Any]]:
    """The spec's fields, in the shape `file_layouts.fields` stores.

    `scaleSource` becomes `"spec"` and `quirkEvidence` is empty on purpose: the boundaries came
    from an approved document, not from a measurement over this file's bytes, and claiming
    evidence we do not have is the thing the spec document itself is careful never to do.
    """
    fields: list[dict[str, Any]] = list(spec_document["layout"]["fields"])
    return [
        {
            "index": int(field["index"]),
            "name": str(field["name"]),
            "start": int(field["start"]),
            "end": int(field["end"]),
            "impliedScale": field.get("impliedScale"),
            "proposedScale": field.get("impliedScale"),
            "scaleSource": "spec",
            "quirks": list(field.get("quirks") or []),
            "confidence": None,
            "note": None,
            "quirkEvidence": [],
        }
        for field in sorted(fields, key=lambda item: int(item["index"]))
    ]
