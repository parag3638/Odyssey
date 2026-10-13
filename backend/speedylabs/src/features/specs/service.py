"""Approving a mapping, and the preconditions that make an approval mean something.

A spec governs how every later file from this customer is read, so approval is the most
consequential write in the product. Four preconditions, each of which has a reason a user can
act on:

1. **Complete** - every required canonical field is mapped. Otherwise the spec cannot produce a
   canonical line at all.
2. **Validated** - verdicts exist. "Somebody looked" is the minimum.
3. **Fresh** - the verdicts describe *this* mapping, by hash. Without this, validate → edit →
   approve carries a mapping nobody checked into the document that governs the next twelve
   months, which is the hole migration 0015 was added to close.
4. **No `fail` verdict**, unless forced with a note on the record. `warn` does not block: it
   means the *data* has gaps, where `fail` means the *mapping* is wrong (decision of
   2026-10-06). Remembering a wrong mapping is worse than remembering none.

The mapping lock deliberately does **not** reach here. Approving after month 1 has been
materialised is the normal case - that is when a human knows the mapping was right.

Content-addressed: a draft that canonicalises to the live spec's `spec_sha256` returns `200`
with the existing spec and writes nothing. Approving twice is not an error, it is a no-op, and a
new version for an unchanged mapping would make the version history meaningless.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.features.canonical.model import CANONICAL_SCHEMA_VERSION
from src.features.layouts import repo as layouts_repo
from src.features.mapping import repo as mapping_repo
from src.features.mapping.validators import require_complete
from src.features.specs import repo
from src.features.specs.document import build_document
from src.lib import canonjson, idempotency, logger, ratelimit
from src.lib import cursor as cursor_lib
from src.lib.errors import (
    NotFound,
    ProfileNotReady,
    SpecConflict,
    SpecNoteRequired,
    ValidationFailing,
    ValidationMissing,
    ValidationStale,
)
from src.lib.ids import uuid7
from src.lib.timeutil import now

log = logger.get_logger(__name__)

#: The route name this feature records idempotency keys under.
IDEMPOTENCY_ROUTE = "specs.approve"


async def approve(
    session: AsyncSession,
    *,
    source_file_id: UUID,
    note: str | None,
    force: bool,
    client_ip_hash: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Promote a validated draft to the approved spec for its customer and format."""
    # Counted **before** the draft is read, so a caller hammering the route cannot make us do
    # the work of building and hashing a document to be told no.
    settings = config.load()
    await ratelimit.enforce(
        session,
        key=f"spec_approve:{client_ip_hash}",
        limit=settings.rate_limit_spec_approval_per_hour,
        window_seconds=3_600,
        what="spec approvals",
    )

    file_row = await repo.file_owner(session, source_file_id)
    if file_row is None:
        # 404 and nothing else: a caller guessing file ids learns nothing about which exist.
        raise NotFound("That file does not exist.")

    customer_id = UUID(str(file_row["customer_id"]))
    layout = await layouts_repo.current_layout(session, source_file_id)
    if layout is None:
        raise ProfileNotReady(
            "This file is still being read. Its mapping can be approved once profiling finishes."
        )

    kind = str(layout["kind"])
    if idempotency_key is not None:
        claim = await idempotency.claim(
            session,
            route=IDEMPOTENCY_ROUTE,
            key=idempotency_key,
            fingerprint=str(source_file_id),
        )
        await session.commit()
        if claim.replay_ref is not None:
            existing = await repo.by_id(session, UUID(claim.replay_ref))
            if existing is not None:
                return {"spec": existing, "created": False}

    draft = await mapping_repo.draft(session, source_file_id, int(layout["version"]))
    mapping: dict[str, Any] = dict(draft["mapping"]) if draft else {}
    require_complete(list(mapping))
    assert draft is not None  # require_complete raises on an empty draft

    validations = await mapping_repo.validations(session, UUID(str(draft["id"])))
    if not validations:
        raise ValidationMissing(
            "Validate this mapping before approving it. The spec will govern every later file "
            "from this customer."
        )
    stored_hash = draft["validated_sha256"]
    if not stored_hash or str(stored_hash) != canonjson.sha256(mapping):
        raise ValidationStale(
            "This mapping has changed since it was validated. Validate it again, then approve."
        )

    failing = [str(row["canonical_field"]) for row in validations if str(row["verdict"]) == "fail"]
    if failing and not force:
        raise ValidationFailing(
            "Some fields did not validate. Fix them, or approve anyway with a note saying why.",
            fields=dict.fromkeys(failing, "This field's verdict is a failure."),
        )
    if force and not (note or "").strip():
        raise SpecNoteRequired(
            "Approving over a failing check needs a note saying why.",
            fields={"note": "Required when forcing an approval."},
        )

    document = build_document(layout, mapping)
    spec_json = canonjson.dumps(document)
    spec_sha256 = canonjson.sha256(document)

    live = await repo.approved(session, customer_id, kind)
    if live is not None and str(live["spec_sha256"]) == spec_sha256:
        # The same mapping already approved. Not an error and not a new version: a version
        # history where consecutive entries can be identical says nothing.
        log.info("spec_unchanged", customer_id=str(customer_id), kind=kind, version=live["version"])
        if idempotency_key is not None:
            await idempotency.fulfil(
                session,
                route=IDEMPOTENCY_ROUTE,
                key=idempotency_key,
                response_ref=str(live["id"]),
            )
            await session.commit()
        return {"spec": live, "created": False}

    spec_id = uuid7()
    try:
        inserted = await repo.insert_approved(
            session,
            spec_id=spec_id,
            customer_id=customer_id,
            kind=kind,
            canonical_schema_version=CANONICAL_SCHEMA_VERSION,
            spec_json=spec_json,
            spec_sha256=spec_sha256,
            approved_from_source_file_id=source_file_id,
            # The file **and** the version. `approved_from_source_file_id` alone does not identify
            # the column profiles the spec's columns belong to: a human can edit that file's
            # boundaries afterwards, and version 2's profiles are keyed to different columns - so
            # P10 comparing against "the newest layout of the baseline file" would silently compare
            # the wrong columns.
            approved_from_layout_version=int(layout["version"]),
            approved_note=(note or "").strip() or None,
            approved_forced=bool(failing and force),
            approved_from_ip_hash=client_ip_hash,
            approved_at=now(),
        )
        if idempotency_key is not None:
            await idempotency.fulfil(
                session,
                route=IDEMPOTENCY_ROUTE,
                key=idempotency_key,
                response_ref=str(spec_id),
            )
        await session.commit()
    except Exception as error:
        await session.rollback()
        if _is_unique_violation(error):
            # Another approval for this customer and format committed first. The caller's
            # request was valid and is simply no longer the newest.
            raise SpecConflict(
                "Another approval for this customer landed first. Reload the spec list and "
                "approve again if you still want to."
            ) from error
        raise

    log.info(
        "spec_approved",
        customer_id=str(customer_id),
        kind=kind,
        version=inserted["version"],
        forced=bool(failing and force),
    )
    stored = await repo.by_id(session, spec_id)
    if stored is None:
        raise RuntimeError(f"spec {spec_id} vanished after insert")
    return {"spec": stored, "created": True}


