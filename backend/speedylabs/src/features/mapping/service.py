"""Mapping rules. No HTTP, no SQL.

`mapping` is the highest-risk input in the system: it decides how every value in the file
is interpreted, and from P6 a model writes it. So every part of an entry is checked
against something that already exists rather than taken on trust - the canonical field
against the committed schema, the source column against the file's own profile, the
transform against the closed registry. Nothing here trusts a name.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.canonical.model import (
    CANONICAL_FIELD_NAMES,
    CANONICAL_SCHEMA_VERSION,
    REQUIRED_FIELD_NAMES,
)
from src.features.layouts import repo as layouts_repo
from src.features.mapping import repo
from src.features.mapping.apply import FieldMapping, apply_mapping
from src.features.mapping.schemas import MappingPatchIn
from src.features.mapping.transforms import parse as parse_transform
from src.features.mapping.transforms import serialise as serialise_transform
from src.features.mapping.validators import FieldVerdict, require_complete, validate
from src.lib import canonjson
from src.lib.errors import (
    MappingLocked,
    NotFound,
    ProfileNotReady,
    UnknownCanonicalField,
    UnknownSourceColumn,
)
from src.lib.ids import uuid7
from src.lib.parsers.csv_reader import iter_csv_rows
from src.lib.parsers.fwf_reader import SliceField, iter_fixed_width_rows
from src.lib.storage import LocalStorage

#: Rows sampled by `validate`. The plan's configured number: enough that a share is
#: meaningful, few enough to answer inside a request.
VALIDATION_SAMPLE_ROWS = 5_000


async def _current_layout_version(session: AsyncSession, file_id: UUID) -> int:
    layout = await layouts_repo.current_layout(session, file_id)
    if layout is None:
        if not await repo.file_exists(session, file_id):
            raise NotFound("That file does not exist.")
        raise ProfileNotReady(
            "This file is still being read. Its columns appear here once parsing and "
            "profiling finish."
        )
    return int(layout["version"])


def _unmapped_required(mapping: dict[str, Any]) -> list[str]:
    return [name for name in REQUIRED_FIELD_NAMES if name not in mapping]


def _draft_payload(
    file_id: UUID,
    layout_version: int,
    draft: dict[str, Any] | None,
    validations: list[dict[str, Any]],
) -> dict[str, Any]:
    mapping: dict[str, Any] = dict(draft["mapping"]) if draft else {}
    # Stale means "these verdicts were computed from a different mapping". A draft with no
    # verdicts is not stale - there is nothing to be out of date - and the UI says "not
    # validated" for that, which is a different sentence.
    # `.get`, because a draft reaches here from three statements (select, merge, field removal)
    # and a shape that forgets the column should degrade to "not validated" rather than 500. The
    # three statements all return it; this is the belt to that braces.
    stored = draft.get("validated_sha256") if draft else None
    stored_hash = str(stored) if stored else None
    stale = bool(validations) and stored_hash != canonjson.sha256(mapping)
    return {
        "source_file_id": file_id,
        "layout_version": layout_version,
        "canonical_schema_version": (
            str(draft["canonical_schema_version"]) if draft else CANONICAL_SCHEMA_VERSION
        ),
        "fields": mapping,
        "unmapped_required": _unmapped_required(mapping),
        "validations": validations,
        "validations_stale": stale,
    }


async def get_mapping(session: AsyncSession, file_id: UUID) -> dict[str, Any]:
    """The draft and its last verdicts.

    An empty draft for a never-mapped file, not a 404: "nobody has mapped this yet" is a
    state the UI renders, and making the client distinguish it from "no such file" would
    push that judgement onto every caller.
    """
    layout_version = await _current_layout_version(session, file_id)
    draft = await repo.draft(session, file_id, layout_version)
    validations = await repo.validations(session, UUID(str(draft["id"]))) if draft else []
    return _draft_payload(file_id, layout_version, draft, validations)


async def patch_mapping(
    session: AsyncSession, file_id: UUID, body: MappingPatchIn
) -> dict[str, Any]:
    """Validate every entry, then merge them into the draft in one statement."""
    layout_version = await _current_layout_version(session, file_id)

    if await repo.has_materialized_run(session, file_id):
        raise MappingLocked(
            "This file's canonical lines have already been built, so its mapping cannot "
            "be changed. Upload the file again as a new run to map it differently."
        )

    known_columns = await repo.source_columns(session, file_id, layout_version)
    if not known_columns:
        raise ProfileNotReady(
            "This file's columns are not known yet. Wait for profiling to finish before mapping it."
        )

    patch: dict[str, Any] = {}
    for canonical_field, entry in body.fields.items():
        if canonical_field not in CANONICAL_FIELD_NAMES:
            raise UnknownCanonicalField(
                f"{canonical_field!r} is not a field in the canonical schema.",
                fields={canonical_field: "Not a canonical field."},
            )
        if entry.source_column not in known_columns:
            raise UnknownSourceColumn(
                f"{entry.source_column!r} is not a column in this file.",
                fields={canonical_field: f"{entry.source_column} is not in this file."},
            )
        # Parsed here, so an invalid transform is a 422 before anything is stored - and
        # re-serialised, so what is stored is the registry's own normal form rather than
        # whatever shape the client sent.
        transform = parse_transform(entry.transform)
        patch[canonical_field] = {
            "sourceColumn": entry.source_column,
            "transform": serialise_transform(transform),
            "origin": entry.origin,
            "confidence": entry.confidence,
            "reason": entry.reason,
        }

    draft = await repo.merge_draft(
        session,
        file_id,
        layout_version,
        draft_id=uuid7(),
        canonical_schema_version=CANONICAL_SCHEMA_VERSION,
        patch=patch,
    )
    await session.commit()
    validations = await repo.validations(session, UUID(str(draft["id"])))
    return _draft_payload(file_id, layout_version, draft, validations)


async def delete_mapping_fields(
    session: AsyncSession, file_id: UUID, names: list[str]
) -> dict[str, Any]:
    """Remove canonical fields from the draft - the UI's "ignore this column"."""
    layout_version = await _current_layout_version(session, file_id)
    unknown = [name for name in names if name not in CANONICAL_FIELD_NAMES]
    if unknown:
        raise UnknownCanonicalField(
            f"{', '.join(repr(name) for name in unknown)} is not a canonical field.",
            fields=dict.fromkeys(unknown, "Not a canonical field."),
        )
    draft = await repo.remove_fields(session, file_id, layout_version, names)
    await session.commit()
    validations = await repo.validations(session, UUID(str(draft["id"]))) if draft else []
    return _draft_payload(file_id, layout_version, draft, validations)


