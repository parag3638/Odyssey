"""The mapping-spec routes. Parse, delegate, shape (BACKEND.md §2)."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.features.specs import service
from src.features.specs.schemas import (
    SpecApproveIn,
    SpecEnvelope,
    SpecListEnvelope,
    SpecOut,
    SpecSummaryOut,
)
from src.lib import canonjson, db, filenames, idempotency
from src.lib.clientip import client_ip_hash

router = APIRouter(prefix="/v1", tags=["mapping-specs"])


@router.post("/mapping-specs", status_code=201)
async def approve_spec(
    body: SpecApproveIn,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> SpecEnvelope:
    """Promote a validated draft to this customer's approved spec for its format.

    `201` for a new version, `200` when the draft canonicalises to the spec already approved -
    approving twice is a no-op rather than an error, and a new version for an unchanged mapping
    would make the version history say nothing. The status code is the honest signal, so it is
    set on the response rather than being inferred by the client from the version number.
    """
    settings = config.load()
    result = await service.approve(
        session,
        source_file_id=body.source_file_id,
        note=body.note,
        force=body.force,
        client_ip_hash=client_ip_hash(request, settings.trusted_proxy_hops, settings.ip_hash_salt),
        idempotency_key=idempotency.clean_key(request.headers.get("Idempotency-Key")),
    )
    if not result["created"]:
        response.status_code = 200
    return SpecEnvelope(data=_spec(result["spec"]))


@router.get("/customers/{customer_id}/mapping-specs")
async def list_specs(
    customer_id: UUID,
    session: Annotated[AsyncSession, Depends(db.sessions)],
    kind: Annotated[Literal["csv", "fixed_width"] | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> SpecListEnvelope:
    """This customer's specs, newest first. At most one `approved` per format."""
    page = await service.list_specs(session, customer_id, kind=kind, limit=limit, cursor=cursor)
    return SpecListEnvelope(
        data=[_summary(row) for row in page["rows"]], next_cursor=page["next_cursor"]
    )


@router.get("/mapping-specs/{spec_id}")
async def get_spec(
    spec_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> SpecEnvelope:
    """One spec, with its document."""
    return SpecEnvelope(data=_spec(await service.get_spec(session, spec_id)))


@router.get("/mapping-specs/{spec_id}/download")
async def download_spec(
    spec_id: UUID, session: Annotated[AsyncSession, Depends(db.sessions)]
) -> Response:
    """The bare document, in its canonical form.

    No `data` envelope: this file is meant to be diffed against another copy of itself, and an
    envelope would put a wrapper line at the top of every diff. The bytes are produced by the
    same canonicaliser that hashed the document, so two downloads of one spec are byte-identical
    and `diff` reports nothing.
    """
    spec = await service.get_spec(session, spec_id)
    name = filenames.download_name(
        "mapping-spec-",
        f"{spec['customer_name']} v{spec['version']}",
        spec["kind"],
        "json",
    )
    return Response(
        content=canonjson.dumps(spec["spec"]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


def _summary(row: dict[str, Any]) -> SpecSummaryOut:
    return SpecSummaryOut(
        id=row["id"],
        customer_id=row["customer_id"],
        kind=row["kind"],
        version=int(row["version"]),
        status=row["status"],
        canonical_schema_version=str(row["canonical_schema_version"]),
        spec_sha256=str(row["spec_sha256"]),
        approved_from_source_file_id=row["approved_from_source_file_id"],
        approved_note=row["approved_note"],
        approved_forced=bool(row["approved_forced"]),
        approved_at=row["approved_at"],
    )


def _spec(row: dict[str, Any]) -> SpecOut:
    return SpecOut(**_summary(row).model_dump(), spec=dict(row["spec"]))