def _is_unique_violation(error: Exception) -> bool:
    """Whether this is Postgres' unique-violation, by code rather than by message text."""
    original = getattr(error, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(error, "sqlstate", None)
    return sqlstate == "23505"


#: §3: cursor pagination, default 25, max 100.
DEFAULT_LIMIT = 25
MAX_LIMIT = 100


async def list_specs(
    session: AsyncSession,
    customer_id: UUID,
    *,
    kind: str | None,
    limit: int | None,
    cursor: str | None,
) -> dict[str, Any]:
    """This customer's specs, newest first."""
    if not await _customer_exists(session, customer_id):
        raise NotFound("That customer does not exist.")
    size = DEFAULT_LIMIT if limit is None else max(1, min(limit, MAX_LIMIT))
    decoded = _decode_cursor(cursor) if cursor else None
    rows = await repo.list_for_customer(
        session,
        customer_id,
        kind=kind,
        limit=size,
        cursor_approved_at=decoded[0] if decoded else None,
        cursor_id=decoded[1] if decoded else None,
    )
    next_cursor = (
        cursor_lib.encode(rows[-1]["approved_at"].isoformat(), str(rows[-1]["id"]))
        if len(rows) == size
        else None
    )
    return {"rows": rows, "next_cursor": next_cursor}


async def get_spec(session: AsyncSession, spec_id: UUID) -> dict[str, Any]:
    spec = await repo.by_id(session, spec_id)
    if spec is None:
        # The same body for "not a spec" as for "not a uuid we know": a caller enumerating ids
        # learns nothing either way.
        raise NotFound("That mapping spec does not exist.")
    return spec


def _decode_cursor(value: str) -> tuple[datetime, UUID]:
    return cursor_lib.decode(
        value,
        parse=lambda parts: (datetime.fromisoformat(parts[0]), UUID(parts[1])),
        message="That page link is not valid. Go back to the first page of specs.",
    )


async def _customer_exists(session: AsyncSession, customer_id: UUID) -> bool:
    return await repo.customer_exists(session, customer_id)
