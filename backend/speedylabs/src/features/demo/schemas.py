"""Demo state shapes. camelCase on the wire (BACKEND.md §3)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DemoStateOut(BaseModel):
    seed_version: str = Field(serialization_alias="seedVersion")
    seeded_at: datetime | None = Field(default=None, serialization_alias="seededAt")
    #: False for all three ways of not being seeded - no row, an interrupted row, or no customer -
    #: because the caller's next action is the same in each.
    seeded: bool
    reset_enabled: bool = Field(serialization_alias="resetEnabled")
    customer_name: str | None = Field(default=None, serialization_alias="customerName")
    run_count: int = Field(serialization_alias="runCount")

    model_config = {"populate_by_name": True}


class DemoStateEnvelope(BaseModel):
    data: DemoStateOut


class ResetAcceptedOut(BaseModel):
    status: str
    seed_version: str = Field(serialization_alias="seedVersion")
    status_url: str = Field(serialization_alias="statusUrl")

    model_config = {"populate_by_name": True}


class ResetAcceptedEnvelope(BaseModel):
    data: ResetAcceptedOut
