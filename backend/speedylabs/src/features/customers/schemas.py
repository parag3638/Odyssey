"""Customer shapes."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class Customer(BaseModel):
    id: UUID
    name: str
    slug: str
    run_count: int = Field(serialization_alias="runCount")

    model_config = {"populate_by_name": True}


class CustomerListEnvelope(BaseModel):
    data: list[Customer]