async def validate_mapping(
    session: AsyncSession, file_id: UUID, upload_dir: Path
) -> dict[str, Any]:
    """Apply the draft to a sample of real rows and store a verdict per field.

    Synchronous because it samples: 5,000 rows across sixteen fields is milliseconds, and
    a verdict a user waited for is worth more than a job they have to poll for.
    """
    layout_version = await _current_layout_version(session, file_id)
    draft = await repo.draft(session, file_id, layout_version)
    mapping: dict[str, Any] = dict(draft["mapping"]) if draft else {}

    # Raises 422 mapping_incomplete naming every required field still unmapped.
    require_complete(list(mapping))
    assert draft is not None  # require_complete would have raised on an empty draft

    parsed = {
        name: FieldMapping(
            source_column=str(entry["sourceColumn"]),
            transform=parse_transform(entry["transform"]),
        )
        for name, entry in mapping.items()
    }

    header, rows, first_row_number = await _read_rows(session, file_id, upload_dir)
    applied = list(
        apply_mapping(
            header,
            rows,
            parsed,
            limit=VALIDATION_SAMPLE_ROWS,
            first_row_number=first_row_number,
        )
    )
    verdicts = validate(applied, list(parsed))

    await repo.replace_validations(
        session,
        UUID(str(draft["id"])),
        [_validation_record(v) for v in verdicts],
        # The mapping **as sampled**, not as it may be a moment from now. P9 promotes a
        # validated draft to a spec that governs every later file, so "validated" has to mean
        # "these verdicts describe this mapping" rather than "somebody once pressed Validate".
        validated_sha256=canonjson.sha256(mapping),
    )
    await session.commit()

    return {
        "source_file_id": file_id,
        "layout_version": layout_version,
        "checked_rows": len(applied),
        "validations": await repo.validations(session, UUID(str(draft["id"]))),
    }


def _validation_record(verdict: FieldVerdict) -> dict[str, Any]:
    return {
        "id": uuid7(),
        "canonical_field": verdict.canonical_field,
        "verdict": verdict.verdict,
        "checked_rows": verdict.checked_rows,
        "failed_rows": verdict.failed_rows,
        "detail": verdict.detail,
        "example_row_numbers": list(verdict.example_row_numbers),
    }


async def _read_rows(
    session: AsyncSession, file_id: UUID, upload_dir: Path
) -> tuple[list[str], list[list[str]], int]:
    """Read the file the way its layout says to, and return rows plus the first line
    number. One function, so validation and the materialiser cannot disagree about which
    characters a column is."""
    layout = await layouts_repo.current_layout(session, file_id)
    if layout is None:
        raise ProfileNotReady("This file's layout is not known yet.")

    stored_name = await _stored_name(session, file_id)
    path = LocalStorage(upload_dir).open_path(stored_name)

    if layout["kind"] == "fixed_width":
        documents: list[dict[str, Any]] = list(layout["fields"])
        fields = [
            SliceField(name=str(entry["name"]), start=int(entry["start"]), end=int(entry["end"]))
            for entry in sorted(documents, key=lambda entry: int(entry["index"]))
        ]
        for names, row_iter in iter_fixed_width_rows(path, fields):
            # Materialised because the file handle closes when the generator is done.
            return names, list(row_iter), 1
        return [], [], 1

    for header, row_iter in iter_csv_rows(path):
        return header, list(row_iter), 2
    return [], [], 2


async def _stored_name(session: AsyncSession, file_id: UUID) -> str:
    from sqlalchemy import text

    row = (
        await session.execute(
            text("select stored_name from source_files where id = :id"), {"id": file_id}
        )
    ).first()
    if row is None:
        raise NotFound("That file does not exist.")
    return str(row[0])


def write_canonical_csv(rows: list[dict[str, Any]], destination: Path) -> None:
    """Write canonical rows in the committed CSV format. Used by P7; here because the
    column order belongs with the canonical model, not with a route."""
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        writer = csv.writer(handle, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(CANONICAL_FIELD_NAMES)
        for row in rows:
            writer.writerow(
                [
                    "" if row.get(name) is None else _csv_value(row[name])
                    for name in CANONICAL_FIELD_NAMES
                ]
            )


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def mapping_as_json(mapping: dict[str, Any]) -> str:
    """Canonical JSON for a mapping, so a stored spec diffs cleanly (P9).

    Delegates to `lib/canonjson`, which is the one home for the rules. Sorting keys alone was
    not enough: a `map_values` table is an ordered **list** of pairs, so two humans entering the
    same three value mappings in different orders produced two different hashes for the same
    instruction - and `spec_sha256` is what decides whether two specs are the same mapping.
    """
    return canonjson.dumps(mapping)
