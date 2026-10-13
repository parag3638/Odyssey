"""The mapping routes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.features.mapping import propose as service_propose
from src.features.mapping import service
from src.features.mapping.schemas import (
    MappingEntryOut,
    MappingEnvelope,
    MappingOut,
    MappingPatchIn,
    ValidationEnvelope,
    ValidationOut,
    ValidationSummary,
)
from src.lib import db, idempotency
from src.lib.clientip import client_ip_hash

router = APIRouter(prefix="/v1", tags=["mapping"])


def _upload_dir() -> Path:
    return Path(config.load().upload_dir)


def _entries(fields: dict[str, dict[str, Any]]) -> dict[str, MappingEntryOut]:
    return {
        name: MappingEntryOut(
            source_column=str(entry["sourceColumn"]),
            transform=dict(entry["transform"]),
            origin=str(entry.get("origin") or "manual"),
            confidence=None if entry.get("confidence") is None else str(entry["confidence"]),
            reason=None if entry.get("reason") is None else str(entry["reason"]),
        )
        for name, entry in fields.items()
    }


def _validations(records: list[dict[str, Any]]) -> list[ValidationOut]:
    return [
        ValidationOut(
            canonical_field=str(record["canonical_field"]),
            verdict=str(record["verdict"]),
            checked_rows=int(record["checked_rows"]),
            failed_rows=int(record["failed_rows"]),
            detail=dict(record["detail"]),
            example_row_numbers=list(record["example_row_numbers"]),
        )
        for record in records
    ]


def _envelope(data: dict[str, Any]) -> MappingEnvelope:
    return MappingEnvelope(
        data=MappingOut(
            source_file_id=data["source_file_id"],
            layout_version=int(data["layout_version"]),
            canonical_schema_version=str(data["canonical_schema_version"]),
            fields=_entries(data["fields"]),
            unmapped_required=list(data["unmapped_required"]),
            validations=_validations(data["validations"]),
            validations_stale=bool(data.get("validations_stale", False)),
        )
    )


@router.get("/source-files/{file_id}/mapping")
async def get_mapping(
    file_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> MappingEnvelope:
    """The current mapping draft and the verdicts last computed for it."""
    return _envelope(await service.get_mapping(session, file_id))


@router.patch("/source-files/{file_id}/mapping")
async def patch_mapping(
    file_id: UUID,
    body: MappingPatchIn,
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> MappingEnvelope:
    """Update the canonical fields named in the body, leaving the rest untouched."""
    return _envelope(await service.patch_mapping(session, file_id, body))


@router.delete("/source-files/{file_id}/mapping")
async def delete_mapping_fields(
    file_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
    fields: Annotated[list[str], Query(min_length=1)],
) -> MappingEnvelope:
    """Remove canonical fields from the draft - the UI's "ignore this column"."""
    return _envelope(await service.delete_mapping_fields(session, file_id, fields))


class ProposalEntryOut(BaseModel):
    canonical_field: str = Field(serialization_alias="canonicalField")
    source_column: str = Field(serialization_alias="sourceColumn")
    transform: dict[str, Any]
    #: A decimal string, 0-1. Never a float, and never formatted in the frontend.
    confidence: str | None = None
    reason: str | None = None

    model_config = {"populate_by_name": True}


class DroppedFieldOut(BaseModel):
    canonical_field: str = Field(serialization_alias="canonicalField")
    source_column: str = Field(serialization_alias="sourceColumn")
    reason: str

    model_config = {"populate_by_name": True}


class SpendOut(BaseModel):
    call_cost_usd: str = Field(serialization_alias="callCostUsd")
    today_usd: str = Field(serialization_alias="todayUsd")
    held_usd: str = Field(serialization_alias="heldUsd")
    cap_usd: str = Field(serialization_alias="capUsd")

    model_config = {"populate_by_name": True}


class ProposalOut(BaseModel):
    source_file_id: UUID = Field(serialization_alias="sourceFileId")
    origin: str
    proposals: list[ProposalEntryOut]
    dropped_fields: list[DroppedFieldOut] = Field(serialization_alias="droppedFields")
    #: Fields the model could not separate. Shown, because a named ambiguity is one a
    #: human can resolve and a hidden one is not.
    ambiguities: list[str]
    spend: SpendOut

    model_config = {"populate_by_name": True}


class ProposalEnvelope(BaseModel):
    data: ProposalOut


@router.post("/source-files/{file_id}/mapping/propose")
async def propose_mapping(
    file_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> ProposalEnvelope:
    """Ask the model for a mapping, or serve the one we already paid for.

    Blocks for up to the configured LLM timeout rather than returning 202 and a job. A
    named deviation from BACKEND.md §6's 500 ms write budget: this is a user-initiated
    action the user is actively watching behind a spinner, with a hard timeout, and the
    alternative is more machinery for something already on screen.
    """
    settings = config.load()
    data = await service_propose.propose(
        session,
        file_id,
        upload_dir=_upload_dir(),
        client_ip_hash=client_ip_hash(request, settings.trusted_proxy_hops, settings.ip_hash_salt),
        # §3: a repeat with the same key returns the same proposal rather than paying for a
        # second call. The header was accepted and documented long before it was read - the
        # P6 tester found that, and this line is the half that was missing.
        idempotency_key=idempotency.clean_key(request.headers.get("Idempotency-Key")),
    )
    entries: dict[str, Any] = data["entries"]
    return ProposalEnvelope(
        data=ProposalOut(
            source_file_id=file_id,
            origin=str(data["origin"]),
            proposals=[
                ProposalEntryOut(
                    canonical_field=name,
                    source_column=str(entry["sourceColumn"]),
                    transform=dict(entry["transform"]),
                    confidence=(
                        None if entry.get("confidence") is None else str(entry["confidence"])
                    ),
                    reason=None if entry.get("reason") is None else str(entry["reason"]),
                )
                for name, entry in entries.items()
            ],
            dropped_fields=[
                DroppedFieldOut(
                    canonical_field=str(item["canonicalField"]),
                    source_column=str(item["sourceColumn"]),
                    reason=str(item["reason"]),
                )
                for item in data["dropped"]
            ],
            ambiguities=list(data["ambiguities"]),
            spend=SpendOut(
                call_cost_usd=data["spend"]["callCostUsd"],
                today_usd=data["spend"]["todayUsd"],
                held_usd=data["spend"]["heldUsd"],
                cap_usd=data["spend"]["capUsd"],
            ),
        )
    )


@router.post("/source-files/{file_id}/mapping/validate")
async def validate_mapping(
    file_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> ValidationEnvelope:
    """Check the draft against a sample of the real file. Synchronous: it samples."""
    data = await service.validate_mapping(session, file_id, _upload_dir())
    return ValidationEnvelope(
        data=ValidationSummary(
            source_file_id=data["source_file_id"],
            layout_version=int(data["layout_version"]),
            checked_rows=int(data["checked_rows"]),
            validations=_validations(data["validations"]),
        )
    )
