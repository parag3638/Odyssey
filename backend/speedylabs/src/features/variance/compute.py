"""Comparing what they paid with what we calculate, per program per month.

One `insert … select` over two small tables, inside one transaction. No Python loop touches a row:
eighteen program-periods is a join, not a pass, and doing it in SQL is what makes
`variance_amount = legacy_amount - calculated_amount` structurally true rather than a property the
database then has to re-check (it re-checks it anyway - migration 0026 - but the constraint should
never be the thing that catches us).

**A full outer join, because a gap is a variance and not an omission.** A program-month the legacy
system paid for and we calculated nothing for is `presence='legacy_only'` with
`calculated_amount = 0.00`; the reverse is `calculated_only`. Dropping either side would produce a
table that looks complete and quietly is not - the same failure the legacy reader refuses a whole
file to avoid.

**Unmatched legacy rows produce no variance row at all.** They name a program this customer does
not have, so there is nothing to compare them against. They are reported in the unmatched panel and
counted in the summary, so the screen is never quietly short.

**Only `matched` legacy rows and only this run's `rebate_calcs` take part**, and both are filtered
inside the join rather than afterwards, so a second legacy file cannot leak into a comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg

from src.lib import logger
from src.lib.timing import MERGE_ONLY_SQL, MERGE_SQL, StageTimer, stage_timing_param

log = logger.get_logger(__name__)

#: **`gen_random_uuid()`, not `uuid7()`, and this is the one place in the codebase that differs.**
#: Everywhere else ids are minted in Python as UUIDv7 for index locality. Keeping this a single
#: `insert … select` is worth more here: it makes `variance_amount = legacy - calculated`
#: structurally true rather than something Python computes and the database then has to catch, and
#: it needs ids from SQL. The locality argument does not apply to eighteen rows per run.
#:
#: A full outer join of this run's calculated periods against the legacy file's matched rows.
#: `coalesce` on both amounts is what turns a missing side into a zero rather than a null, and
#: `presence` records which side was missing so the UI can say so in words.
_COMPUTE = """
    insert into variances (
        id, ingest_run_id, program_id, legacy_source_file_id, period_start, period_end,
        legacy_amount, calculated_amount, variance_amount, presence
    )
    select
        gen_random_uuid(),
        %(run_id)s, coalesce(c.program_id, l.program_id), %(legacy_file_id)s,
        coalesce(c.period_start, l.period_start), coalesce(c.period_end, l.period_end),
        coalesce(l.amount, 0), coalesce(c.amount, 0),
        coalesce(l.amount, 0) - coalesce(c.amount, 0),
        case
            when l.program_id is null then 'calculated_only'
            when c.program_id is null then 'legacy_only'
            else 'both'
        end
    from (
        select program_id, period_start, period_end, amount
        from rebate_calcs where ingest_run_id = %(run_id)s
    ) c
    full outer join (
        select program_id, period_start, period_end, amount
        from legacy_rebate_lines
        where source_file_id = %(legacy_file_id)s and match_status = 'matched'
    ) l on l.program_id = c.program_id and l.period_start = c.period_start
"""


@dataclass(frozen=True)
class VarianceOutcome:
    rows: int
    legacy_total: Decimal
    calculated_total: Decimal
    net_variance: Decimal


def run_computation(
    connection: psycopg.Connection[Any],
    *,
    run_id: str,
    legacy_source_file_id: str,
    attempt: int = 1,
) -> VarianceOutcome:
    """Replace this run's comparison with one against the given legacy export."""
    elapsed = StageTimer()
    with connection.cursor() as cur:
        # Delete first, in the same transaction: one comparison per run, replaced rather than
        # accumulated, so two legacy files never both appear on the screen.
        cur.execute("delete from variances where ingest_run_id = %s", (run_id,))
        cur.execute(
            _COMPUTE,
            {"run_id": run_id, "legacy_file_id": legacy_source_file_id},
        )
        cur.execute(
            "select count(*), coalesce(sum(legacy_amount), 0), "
            "coalesce(sum(calculated_amount), 0), coalesce(sum(variance_amount), 0) "
            "from variances where ingest_run_id = %s",
            (run_id,),
        )
        row = cur.fetchone()
        assert row is not None
        rows, legacy_total, calculated_total, net = row

        if rows:
            # `first_variance_at` is written **once**, conditional on null. The column is named
            # *first*, and a recompute an hour later would otherwise destroy A6's measurement -
            # which is the number the dashboard leads with. P15's demo reset must clear it
            # explicitly to get a fresh one.
            now = datetime.now(UTC)
            cur.execute(
                "update ingest_runs set first_variance_at = %s, "
                "elapsed_ms_to_first_variance = "
                "  greatest(0, (extract(epoch from (%s - created_at)) * 1000)::bigint), "
                f"{MERGE_SQL}, updated_at = now() "
                "where id = %s and first_variance_at is null",
                (now, now, stage_timing_param("variance", elapsed.ms, attempt), run_id),
            )
        # The write above is conditional on `first_variance_at is null`, so on a recompute it
        # matches no row and the timing would be lost with it. Written separately in that case:
        # the stage ran, and how long it took is true whether or not it was the first time.
        cur.execute(
            MERGE_ONLY_SQL,
            (stage_timing_param("variance", elapsed.ms, attempt), run_id),
        )
    connection.commit()

    log.info(
        "variances_computed",
        ingest_run_id=run_id,
        legacy_source_file_id=legacy_source_file_id,
        rows=rows,
        legacy_total=f"{legacy_total:f}",
        calculated_total=f"{calculated_total:f}",
        net_variance=f"{net:f}",
    )
    return VarianceOutcome(
        rows=int(rows),
        legacy_total=legacy_total,
        calculated_total=calculated_total,
        net_variance=net,
    )
