"""The profile route."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.features.profiling import service
from src.features.profiling.schemas import ProfileEnvelope, ProfileOut, column_from_row
from src.lib import db

router = APIRouter(prefix="/v1", tags=["profiling"])


@router.get("/source-files/{file_id}/profile")
async def get_profile(
    file_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
    layout_version: Annotated[int | None, Query(alias="layoutVersion", ge=1)] = None,
) -> ProfileEnvelope:
    """Every column's profile for one file, in column order."""
    data = await service.get_profile(session, file_id, layout_version)
    return ProfileEnvelope(
        data=ProfileOut(
            source_file_id=data["source_file_id"],
            layout_version=data["layout_version"],
            row_count=data["row_count"],
            columns=[column_from_row(row) for row in data["columns"]],
        )
    )
