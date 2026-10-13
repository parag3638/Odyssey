"""The mapping spec document: what gets remembered about how to read a customer's file.

A spec is **keyed to a customer, not a file**, and it carries the layout as well as the
mapping - for a fixed-width export the field boundaries are as much "how to read this
customer's file" as the canonical targets are.

The hard part is not what goes in. It is what stays out
-------------------------------------------------------
Everything here is a description of the *format*. Everything about one particular file is
evidence, and evidence copied forward becomes a lie about the next file:

* `footer_total` / `footer_declared_rows` are this file's trailer. Baking month 1's total into
  the spec would make P8's footer check - whose entire job is comparing the declared total with
  the lines - compare every later month against a constant.
* `inference_evidence` is a blank histogram over this file's bytes. Carrying it forward would
  attribute the spec's boundaries to a measurement that did not produce them, which is the
  mistake `layouts/service.patch_layout` already refuses to make when a human edits boundaries.
* `sample_records` are verbatim invoice lines. A spec is downloadable; it must not carry
  customer data.
* Per-field `confidence`, `reason`, `origin`, `quirkEvidence` and `proposedScale` are statements
  about how *this* mapping was arrived at. A re-proposal with reworded prose would otherwise show
  up as a spec change when nothing about the mapping changed - exactly the diff noise canonical
  JSON exists to prevent.

`footerRowCount` **is** in the document, as the expectation. The layout written for a new file
still takes the value measured on that file: a month whose export happens to carry no trailer
would otherwise lose its last detail line, silently, with money on it. Comparing expectation
with measurement is P10's job, not this document's.
"""

from __future__ import annotations

from typing import Any, Final, cast

from src.features.canonical.model import CANONICAL_SCHEMA_VERSION
from src.features.layouts.document import KIND_CSV, KIND_FIXED_WIDTH
from src.features.specs.model import validate_document

#: The only keys a mapping entry keeps. `origin`, `confidence` and `reason` are deliberately
#: dropped - see the module docstring.
MAPPING_ENTRY_KEYS: Final = ("sourceColumn", "transform")

#: The only keys a fixed-width field keeps.
FIELD_KEYS: Final = ("index", "name", "start", "end", "impliedScale", "quirks")

#: Keys that must never appear anywhere in a document, at any depth. The 9.1 test walks a
#: built document for each of these rather than eyeballing one fixture.
EXCLUDED_KEYS: Final = (
    "footerTotal",
    "footer_total",
    "footerDeclaredRows",
    "footer_declared_rows",
    "inferenceEvidence",
    "inference_evidence",
    "sampleRecords",
    "sample_records",
    "quirkEvidence",
    "confidence",
    "reason",
    "origin",
    "proposedScale",
    "scaleSource",
    "note",
    "sourceFileId",
    "layoutVersion",
    "rowCount",
)


class SpecIncompleteError(ValueError):
    """A layout or mapping that cannot describe how to read anything.

    A `ValueError`, not an `AppError`: this is a programming mistake at the boundary of the
    feature, and the route refuses long before it on a rule the caller can act on.
    """


def build_document(layout: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    """The spec document for this layout and mapping. Pure: no session, no file, no clock."""
    kind = str(layout["kind"])
    if not mapping:
        raise SpecIncompleteError("a spec needs a mapping")

    if kind == KIND_FIXED_WIDTH:
        layout_block = _fixed_width_layout(layout)
    elif kind == KIND_CSV:
        layout_block = _csv_layout(layout)
    else:
        raise SpecIncompleteError(f"{kind!r} is not a layout kind a spec can describe")

    document = {
        "canonicalSchemaVersion": CANONICAL_SCHEMA_VERSION,
        "kind": kind,
        "layout": layout_block,
        "mapping": {
            name: {key: entry[key] for key in MAPPING_ENTRY_KEYS}
            for name, entry in sorted(mapping.items())
        },
    }
    # Validated on the way **out** as well as on the way in. A spec is hashed and then governs every
    # later file, so the moment to catch a malformed one is before it is stored - not when a future
    # run tries to read it.
    validate_document(document)
    return document


def _fixed_width_layout(layout: dict[str, Any]) -> dict[str, Any]:
    fields: list[dict[str, Any]] = list(layout["fields"] or [])
    if not fields:
        raise SpecIncompleteError("a fixed-width spec needs field boundaries")
    return {
        "encoding": str(layout["encoding"]),
        "recordWidth": int(layout["record_width"]),
        "footerRowCount": int(layout["footer_row_count"] or 0),
        # Ordered by index, because the order *is* the record layout. Sorting here rather than
        # trusting the stored order: the document's bytes decide a hash.
        "fields": [
            {key: field.get(key) for key in FIELD_KEYS}
            for field in sorted(fields, key=lambda field: int(field["index"]))
        ],
    }


def _csv_layout(layout: dict[str, Any]) -> dict[str, Any]:
    fields: list[dict[str, Any]] = list(layout["fields"] or [])
    if not fields:
        raise SpecIncompleteError("a CSV spec needs the file's header")
    return {
        "encoding": str(layout["encoding"]),
        "delimiter": layout["delimiter"],
        "quoteChar": layout["quote_char"],
        "hasHeader": bool(layout["has_header"]),
        "headerRowIndex": layout["header_row_index"],
        "footerRowCount": int(layout["footer_row_count"] or 0),
        # The **full ordered header**, not only the mapped columns: the layout describes the
        # format, and a column that appears, disappears or is renamed is precisely what P10
        # compares. Keeping only the mapped ones would make a renamed unmapped column invisible.
        "columns": [str(field["name"]) for field in sorted(fields, key=lambda f: int(f["index"]))],
    }


def excluded_keys_present(document: Any, keys: tuple[str, ...] = EXCLUDED_KEYS) -> list[str]:
    """Every excluded key found anywhere in the document. Empty is the only acceptable answer.

    Exists so the test walks the structure rather than comparing one golden fixture: a golden
    file proves the shape of the example, and this proves the rule.
    """
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in cast(dict[str, Any], node).items():
                if key in keys:
                    found.append(f"{path}.{key}" if path else key)
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            # `cast` for the same reason `lib/canonjson` needs one: `isinstance` on an `Any`
            # narrows to an unknown element type, and this walker's input is genuinely dynamic.
            for index, item in enumerate(cast(list[Any], node)):
                walk(item, f"{path}[{index}]")

    walk(document, "")
    return found
