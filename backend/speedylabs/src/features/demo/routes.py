"""The demo routes. Parse, delegate, shape (BACKEND.md §2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.features.demo import service
from src.features.demo.schemas import (
    DemoStateEnvelope,
    DemoStateOut,
    ResetAcceptedEnvelope,
    ResetAcceptedOut,
)
from src.lib import db
from src.lib.clientip import client_ip_hash

router = APIRouter(prefix="/v1", tags=["demo"])


@router.get("/demo/state")
async def get_demo_state(
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> DemoStateEnvelope:
    """Whether this instance holds the seeded demo, and whether reset is available here."""
    data = await service.state(session)
    return DemoStateEnvelope(data=DemoStateOut(**data))


@router.post("/demo/reset", status_code=202)
async def reset_demo(
    request: Request,
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> ResetAcceptedEnvelope:
    """Clear this demo's data and rebuild it from the committed fixtures.

    The only destructive route in the system, so it carries four guards: off by default, rate
    limited per IP, single-flight, and scoped to the demo customer by slug.

    **No `Idempotency-Key`**, unlike every other side-effecting route here - the whole point is to
    do the work again. The lock and the rate limit are what make a double-click safe.

    **202 is now truthful.** It reports `status: "queued"` and nothing destructive has happened by
    the time the caller reads it - `service.start_reset` checks the guards and defers, and the
    clear and rebuild run on the worker. It previously answered 202 while its body said
    `"reseeded"`, which asked a caller to believe two contradictory things; the fix was to move the
    work, not to restate the claim. `statusUrl` points at `GET /v1/demo/state`, which reports
    `seeded: false` for an in-flight or interrupted reset and `true` once it has finished.
    """
    settings = config.load()
    data = await service.start_reset(
        session,
        ip_hash=client_ip_hash(request, settings.trusted_proxy_hops, settings.ip_hash_salt),
    )
    return ResetAcceptedEnvelope(data=ResetAcceptedOut(**data))
