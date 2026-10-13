"""Upload rules. No HTTP objects, no SQL (BACKEND.md §2).

The order of operations here is the security design, not an implementation detail:

1. **Rate limit first.** Counting before any work means a flood costs one upsert per
   request rather than a 64 MB disk write per request.
2. **Customer check second.** A 404 for an unknown customer must not leave a file on
   disk, so it happens before the bytes are stored.
3. **Store while capping.** The size cap is enforced mid-stream by `Storage`; a breach
   deletes the partial file before the error propagates.
4. **Sniff from the stored head.** The type check reads the first chunk we already have,
   so a hostile file is not re-read to be rejected.
5. **Insert rows, then enqueue.** The job is deferred only after the run row exists,
   because a worker that picks up a job for a row that is not there yet fails for a
   reason nobody can diagnose.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.uploads import repo
from src.lib import ratelimit
from src.lib.errors import NotFound, UnsupportedFileType
from src.lib.filetype import sniff_text
from src.lib.parsers.detect import detect_format
from src.lib.storage import Storage

#: One hour, matching `RATE_LIMIT_UPLOAD_PER_HOUR`.
UPLOAD_WINDOW_SECONDS = 3600


@dataclass(frozen=True)
class AcceptedUpload:
    ingest_run_id: UUID
    source_file_id: UUID


async def accept_upload(
    session: AsyncSession,
    storage: Storage,
    *,
    customer_id: UUID,
    kind: str,
    original_filename: str,
    chunks: AsyncIterator[bytes],
    max_bytes: int,
    ip_hash: str,
    rate_limit_per_hour: int,
    now: datetime | None = None,
) -> AcceptedUpload:
    """Store a file and queue its parse. Raises typed errors the handler maps to §3."""
    await ratelimit.enforce(
        session,
        key=f"upload:{ip_hash}",
        limit=rate_limit_per_hour,
        window_seconds=UPLOAD_WINDOW_SECONDS,
        now=now or datetime.now(UTC),
    )

    if not await repo.customer_exists(session, customer_id):
        # 404 rather than 422: §4 says missing and not-yours both return 404, and
        # confirming which customer ids exist is not information this route owes.
        raise NotFound("That customer does not exist. Pick one from the list.")

    stored = await storage.save(chunks, max_bytes=max_bytes)

    try:
        sniffed = sniff_text(stored.head)
    except UnsupportedFileType as exc:
        # The bytes are not a plain-text export. Remove them: a refused upload must not
        # leave a file behind for a later job to trip over.
        storage.delete(stored.stored_name)
        # `fields` lets the form mark the file input rather than showing a bare banner
        # (§3: "fields feeds form field errors directly").
        raise UnsupportedFileType(exc.message, fields={"file": exc.message}) from exc

    file_format = detect_format(stored.head)

    source_file_id = await repo.insert_source_file(
        session,
        customer_id=customer_id,
        kind=kind,
        original_filename=original_filename,
        stored_name=stored.stored_name,
        byte_size=stored.byte_size,
        sha256=stored.sha256,
        sniffed_type=sniffed,
        file_format=file_format,
        uploader_ip_hash=ip_hash,
    )
    run_id = await repo.insert_ingest_run(
        session, source_file_id=source_file_id, customer_id=customer_id
    )
    await session.commit()

    return AcceptedUpload(ingest_run_id=run_id, source_file_id=source_file_id)
