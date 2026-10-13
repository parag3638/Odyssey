"""Layout-read rules. No HTTP, no SQL."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.layouts import repo
from src.features.layouts.document import KIND_FIXED_WIDTH
from src.features.layouts.schemas import LayoutPatchIn
from src.features.layouts.validate import ProposedField, validate_fields
from src.jobs.tasks import defer_reprofile
from src.lib.errors import InvalidLayout, LayoutLocked, NotFound, ProfileNotReady
from src.lib.ids import uuid7


async def get_layout(
    session: AsyncSession, file_id: UUID, version: int | None = None
) -> dict[str, Any]:
    """The current layout, or a named version.

    A missing file and a file belonging to someone else both 404, so neither reveals
    which it was.
    """
    if version is None:
        row = await repo.current_layout(session, file_id)
    else:
        row = await repo.layout_version(session, file_id, version)

    if row is not None:
        return row

    if not await repo.file_exists(session, file_id):
        raise NotFound("That file does not exist.")
    if version is not None:
        raise NotFound("That layout version does not exist.")
    # The file is there but has no layout: parsing has not reached the point of writing
    # one. 409, not 404 - the caller is early, not wrong.
    raise ProfileNotReady(
        "This file's layout has not been worked out yet. It will appear here once parsing finishes."
    )


async def patch_layout(session: AsyncSession, file_id: UUID, body: LayoutPatchIn) -> dict[str, Any]:
    """Validate a proposed field list, store it as a new version, queue a re-profile.

    Additive: version 1 is never touched, so the profile computed under it stays
    readable and the edit is reversible. The new version's statistics arrive when the
    job finishes, which is why this returns 202 and a URL rather than a profile.
    """
    current = await repo.current_layout(session, file_id)
    if current is None:
        if not await repo.file_exists(session, file_id):
            raise NotFound("That file does not exist.")
        raise ProfileNotReady(
            "This file's layout has not been worked out yet. Wait for parsing to "
            "finish before editing the fields."
        )

    if current["kind"] != KIND_FIXED_WIDTH:
        raise InvalidLayout(
            "Only a fixed-width file has character boundaries to edit. A CSV's columns "
            "come from its header row.",
            fields={"fields": "This file is not fixed-width."},
        )

    # Checked before validation so a locked run is told it is locked, rather than being
    # walked through fixing a layout it will not be allowed to save.
    if await repo.has_materialized_run(session, file_id):
        raise LayoutLocked(
            "This file already has canonical lines built from its current field "
            "boundaries, so they cannot be changed. Upload the file again as a new run "
            "to work with different boundaries."
        )

    record_width = current["record_width"]
    if record_width is None:
        raise InvalidLayout(
            "This file's record width is not known, so field boundaries cannot be "
            "checked against it.",
            fields={"fields": "The record width is missing."},
        )

    footer_row_count = (
        current["footer_row_count"] if body.footer_row_count is None else body.footer_row_count
    )
    proposed = [
        ProposedField(
            name=field.name,
            start=field.start,
            end=field.end,
            implied_scale=field.implied_scale,
            quirks=tuple(field.quirks),
        )
        for field in body.fields
    ]
    validate_fields(proposed, record_width=record_width, footer_row_count=footer_row_count)

    # The human's edit is the claim. A scale they set is `confirmed`, not `proposed`:
    # they are the authority the detector deliberately waits for.
    documents = [
        {
            "index": index,
            "name": field.name,
            "start": field.start,
            "end": field.end,
            "impliedScale": field.implied_scale,
            "proposedScale": None,
            "scaleSource": "confirmed" if field.implied_scale is not None else None,
            "quirks": list(field.quirks),
            "confidence": "high",
            "note": None,
            "quirkEvidence": [],
        }
        for index, field in enumerate(proposed)
    ]

    version = await repo.insert_next_version(
        session,
        file_id,
        {
            "id": uuid7(),
            "kind": current["kind"],
            "encoding": current["encoding"],
            "delimiter": current["delimiter"],
            "quote_char": current["quote_char"],
            "has_header": current["has_header"],
            "header_row_index": current["header_row_index"],
            "footer_row_count": footer_row_count,
            "record_width": record_width,
            "fields": json.dumps(documents),
            # Evidence belongs to inference. A human-authored layout has none, and
            # copying version 1's histogram forward would attribute their boundaries to
            # a measurement that did not produce them.
            "inference_evidence": None,
            # The records, by contrast, are the file's own content and are unchanged by
            # anybody's edit - so they carry forward. Dropping them with the evidence is
            # what left version 2's editor with no ruler and no preview.
            "sample_records": json.dumps(list(current["sample_records"] or [])),
        },
    )
    await session.commit()

    await defer_reprofile(file_id, version)

    return {
        "source_file_id": file_id,
        "version": version,
        "profile_url": f"/v1/source-files/{file_id}/profile?layoutVersion={version}",
        "layout_url": f"/v1/source-files/{file_id}/layout?version={version}",
    }
