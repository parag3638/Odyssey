"""The layout route."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.features.layouts import service
from src.features.layouts.schemas import (
    LayoutEnvelope,
    LayoutPatchAccepted,
    LayoutPatchEnvelope,
    LayoutPatchIn,
    layout_from_row,
)
from src.lib import db

router = APIRouter(prefix="/v1", tags=["layouts"])


@router.get("/source-files/{file_id}/layout")
async def get_layout(
    file_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
    version: Annotated[int | None, Query(ge=1)] = None,
) -> LayoutEnvelope:
    """How this file is cut into fields. Defaults to the current (highest) version."""
    row = await service.get_layout(session, file_id, version)
    return LayoutEnvelope(data=layout_from_row(row))


@router.patch("/source-files/{file_id}/layout", status_code=status.HTTP_202_ACCEPTED)
async def patch_layout(
    file_id: UUID,
    body: LayoutPatchIn,
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> LayoutPatchEnvelope:
    """Replace the field list, as a new version, and queue a re-profile.

    202 rather than 200: the version exists when this returns, but the statistics that
    describe it do not yet - they are a job. The response says where to read them.
    """
    data = await service.patch_layout(session, file_id, body)
    return LayoutPatchEnvelope(
        data=LayoutPatchAccepted(
            source_file_id=data["source_file_id"],
            version=data["version"],
            profile_url=data["profile_url"],
            layout_url=data["layout_url"],
        )
    )
