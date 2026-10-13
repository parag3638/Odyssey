"""Profiling SQL. Literal statements only (see the runs repo for why)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# One statement for the whole request: does the file exist, what is its run's status, and
# what are its column profiles. Three questions, one round trip.
#
# It was two statements - an existence/status check, then the data - which is no N+1 (the
# count does not grow with the column count) but is not what 3.3's criterion says:
# "exactly one DB query". The tester's query-counting test held the strict reading rather
# than agreeing with the code, which is what surfaced this.
#
# The joins are both LEFT, so the shape of the result distinguishes all three outcomes
# without a second query:
#   * no rows                  -> no such file
#   * one row, column_index null -> the file exists, nothing profiled for this version
#   * n rows                   -> the profile
_SELECT_FILE_WITH_PROFILE = text(
    """
    select f.id            as file_id,
           latest_run.status as run_status,
           p.layout_version,
           p.column_index, p.column_name, p.type_guess, p.row_count, p.null_count,
           p.null_pct, p.distinct_count, p.distinct_capped, p.min_text, p.max_text,
           p.date_format, p.sample_values, p.patterns,
           p.value_counts, p.numeric_count, p.numeric_sum
    from source_files f
    left join lateral (
        select r.status
        from ingest_runs r
        where r.source_file_id = f.id
        order by r.created_at desc
        limit 1
    ) latest_run on true
    left join column_profiles p
        on p.source_file_id = f.id
       and p.layout_version = coalesce(
               cast(:layout_version as integer),
               (select max(layout_version) from column_profiles
                 where source_file_id = f.id),
               1)
    where f.id = :source_file_id
    order by p.column_index
    """
)


async def file_with_profile(
    session: AsyncSession, file_id: UUID, layout_version: int | None = None
) -> list[dict[str, Any]]:
    """Every row for one file and layout version. Empty list means no such file.

    `layout_version=None` means the newest profiled version, resolved inside the same
    statement by a scalar subquery rather than a prior lookup - still one query, which
    3.3's criterion requires. That default matters after a boundary edit: the caller
    asking "what does this file look like?" wants the layout in force, not version 1.
    """
    rows = await session.execute(
        _SELECT_FILE_WITH_PROFILE,
        {"source_file_id": file_id, "layout_version": layout_version},
    )
    return [dict(row) for row in rows.mappings()]
