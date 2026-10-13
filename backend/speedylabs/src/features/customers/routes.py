"""Customer list - what the upload form's picker reads."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.features.customers import repo
from src.features.customers.schemas import Customer, CustomerListEnvelope
from src.lib import db

router = APIRouter(prefix="/v1", tags=["customers"])


@router.get("/customers")
async def list_customers(
    session: Annotated[AsyncSession, Depends(db.sessions)],
) -> CustomerListEnvelope:
    rows = await repo.list_customers(session)
    return CustomerListEnvelope(data=[Customer(**row) for row in rows])
