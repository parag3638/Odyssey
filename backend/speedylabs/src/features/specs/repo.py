"""Mapping-spec SQL. The only place it lives (BACKEND.md §2).

The insert computes its own version number, the way `layouts/repo` does, so two writers cannot
both read "max is 1" and both write 2. The status flip and the insert run in one transaction, so
no moment exists with two approved specs for one customer and format - and the partial unique
index refuses it even if one did.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_APPROVED = text(
    """
    select id, customer_id, kind, version, status, canonical_schema_version, spec,
           spec_sha256, approved_from_source_file_id, approved_from_layout_version,
           approved_note, approved_forced,
           approved_at, created_at
    from mapping_specs
    where customer_id = :customer_id and kind = :kind and status = 'approved'
    """
)

_BY_ID = text(
    """
    select s.id, s.customer_id, s.kind, s.version, s.status, s.canonical_schema_version,
           s.spec, s.spec_sha256, s.approved_from_source_file_id,
           s.approved_from_layout_version, s.approved_note,
           s.approved_forced, s.approved_at, s.created_at, c.name as customer_name
    from mapping_specs s
    join customers c on c.id = s.customer_id
    where s.id = :id
    """
)

#: Keyset pagination on `(approved_at desc, id desc)`: newest first, with the id as the
#: tie-break, because two approvals can share a timestamp and a page boundary landing inside a
#: tie would drop whatever follows.
_LIST = text(
    """
    select id, customer_id, kind, version, status, canonical_schema_version, spec_sha256,
           approved_from_source_file_id, approved_note, approved_forced, approved_at
    from mapping_specs
    where customer_id = :customer_id
      and (cast(:kind as text) is null or kind = cast(:kind as text))
      and (
        cast(:cursor_approved_at as timestamptz) is null
        or (approved_at, id)
             < (cast(:cursor_approved_at as timestamptz), cast(:cursor_id as uuid))
      )
    order by approved_at desc, id desc
    limit :limit
    """
)

#: Locks this customer and kind's approved row for the length of the transaction, so the flip
#: and the insert cannot interleave with another approver's.
_LOCK_APPROVED = text(
    """
    select id from mapping_specs
    where customer_id = :customer_id and kind = :kind and status = 'approved'
    for update
    """
)

_SUPERSEDE = text(
    """
    update mapping_specs set status = 'superseded', updated_at = now()
    where customer_id = :customer_id and kind = :kind and status = 'approved'
    """
)

_INSERT = text(
    """
    insert into mapping_specs (
        id, customer_id, kind, version, status, canonical_schema_version, spec, spec_sha256,
        approved_from_source_file_id, approved_from_layout_version, approved_note,
        approved_forced, approved_from_ip_hash, approved_at
    )
    select :id, :customer_id, :kind, coalesce(max(version), 0) + 1, 'approved',
           :canonical_schema_version, cast(:spec as jsonb), :spec_sha256,
           :approved_from_source_file_id, :approved_from_layout_version, :approved_note,
           :approved_forced, :approved_from_ip_hash, :approved_at
    from mapping_specs
    where customer_id = :customer_id and kind = :kind
    returning id, version, approved_at
    """
)


#: A file's owning customer. A question about a file rather than about a spec, but it is SQL and
#: §2 allows SQL in exactly one kind of place - so it lives in the repo of the feature that asks
#: it, not inline in a service.
_FILE_OWNER = text("select id, customer_id from source_files where id = :id")


_CUSTOMER_EXISTS = text("select id from customers where id = :id")


async def customer_exists(session: AsyncSession, customer_id: UUID) -> bool:
    return (await session.execute(_CUSTOMER_EXISTS, {"id": customer_id})).first() is not None


async def file_owner(session: AsyncSession, source_file_id: UUID) -> dict[str, Any] | None:
    row = (await session.execute(_FILE_OWNER, {"id": source_file_id})).mappings().first()
    return dict(row) if row else None


async def approved(session: AsyncSession, customer_id: UUID, kind: str) -> dict[str, Any] | None:
    row = (
        (await session.execute(_APPROVED, {"customer_id": customer_id, "kind": kind}))
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def by_id(session: AsyncSession, spec_id: UUID) -> dict[str, Any] | None:
    row = (await session.execute(_BY_ID, {"id": spec_id})).mappings().first()
    return dict(row) if row else None


async def list_for_customer(
    session: AsyncSession,
    customer_id: UUID,
    *,
    kind: str | None,
    limit: int,
    cursor_approved_at: datetime | None,
    cursor_id: UUID | None,
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            _LIST,
            {
                "customer_id": customer_id,
                "kind": kind,
                "limit": limit,
                "cursor_approved_at": cursor_approved_at,
                "cursor_id": cursor_id,
            },
        )
    ).mappings()
    return [dict(row) for row in rows]


async def insert_approved(
    session: AsyncSession,
    *,
    spec_id: UUID,
    customer_id: UUID,
    kind: str,
    canonical_schema_version: str,
    spec_json: str,
    spec_sha256: str,
    approved_from_source_file_id: UUID,
    approved_from_layout_version: int,
    approved_note: str | None,
    approved_forced: bool,
    approved_from_ip_hash: str,
    approved_at: datetime,
) -> dict[str, Any]:
    """Supersede the live spec and insert the new one. One transaction, lock first."""
    await session.execute(_LOCK_APPROVED, {"customer_id": customer_id, "kind": kind})
    await session.execute(_SUPERSEDE, {"customer_id": customer_id, "kind": kind})
    row = (
        await session.execute(
            _INSERT,
            {
                "id": spec_id,
                "customer_id": customer_id,
                "kind": kind,
                "canonical_schema_version": canonical_schema_version,
                "spec": spec_json,
                "spec_sha256": spec_sha256,
                "approved_from_source_file_id": approved_from_source_file_id,
                "approved_from_layout_version": approved_from_layout_version,
                "approved_note": approved_note,
                "approved_forced": approved_forced,
                "approved_from_ip_hash": approved_from_ip_hash,
                "approved_at": approved_at,
            },
        )
    ).mappings()
    inserted = row.first()
    if inserted is None:
        raise RuntimeError(f"spec insert returned no row for customer {customer_id}")
    return dict(inserted)
