"""Upload route: parse input, call the service, shape the output. No rules here (§2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.features.uploads import repo, service
from src.features.uploads.schemas import UploadAccepted, UploadAcceptedEnvelope, UploadKind
from src.lib import db
from src.lib.clientip import client_ip_hash
from src.lib.errors import TooLarge
from src.lib.storage import CHUNK_BYTES, LocalStorage

router = APIRouter(prefix="/v1", tags=["uploads"])


def _storage() -> LocalStorage:
    return LocalStorage(config.load().upload_dir)


async def _chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(CHUNK_BYTES):
        yield chunk


@router.post("/uploads", status_code=202)
async def create_upload(
    request: Request,
    file: Annotated[UploadFile, File()],
    customer_id: Annotated[UUID, Form(alias="customerId")],
    kind: Annotated[UploadKind, Form()],
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> UploadAcceptedEnvelope:
    """202, not 201: the file is accepted and queued, and the work has not happened yet.

    The response carries a `statusUrl` rather than a result, which is what §3 asks of a
    202 - the client polls that instead of holding a connection open for a 15-second
    parse.
    """
    settings = config.load()

    # An honest client declares its size, and refusing here costs nothing. A lying one
    # is caught mid-stream by the storage cap; this is the cheap path, not the guarantee.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.max_upload_bytes:
        megabytes = settings.max_upload_bytes // (1024 * 1024)
        raise TooLarge(
            f"That file is larger than the {megabytes} MB limit. Split the export and "
            "upload it in parts."
        )

    accepted = await service.accept_upload(
        session,
        _storage(),
        customer_id=customer_id,
        kind=kind,
        # Display only. Never used to build a path, so `../../etc/passwd` is just an
        # odd-looking label on a randomly-named file.
        original_filename=file.filename or "unnamed",
        chunks=_chunks(file),
        max_bytes=settings.max_upload_bytes,
        ip_hash=client_ip_hash(request, settings.trusted_proxy_hops, settings.ip_hash_salt),
        rate_limit_per_hour=settings.rate_limit_upload_per_hour,
    )

    from src.jobs.tasks import defer_ingest

    # The route does not branch on kind: `defer_ingest` picks the task, so queue knowledge stays in
    # the queue module and this edge keeps owning only the caps, the sniff and the rate limit.
    job_id = await defer_ingest(accepted.ingest_run_id, kind)
    await repo.attach_job_id(session, run_id=accepted.ingest_run_id, job_id=job_id)
    await session.commit()

    return UploadAcceptedEnvelope(
        data=UploadAccepted(
            ingest_run_id=accepted.ingest_run_id,
            source_file_id=accepted.source_file_id,
            status_url=f"/v1/ingest-runs/{accepted.ingest_run_id}",
        )
    )
