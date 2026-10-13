"""The only place uploads' SQL lives (BACKEND.md §2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.ids import uuid7


async def customer_exists(session: AsyncSession, customer_id: UUID) -> bool:
    row = await session.execute(text("select 1 from customers where id = :id"), {"id": customer_id})
    return row.first() is not None


async def insert_source_file(
    session: AsyncSession,
    *,
    customer_id: UUID,
    kind: str,
    original_filename: str,
    stored_name: str,
    byte_size: int,
    sha256: str,
    sniffed_type: str,
    file_format: str,
    uploader_ip_hash: str,
) -> UUID:
    file_id = uuid7()
    await session.execute(
        text(
            """
            insert into source_files (
                id, customer_id, kind, original_filename, stored_name, byte_size,
                sha256, sniffed_type, format, uploader_ip_hash
            ) values (
                :id, :customer_id, :kind, :original_filename, :stored_name, :byte_size,
                :sha256, :sniffed_type, :format, :uploader_ip_hash
            )
            """
        ),
        {
            "id": file_id,
            "customer_id": customer_id,
            "kind": kind,
            "original_filename": original_filename,
            "stored_name": stored_name,
            "byte_size": byte_size,
            "sha256": sha256,
            "sniffed_type": sniffed_type,
            "format": file_format,
            "uploader_ip_hash": uploader_ip_hash,
        },
    )
    return file_id


async def insert_ingest_run(
    session: AsyncSession, *, source_file_id: UUID, customer_id: UUID
) -> UUID:
    run_id = uuid7()
    await session.execute(
        text(
            """
            insert into ingest_runs (id, source_file_id, customer_id, status)
            values (:id, :source_file_id, :customer_id, 'queued')
            """
        ),
        {"id": run_id, "source_file_id": source_file_id, "customer_id": customer_id},
    )
    return run_id


async def attach_job_id(session: AsyncSession, *, run_id: UUID, job_id: int) -> None:
    await session.execute(
        text("update ingest_runs set job_id = :job_id, updated_at = now() where id = :id"),
        {"id": run_id, "job_id": job_id},
    )


async def run_row(session: AsyncSession, run_id: UUID) -> dict[str, Any] | None:
    row = (
        (await session.execute(text("select * from ingest_runs where id = :id"), {"id": run_id}))
        .mappings()
        .first()
    )
    return dict(row) if row else None
