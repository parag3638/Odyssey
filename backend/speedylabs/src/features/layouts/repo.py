"""Layout SQL. Literal statements only (see the runs repo for why)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The current layout is the highest version. `order by version desc limit 1` rides the
# `ix_file_layouts_file_version_desc` index, so this is an index read and not a sort.
_SELECT_CURRENT = text(
    """
    select source_file_id, version, kind, encoding, delimiter, quote_char, has_header,
           header_row_index, footer_row_count, footer_declared_rows, footer_total,
           record_width, fields, inference_evidence,
           sample_records, inferred, edited_by_human
    from file_layouts
    where source_file_id = :source_file_id
    order by version desc
    limit 1
    """
)

_SELECT_VERSION = text(
    """
    select source_file_id, version, kind, encoding, delimiter, quote_char, has_header,
           header_row_index, footer_row_count, footer_declared_rows, footer_total,
           record_width, fields, inference_evidence,
           sample_records, inferred, edited_by_human
    from file_layouts
    where source_file_id = :source_file_id and version = :version
    """
)

#: Which of the locked runs is actually materialised. Runs after the lock above, in the
#: same transaction, so the answer cannot change before the insert.
_MATERIALIZED_RUN_MATERIALIZED = text(
    """
    select id
    from ingest_runs
    where source_file_id = :source_file_id and materialized_at is not null
    limit 1
    """
)

_FILE_EXISTS = text("select id from source_files where id = :id")

# Is this file's run already materialised? If canonical lines exist, the boundaries they
# were built from are load-bearing and an edit is refused (409). Reads `materialized_at`
# rather than the status text, because a run that has moved on to `ready` has still
# materialised.
# `for update` takes a row lock on this file's runs for the rest of the transaction, so
# a concurrent writer of `materialized_at` has to wait behind the layout insert instead of
# slipping between the check and the write. The tester demonstrated that gap at the repo
# level: check (false) -> another connection materialises and commits -> the insert still
# succeeds, writing a layout version over materialised rows. Nothing writes
# `materialized_at` yet, so it is dormant - which is exactly when it is cheap to close.
_MATERIALIZED_RUN = text(
    """
    select id
    from ingest_runs
    where source_file_id = :source_file_id
    for update
    """
)

# The new version number is computed inside the insert, so two concurrent edits cannot
# both read "max is 1" and then both write version 2 - and if they race anyway, the
# unique constraint on (source_file_id, version) is what refuses the second.
#
# `cast(:p as jsonb)` rather than `:p::jsonb`: SQLAlchemy's `text()` reads `::` as the
# start of another bind parameter and the statement stops being valid SQL. That cost
# every list route a 500 once, while `make check` stayed green.
#: The declared footer facts are **inherited**, not re-supplied by the caller: they are
#: facts about the file, not about where a human put the field boundaries, and the editor
#: never sees them (`PATCH .../layout` rejects unknown keys). `(order by version desc)[1]`
#: rather than `max(...)` of each column, so the two values always come from the same
#: version - a file whose trailer stopped being recognised must not inherit a stale total
#: alongside a fresh null count.
_INSERT_NEXT_VERSION = text(
    """
    insert into file_layouts (
        id, source_file_id, version, kind, encoding, delimiter, quote_char, has_header,
        header_row_index, footer_row_count, footer_declared_rows, footer_total,
        record_width, fields, inference_evidence,
        sample_records, inferred, edited_by_human
    )
    select :id, :source_file_id, coalesce(max(version), 0) + 1, :kind, :encoding,
           :delimiter, :quote_char, :has_header, :header_row_index, :footer_row_count,
           (array_agg(footer_declared_rows order by version desc))[1],
           (array_agg(footer_total order by version desc))[1],
           :record_width, cast(:fields as jsonb), cast(:inference_evidence as jsonb),
           cast(:sample_records as jsonb), false, true
    from file_layouts
    where source_file_id = :source_file_id
    returning version
    """
)


async def has_materialized_run(session: AsyncSession, file_id: UUID) -> bool:
    """Lock this file's runs, then answer. The lock is held until the caller commits.

    Locking every run for the file rather than only the materialised ones: `for update`
    cannot lock rows that do not match, and the row that matters is the one a concurrent
    transaction is about to *set* `materialized_at` on.
    """
    # The lock first, then the question. Running only the second query would answer
    # correctly and protect nothing.
    await session.execute(_MATERIALIZED_RUN, {"source_file_id": file_id})
    row = (
        await session.execute(_MATERIALIZED_RUN_MATERIALIZED, {"source_file_id": file_id})
    ).first()
    return row is not None


async def insert_next_version(session: AsyncSession, file_id: UUID, values: dict[str, Any]) -> int:
    """Insert the next version for this file and return its number."""
    row = (
        await session.execute(_INSERT_NEXT_VERSION, {"source_file_id": file_id, **values})
    ).first()
    if row is None:
        # `insert ... select` over an aggregate always produces one row, so this is
        # unreachable rather than a case to handle gracefully - say so loudly.
        raise RuntimeError(f"layout insert returned no version for file {file_id}")
    return int(row[0])


async def current_layout(session: AsyncSession, file_id: UUID) -> dict[str, Any] | None:
    row = (await session.execute(_SELECT_CURRENT, {"source_file_id": file_id})).mappings().first()
    return dict(row) if row else None


async def layout_version(
    session: AsyncSession, file_id: UUID, version: int
) -> dict[str, Any] | None:
    row = (
        (await session.execute(_SELECT_VERSION, {"source_file_id": file_id, "version": version}))
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def file_exists(session: AsyncSession, file_id: UUID) -> bool:
    row = (await session.execute(_FILE_EXISTS, {"id": file_id})).first()
    return row is not None
