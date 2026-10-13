"""Profile-read rules. No HTTP, no SQL."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.profiling import repo
from src.lib.errors import NotFound, ProfileNotReady

#: Statuses from which a profile exists or will shortly.
_PROFILED_OR_LATER = frozenset({"profiled", "mapped", "materialized", "ready", "blocked"})


async def get_profile(
    session: AsyncSession, file_id: UUID, layout_version: int | None = None
) -> dict[str, Any]:
    rows = await repo.file_with_profile(session, file_id, layout_version)
    if not rows:
        raise NotFound("That file does not exist.")

    # One statement answered three questions; the row shape says which case this is.
    # A file with no profile for this version still comes back as a single row whose
    # `column_index` is null, because the join is a LEFT join.
    columns = [row for row in rows if row["column_index"] is not None]
    if not columns:
        status = rows[0]["run_status"]
        if status in _PROFILED_OR_LATER:
            # Profiled, but nothing stored for this layout version - a real
            # inconsistency rather than a timing problem.
            raise NotFound("No profile exists for that layout version.")
        raise ProfileNotReady(
            "This file has not been profiled yet. It will appear here once parsing and "
            "profiling finish."
        )

    return {
        "source_file_id": file_id,
        # Report the version actually read, which with the default is whatever the
        # newest profile is - never the requested value, which may be None.
        "layout_version": columns[0]["layout_version"],
        # Every column saw the same rows, so the file's row count is any column's.
        "row_count": columns[0]["row_count"],
        "columns": columns,
    }
