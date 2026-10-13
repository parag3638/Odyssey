"""Mapping SQL. Literal statements only (see the runs repo for why)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SELECT_DRAFT = text(
    """
    select id, source_file_id, layout_version, canonical_schema_version, mapping,
           validated_sha256
    from mapping_drafts
    where source_file_id = :source_file_id and layout_version = :layout_version
    """
)

# The whole point of this statement: `mapping || :patch` merges in the database, in one
# round trip, so two callers patching different canonical fields both survive. The
# read-modify-write this replaces would have had the second overwrite the first with a
# document that never contained the first's edit.
#
# `cast(:patch as jsonb)` rather than `:patch::jsonb`: SQLAlchemy's `text()` reads `::`
# as the start of another bind parameter.
_UPSERT_DRAFT = text(
    """
    insert into mapping_drafts (
        id, source_file_id, layout_version, canonical_schema_version, mapping
    )
    values (
        :id, :source_file_id, :layout_version, :canonical_schema_version,
        cast(:patch as jsonb)
    )
    on conflict (source_file_id, layout_version) do update
        set mapping = mapping_drafts.mapping || cast(:patch as jsonb),
            canonical_schema_version = excluded.canonical_schema_version,
            updated_at = now()
    returning id, source_file_id, layout_version, canonical_schema_version, mapping,
              validated_sha256
    """
)

_DELETE_FIELDS = text(
    """
    update mapping_drafts
    set mapping = mapping - cast(:names as text[]), updated_at = now()
    where source_file_id = :source_file_id and layout_version = :layout_version
    returning id, source_file_id, layout_version, canonical_schema_version, mapping,
              validated_sha256
    """
)

_SELECT_VALIDATIONS = text(
    """
    select canonical_field, verdict, checked_rows, failed_rows, detail,
           example_row_numbers
    from mapping_validations
    where mapping_draft_id = :draft_id
    order by canonical_field
    """
)

_UPSERT_VALIDATION = text(
    """
    insert into mapping_validations (
        id, mapping_draft_id, canonical_field, verdict, checked_rows, failed_rows,
        detail, example_row_numbers
    )
    values (
        :id, :draft_id, :canonical_field, :verdict, :checked_rows, :failed_rows,
        cast(:detail as jsonb), :example_row_numbers
    )
    on conflict (mapping_draft_id, canonical_field) do update
        set verdict = excluded.verdict,
            checked_rows = excluded.checked_rows,
            failed_rows = excluded.failed_rows,
            detail = excluded.detail,
            example_row_numbers = excluded.example_row_numbers,
            created_at = now()
    """
)

# A re-validation can leave behind verdicts for fields that are no longer mapped. They
# are deleted rather than kept, because a stale verdict beside a live one is worse than
# no verdict: a reader cannot tell which run it belongs to.
_DELETE_STALE_VALIDATIONS = text(
    """
    delete from mapping_validations
    where mapping_draft_id = :draft_id
      and not (canonical_field = any(:keep))
    """
)

_SOURCE_COLUMNS = text(
    """
    select column_name
    from column_profiles
    where source_file_id = :source_file_id and layout_version = :layout_version
    order by column_index
    """
)

_MATERIALIZED_RUN = text(
    """
    select id from ingest_runs
    where source_file_id = :source_file_id and materialized_at is not null
    limit 1
    """
)

_FILE_EXISTS = text("select id from source_files where id = :id")


async def draft(session: AsyncSession, file_id: UUID, layout_version: int) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                _SELECT_DRAFT,
                {"source_file_id": file_id, "layout_version": layout_version},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def merge_draft(
    session: AsyncSession,
    file_id: UUID,
    layout_version: int,
    *,
    draft_id: UUID,
    canonical_schema_version: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge `patch` into the draft's mapping, creating the draft if it is the first."""
    row = (
        (
            await session.execute(
                _UPSERT_DRAFT,
                {
                    "id": draft_id,
                    "source_file_id": file_id,
                    "layout_version": layout_version,
                    "canonical_schema_version": canonical_schema_version,
                    "patch": json.dumps(patch),
                },
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise RuntimeError(f"mapping upsert returned no row for file {file_id}")
    return dict(row)


async def remove_fields(
    session: AsyncSession, file_id: UUID, layout_version: int, names: list[str]
) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                _DELETE_FIELDS,
                {
                    "source_file_id": file_id,
                    "layout_version": layout_version,
                    "names": names,
                },
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


_SET_VALIDATED_SHA256 = text(
    """
    update mapping_drafts set validated_sha256 = :validated_sha256, updated_at = now()
    where id = :draft_id
    """
)


async def validations(session: AsyncSession, draft_id: UUID) -> list[dict[str, Any]]:
    rows = await session.execute(_SELECT_VALIDATIONS, {"draft_id": draft_id})
    return [dict(row) for row in rows.mappings()]


async def replace_validations(
    session: AsyncSession,
    draft_id: UUID,
    records: list[dict[str, Any]],
    *,
    validated_sha256: str,
) -> None:
    """Write this run's verdicts, drop any left over, and record what they describe.

    The hash goes in **here**, not in a second call from the service: the verdicts and the
    statement of which mapping produced them have to commit together or a crash between the two
    leaves verdicts that claim to describe a mapping nobody checked.
    """
    for record in records:
        await session.execute(
            _UPSERT_VALIDATION,
            {
                "draft_id": draft_id,
                **record,
                "detail": json.dumps(record["detail"]),
            },
        )
    await session.execute(
        _SET_VALIDATED_SHA256, {"draft_id": draft_id, "validated_sha256": validated_sha256}
    )
    await session.execute(
        _DELETE_STALE_VALIDATIONS,
        {"draft_id": draft_id, "keep": [record["canonical_field"] for record in records]},
    )


async def source_columns(session: AsyncSession, file_id: UUID, layout_version: int) -> list[str]:
    """The column names this file's profile actually contains, in column order."""
    rows = await session.execute(
        _SOURCE_COLUMNS, {"source_file_id": file_id, "layout_version": layout_version}
    )
    return [str(row[0]) for row in rows]


async def has_materialized_run(session: AsyncSession, file_id: UUID) -> bool:
    row = (await session.execute(_MATERIALIZED_RUN, {"source_file_id": file_id})).first()
    return row is not None


async def file_exists(session: AsyncSession, file_id: UUID) -> bool:
    row = (await session.execute(_FILE_EXISTS, {"id": file_id})).first()
    return row is not None


# ---------------------------------------------------------------------------------------------
# Proposal cache (moved out of propose.py when ambiguities joined it - BACKEND.md §2)
# ---------------------------------------------------------------------------------------------

_SELECT_CACHED_PROPOSAL = text(
    """
    select proposal, dropped_fields, ambiguities, model, cost_usd
    from mapping_proposals
    where cache_key = :cache_key
    """
)

_INSERT_PROPOSAL = text(
    """
    insert into mapping_proposals (
        id, source_file_id, cache_key, canonical_schema_version, origin, model, effort,
        input_tokens, output_tokens, cached_input_tokens, cache_write_tokens, cost_usd,
        latency_ms, finish_reason, proposal, dropped_fields, ambiguities
    ) values (
        :id, :source_file_id, :cache_key, :canonical_schema_version, :origin, :model,
        :effort, :input_tokens, :output_tokens, :cached_input_tokens, :cache_write_tokens,
        :cost_usd, :latency_ms, :finish_reason, cast(:proposal as jsonb),
        cast(:dropped_fields as jsonb), cast(:ambiguities as jsonb)
    )
    on conflict (cache_key) do nothing
    """
)


async def cached_proposal(session: AsyncSession, cache_key: str) -> dict[str, Any] | None:
    """The stored answer for a cache key: proposal, dropped fields, the model's own ambiguities."""
    row = (
        (await session.execute(_SELECT_CACHED_PROPOSAL, {"cache_key": cache_key}))
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


async def insert_proposal(
    session: AsyncSession,
    *,
    proposal: dict[str, Any],
    dropped_fields: list[dict[str, Any]],
    ambiguities: list[str],
    **facts: Any,
) -> None:
    """Store one answered proposal. `facts` are the call's bookkeeping columns as named."""
    await session.execute(
        _INSERT_PROPOSAL,
        {
            **facts,
            "proposal": json.dumps(proposal),
            "dropped_fields": json.dumps(dropped_fields),
            "ambiguities": json.dumps(ambiguities),
        },
    )
