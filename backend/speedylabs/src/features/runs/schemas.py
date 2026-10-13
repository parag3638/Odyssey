"""Run list and detail shapes. Keys are camelCase on the wire (BACKEND.md §3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

#: The one-way-door vocabulary: each value maps 1:1 to a UI badge variant, so renaming
#: one is a §3 breaking change rather than a refactor.
RunStatus = Literal[
    "queued",
    "parsing",
    "parsed",
    "profiled",
    "mapped",
    "materializing",
    "materialized",
    "drift_blocked",
    "ready",
    "blocked",
    "rejected",
    "failed",
    "imported",
]


#: Statuses after which nothing moves without a new human action.
#:
#: The **backend** owns this, and the API exposes the answer as `isSettled`, because the
#: frontend had the same set duplicated in two files and both copies were wrong: they
#: omitted `parsed`, which in Phase 2 is the only terminal state a successful run can
#: reach. Every finished run therefore polled forever. One source of truth, server-side,
#: and the client reads a boolean instead of re-deriving a vocabulary it does not own.
#:
#: Phase 3 removed `parsed`, exactly as predicted above: profiling now chains after
#: parse, so a parsed run is mid-flight rather than finished. One line here, no frontend
#: edit at all - which is the test of whether that boundary was in the right place.
#: `profiled` holds the place until P5 chains mapping after it.
#:
#: Phase 7 adds `materialized` rather than removing `profiled`: mapping is a human action,
#: so a profiled run is genuinely finished until somebody maps it, and a materialised run
#: is finished until P8 chains readiness after it. `materializing` is deliberately absent -
#: that is the one state in this phase a client should keep polling through.
#: Phase 8 removes `materialized` again: readiness is now chained from the materialise job,
#: so a materialised run is mid-flight on its way to `ready` or `blocked` - exactly the
#: situation `parsed` was in when Phase 3 chained profiling. The pattern is worth naming: a
#: status is terminal only while nothing automatically follows it, and the day something does,
#: this set is the single line that has to change.
#: Phase 9 adds `mapped`: a run whose mapping came from an approved spec has nothing automatic
#: left to do - materialising is a human's decision - so it is finished in the sense this set
#: means. Note the asymmetry with `materialized`, which P8 *removed* because readiness does chain
#: after it. The test is always the same one: does anything follow on its own?
#: Phase 10 adds `drift_blocked`: nothing follows it without a human accepting the change or
#: rejecting the file, which is the definition this set has had since Phase 3.
#: Phase 12 adds `imported`, for a legacy rebate export: it is read, matched against the customer's
#: programs, and done. Nothing chains after it - computing variances is a human action against an
#: *ERP* run that takes the legacy file as an input - so it passes the same test every value here
#: has had to pass.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "profiled",
        "mapped",
        "drift_blocked",
        "ready",
        "blocked",
        "rejected",
        "failed",
        "imported",
    }
)


def is_settled(status: str) -> bool:
    return status in TERMINAL_STATUSES


class StageTimestamps(BaseModel):
    """When each stage completed. Null means "not reached yet", not "failed"."""

    parsed_at: datetime | None = Field(default=None, serialization_alias="parsedAt")
    profiled_at: datetime | None = Field(default=None, serialization_alias="profiledAt")
    mapped_at: datetime | None = Field(default=None, serialization_alias="mappedAt")
    materialized_at: datetime | None = Field(default=None, serialization_alias="materializedAt")
    readiness_at: datetime | None = Field(default=None, serialization_alias="readinessAt")
    calculated_at: datetime | None = Field(default=None, serialization_alias="calculatedAt")
    first_variance_at: datetime | None = Field(default=None, serialization_alias="firstVarianceAt")

    model_config = {"populate_by_name": True}


class RunSummary(BaseModel):
    """One row in the runs list."""

    id: UUID
    #: The file this run processed. The profile, mapping and output routes are all keyed
    #: by file rather than run, because a re-profile under a new layout version belongs
    #: to the same file.
    source_file_id: UUID = Field(serialization_alias="sourceFileId")
    customer_id: UUID = Field(serialization_alias="customerId")
    customer_name: str = Field(serialization_alias="customerName")
    status: RunStatus
    kind: str
    original_filename: str = Field(serialization_alias="originalFilename")
    file_format: str = Field(serialization_alias="format")
    byte_size: int = Field(serialization_alias="byteSize")
    row_count: int | None = Field(default=None, serialization_alias="rowCount")
    error_code: str | None = Field(default=None, serialization_alias="errorCode")
    error_message: str | None = Field(default=None, serialization_alias="errorMessage")
    created_at: datetime = Field(serialization_alias="createdAt")
    #: True when nothing further will happen on its own - the client stops polling.
    is_settled: bool = Field(serialization_alias="isSettled")

    model_config = {"populate_by_name": True}


class SpecApplicationOut(BaseModel):
    """What this run did with its customer's approved spec.

    Published so "no LLM call was needed" is a fact a client can read, rather than something a
    reader infers from an absence. `skipCode` and `skipDetail` are how a user finds out *why* a
    spec they approved did not apply to the file they just uploaded - which is the question they
    will have, and the answer P10 turns into a gate.
    """

    applied: bool
    spec_id: UUID | None = Field(default=None, serialization_alias="specId")
    skip_code: str | None = Field(default=None, serialization_alias="skipCode")
    skip_detail: dict[str, Any] | None = Field(default=None, serialization_alias="skipDetail")

    model_config = {"populate_by_name": True}


class RunDetail(RunSummary):
    """A run plus its stage timeline."""

    stages: StageTimestamps
    elapsed_ms_to_first_variance: int | None = Field(
        default=None, serialization_alias="elapsedMsToFirstVariance"
    )
    spec: SpecApplicationOut


class RunListEnvelope(BaseModel):
    """List envelope: `{"data": [...], "nextCursor": "..." | null}` (§3)."""

    data: list[RunSummary]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")

    model_config = {"populate_by_name": True}


class RunDetailEnvelope(BaseModel):
    data: RunDetail
