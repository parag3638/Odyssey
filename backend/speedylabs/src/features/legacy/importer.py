"""Landing a legacy export's rows, matched to the customer's rebate programs.

Worker-side over a plain psycopg connection, one transaction for the whole file. Small by nature -
a rebate summary is one row per program per period, eighteen on the demo - so there is no `COPY`
here and no streaming cursor: `executemany` over eighteen rows is the right size of hammer.

**The match is one equality join on `external_ref`, loaded once.** `PROGRAM` carries exactly the
references P11 seeded into `rebate_programs.external_ref`, so matching is an exact comparison after
stripping surrounding whitespace, and **case-sensitive**. Deliberately unlike brand matching, which
normalises through `lib/manufacturers.normalise`: a brand name is prose that a mapping transform may
have reshaped, while a program reference is an identifier, and folding case on an identifier is a
guess. An unmatched reference is reported with its exact spelling so the difference is visible.

There is **no name fallback**. `PROGRAM_NAME` also matches `rebate_programs.name` on all three
seeded programs, so a fallback would never fire on the demo - dead code shipped with a test that
cannot distinguish it from the primary path - and a silent fallback converts "your program's
reference is wrong" into "it worked". The actionable answer is the unmatched report.

**An unmatched row is imported, never dropped.** A legacy row nobody could match is the single most
useful thing this screen can say, because it means the customer's references and ours disagree and
the variance table would otherwise just be quietly missing a line.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from src.features.legacy.reader import LegacyRow, read
from src.lib import logger
from src.lib.errors import Unparseable
from src.lib.ids import uuid7
from src.lib.timing import MERGE_SQL, StageTimer, stage_timing_param

log = logger.get_logger(__name__)

MATCHED = "matched"
UNMATCHED_PROGRAM = "unmatched_program"

_SELECT_PROGRAMS = """
    select external_ref, id from rebate_programs
    where customer_id = %(customer_id)s and external_ref is not null
"""

_INSERT = """
    insert into legacy_rebate_lines (
        id, customer_id, source_file_id, source_row_number, program_external_ref,
        program_name_text, program_id, period_start, period_end, amount, raw, match_status
    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


@dataclass(frozen=True)
class ImportOutcome:
    rows_imported: int
    rows_matched: int
    rows_unmatched: int


def run_import(
    connection: psycopg.Connection[Any],
    *,
    run_id: str,
    source_file_id: str,
    customer_id: str,
    content: bytes,
    attempt: int = 1,
) -> ImportOutcome:
    """Read, match and store the whole file. Replaces anything an earlier attempt wrote."""
    elapsed = StageTimer()
    rows = read(content)

    # Loaded once, not once per row: eighteen round trips for a dictionary of three entries is the
    # kind of thing that is invisible at demo scale and quadratic at customer scale (§11).
    with connection.cursor() as cur:
        cur.execute(_SELECT_PROGRAMS, {"customer_id": customer_id})
        programs: dict[str, UUID] = {str(ref): program_id for ref, program_id in cur.fetchall()}

        # Delete first, inside the same transaction as the insert, so a retry replaces rather
        # than appends.
        cur.execute("delete from legacy_rebate_lines where source_file_id = %s", (source_file_id,))

        values = [_row_tuple(row, customer_id, source_file_id, programs) for row in rows]
        # Two rows naming the same program and period is a **file** problem, not a database one.
        # Checked here, before the insert, so it arrives as a refusal the user can act on rather
        # than as a `UniqueViolation` - which is not an `AppError`, so the task's handler would
        # not catch it, the run would stay at the `parsing` it had already committed to, and the
        # retry's conditional claim would then report success while the run hung forever.
        _refuse_duplicate_periods(rows)
        cur.executemany(_INSERT, values)
        cur.execute(
            "update source_files set row_count = %s where id = %s",
            (len(rows), source_file_id),
        )
        cur.execute(
            f"update ingest_runs set status = 'imported', parsed_at = %s, {MERGE_SQL}, "
            "updated_at = now() where id = %s",
            (datetime.now(UTC), stage_timing_param("legacy_import", elapsed.ms, attempt), run_id),
        )
    connection.commit()

    matched = sum(1 for value in values if value[11] == MATCHED)
    log.info(
        "legacy_imported",
        ingest_run_id=run_id,
        source_file_id=source_file_id,
        rows=len(rows),
        matched=matched,
        unmatched=len(rows) - matched,
    )
    return ImportOutcome(
        rows_imported=len(rows), rows_matched=matched, rows_unmatched=len(rows) - matched
    )


def _refuse_duplicate_periods(rows: list[LegacyRow]) -> None:
    """One row per program per period, which is what a rebate summary is."""
    seen: dict[tuple[str, Any], int] = {}
    for row in rows:
        key = (row.program_external_ref, row.period_start)
        first = seen.get(key)
        if first is not None:
            raise Unparseable(
                f"Rows {first} and {row.source_row_number} both report "
                f"{row.program_external_ref} for {row.period_start:%Y-%m}. A rebate summary has "
                "one row per program per period; combine them, or remove the duplicate."
            )
        seen[key] = row.source_row_number


def _row_tuple(
    row: LegacyRow, customer_id: str, source_file_id: str, programs: dict[str, UUID]
) -> tuple[Any, ...]:
    program_id = programs.get(row.program_external_ref)
    return (
        str(uuid7()),
        customer_id,
        source_file_id,
        row.source_row_number,
        row.program_external_ref,
        row.program_name_text,
        None if program_id is None else str(program_id),
        row.period_start,
        row.period_end,
        row.amount,
        Json(row.raw),
        MATCHED if program_id is not None else UNMATCHED_PROGRAM,
    )
