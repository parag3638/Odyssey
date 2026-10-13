"""Attributing every variance dollar to the line it came from. One pass, one transaction.

A5 claims a percentage of variance dollars explained, and that claim is only as good as its unit.
The unit is a link: one row saying how many dollars of one variance came from one line, and why.
Rolling up from lines rather than classifying whole variances is what makes the percentage
measurable per dollar and the drill-down truthful.

**Conservation is asserted per variance row, and nowhere else.** The links on one variance sum to
that variance's `variance_amount` exactly, with any shortfall carried as a single `unexplained`
residue link so the explained share can never be inflated by losing dollars. It cannot be asserted
over the whole file, because `date_cutoff` **relocates** money rather than creating it - the same
dollars leave one month and arrive in the next, cancelling in any total. Measured: the net
difference across the demo is `4,035.14` while the seeded causes total `7,376.08`, and the
`3,340.94` gap is exactly that relocation.

**The relocation writes two links naming the same canonical line** - negative on the month the
legacy system took the money out of, positive on the month it put it into. That is why
`variance_line_links` permits one line twice (on two different variances) and why migration 0028's
honesty constraint exempts only the residue from naming a line.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Json

from src.features.attribution.rules import (
    AttributionLine,
    ProgramTerms,
    RuleContext,
    WeightEstimate,
    classify,
)
from src.features.variance.codes import ROOT_CAUSES, UNATTRIBUTED, UNEXPLAINED
from src.lib import logger
from src.lib.ids import uuid7
from src.lib.manufacturers import lookup_from_rows
from src.lib.money import quantize_amount
from src.lib.timing import MERGE_ONLY_SQL, StageTimer, stage_timing_param

log = logger.get_logger(__name__)

COPY_BATCH_ROWS = 10_000

_LINK_COLUMNS = (
    "id",
    "ingest_run_id",
    "variance_id",
    "canonical_line_id",
    "root_cause",
    "contribution_amount",
    "evidence",
)

_SELECT_LINES = """
    select l.id, l.source_row_number, l.invoice_no, l.invoice_date, l.dist_item_id,
           l.brand_manufacturer, l.invoice_vendor, l.qty, l.uom, l.pack_size,
           l.catch_weight_lbs, l.ext_amount,
           r.rebate_calc_id, r.line_status, r.skipped_reason,
           r.basis_contribution, r.amount_contribution,
           c.program_id, c.period_start
    from canonical_lines l
    join rebate_calc_lines r on r.canonical_line_id = l.id
    join rebate_calcs c on c.id = r.rebate_calc_id
    where l.ingest_run_id = %(run_id)s and r.ingest_run_id = %(run_id)s
    order by l.source_row_number
"""

#: The pool every catch-weight estimate comes from: this run's lines that still have a weight,
#: as a mean **per unit** so the estimate scales with the line's quantity.
_SELECT_WEIGHTS = """
    select dist_item_id, sum(catch_weight_lbs) / sum(qty) as per_unit, count(*) as sample_lines
    from canonical_lines
    where ingest_run_id = %(run_id)s and catch_weight_lbs is not null and qty <> 0
    group by dist_item_id
"""

#: The second copy of each duplicated line - the one the legacy system paid twice. Decided here,
#: in one query, because a rule cannot see its neighbours.
_SELECT_DUPLICATE_COPIES = """
    select id from (
        select id, row_number() over (
            partition by invoice_no, dist_item_id, qty, ext_amount
            order by source_row_number
        ) as copy_number
        from canonical_lines where ingest_run_id = %(run_id)s
    ) ranked where copy_number > 1
"""

_SELECT_PROGRAMS = """
    select id, rate, uom_basis, redistributor_rate from rebate_programs
    where customer_id = %(customer_id)s
"""

_SELECT_REFERENCE = """
    select name, brand_values, direct_vendor_values, redistributor_only
    from customer_manufacturers where customer_id = %(customer_id)s
"""

_SELECT_VARIANCES = """
    select id, program_id, period_start, variance_amount
    from variances where ingest_run_id = %(run_id)s
"""


@dataclass(frozen=True)
class AttributionOutcome:
    links: int
    variances: int
    explained_amount: Decimal
    absolute_amount: Decimal
    residue_amount: Decimal
    #: rule name -> lines it failed on. Empty on a clean run.
    rule_failures: dict[str, int]


def _next_month(day: date) -> date:
    return (
        day.replace(year=day.year + 1, month=1)
        if day.month == 12
        else day.replace(month=day.month + 1)
    )


def _copy_statement(table: str, columns: tuple[str, ...]) -> sql.Composed:
    return sql.SQL("copy {} ({}) from stdin").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
    )


def run_attribution(
    connection: psycopg.Connection[Any], *, run_id: str, customer_id: str, attempt: int = 1
) -> AttributionOutcome:
    """Explain this run's variances. Replaces anything an earlier attempt wrote."""
    elapsed = StageTimer()
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_PROGRAMS, {"customer_id": customer_id})
        programs = {
            row["id"]: ProgramTerms(
                program_id=row["id"],
                rate=row["rate"],
                uom_basis=str(row["uom_basis"]),
                redistributor_rate=row["redistributor_rate"],
            )
            for row in cur.fetchall()
        }
        cur.execute(_SELECT_REFERENCE, {"customer_id": customer_id})
        lookup = lookup_from_rows(cur.fetchall())
        cur.execute(_SELECT_WEIGHTS, {"run_id": run_id})
        weights = {
            str(row["dist_item_id"]): WeightEstimate(
                per_unit=row["per_unit"], sample_lines=int(row["sample_lines"])
            )
            for row in cur.fetchall()
        }
        cur.execute(_SELECT_DUPLICATE_COPIES, {"run_id": run_id})
        duplicates = frozenset(row["id"] for row in cur.fetchall())
        cur.execute("select legacy_period_cutoff_day from customers where id = %s", (customer_id,))
        row = cur.fetchone()
        cutoff_day = None if row is None else row["legacy_period_cutoff_day"]
        cur.execute(_SELECT_VARIANCES, {"run_id": run_id})
        variances = {
            (r["program_id"], r["period_start"]): (r["id"], r["variance_amount"])
            for r in cur.fetchall()
        }

    context = RuleContext(programs, lookup, duplicates, weights, cutoff_day)

    with connection.cursor() as cur:
        cur.execute("delete from variance_line_links where ingest_run_id = %s", (run_id,))

    #: rule name -> how many lines it failed on. Surfaced on every variance's `detail` and on the
    #: summary, so a degraded run says so rather than quietly reporting a lower percentage.
    rule_failures: dict[str, int] = {}
    claimed: dict[UUID, Decimal] = defaultdict(Decimal)
    by_cause: dict[UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    buffer: list[tuple[Any, ...]] = []
    written = 0

    # `enable_nestloop = off` for this one statement, and the reason is worth the paragraph.
    #
    # This join took **94-130 s** in the demo seed while measuring 23 ms under `EXPLAIN ANALYZE`
    # moments later. Phase instrumentation put all of it in this `execute`; `auto_explain` caught
    # the plan from inside the job:
    #
    #   Nested Loop (cost=0.71..11.91 rows=1) (actual time=0.018..167879.810 rows=48150)
    #     -> Index Scan using ix_rebate_calc_lines_run on rebate_calc_lines r
    #          (cost=0.29..4.31 rows=1) (actual rows=48150 loops=48150)
    #
    # `rows=1` against an actual 48,150 is the whole story: 48,150 loops over 48,150 rows is some
    # 2.3 billion row operations. The estimate is wrong because `demo/reset` deletes the
    # customer's rows and the pipeline then bulk-loads ~48,000 fresh ones seconds before this
    # runs, so `pg_class.reltuples` still says the table is empty. Autovacuum fixes it a minute
    # later - which is why every manual probe measured 0.2-0.5 s and the running job did not.
    #
    # **`ANALYZE` is not available here.** The obvious fix was tried three ways and measured as
    # having no effect at all, because the app role cannot analyse a table it does not own:
    #
    #   speedy=> analyze canonical_lines;
    #   WARNING:  permission denied to analyze "canonical_lines", skipping it
    #
    # It is a *warning*, not an error, so the statement silently succeeds and changes nothing.
    # `canonical_lines` is owned by `speedy_migrator`, and BACKEND.md §4 is right that the app
    # role should not own its tables - so the planner's estimate cannot be corrected from here.
    #
    # Disabling nested loops for this statement sidesteps the estimate instead of fixing it.
    # That is sound rather than a hack *for this query specifically*: it returns every canonical
    # line of a run, tens of thousands of rows, and a nested loop is never the right shape for
    # it at that size - the planner only chooses one when it believes the table is empty.
    # Verified to produce a hash join both with statistics deleted and with them correct, at
    # 16-23 ms either way. `SET LOCAL` so it expires with this transaction and no other
    # statement's plan is touched.
    with connection.cursor() as planner:
        planner.execute("set local enable_nestloop = off")

    # A plain cursor rather than a server-side one. `DECLARE CURSOR` is planned under
    # `cursor_tuple_fraction` (0.1 by default), optimising for the first 10% of rows when this
    # loop consumes all of them - a second reason to reach a fast-start plan. ~48,000 rows at
    # ~183 bytes is roughly 9 MB held in memory, for a file the spec sizes at 50,000 lines.
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(_SELECT_LINES, {"run_id": run_id})
        # `_batched` kept rather than iterating `cursor` directly: it keeps the block size an
        # explicit decision here instead of a library default, and it is what let the earlier
        # server-side version control its `FETCH` size. (That earlier note claimed round trips
        # were the cause of a 95.6 s stage. They were not - `auto_explain` later showed the real
        # cause above, and the fetch loop itself measures 0.19 s in 5 blocks either way.)
        for row in _batched(cursor, COPY_BATCH_ROWS):
            line = AttributionLine(
                canonical_line_id=row["id"],
                source_row_number=int(row["source_row_number"]),
                invoice_no=str(row["invoice_no"]),
                invoice_date=row["invoice_date"],
                dist_item_id=str(row["dist_item_id"]),
                brand_manufacturer=str(row["brand_manufacturer"]),
                invoice_vendor=str(row["invoice_vendor"]),
                qty=row["qty"],
                uom=str(row["uom"]),
                pack_size=row["pack_size"],
                catch_weight_lbs=row["catch_weight_lbs"],
                ext_amount=row["ext_amount"],
                program_id=row["program_id"],
                line_status=str(row["line_status"]),
                skipped_reason=row["skipped_reason"],
                basis_contribution=row["basis_contribution"],
                amount_contribution=row["amount_contribution"],
            )
            claim = classify(line, context, rule_failures)
            if claim is None:
                continue

            period = row["period_start"]
            targets: list[tuple[date, Decimal]]
            if claim.relocates:
                # Out of this period, into the next: the same dollars, named on the same line,
                # on two different variances.
                targets = [(period, -claim.contribution), (_next_month(period), claim.contribution)]
            else:
                targets = [(period, claim.contribution)]

            for target_period, amount in targets:
                found = variances.get((line.program_id, target_period))
                if found is None:
                    # No variance for that program-period means nothing to explain there - which
                    # happens when the relocation's destination month is outside the comparison.
                    continue
                variance_id, _ = found
                buffer.append(
                    (
                        str(uuid7()),
                        run_id,
                        str(variance_id),
                        str(line.canonical_line_id),
                        claim.root_cause,
                        amount,
                        Json(claim.evidence),
                    )
                )
                claimed[variance_id] += amount
                by_cause[variance_id][claim.root_cause] += amount
                if len(buffer) >= COPY_BATCH_ROWS:
                    written += _flush(connection, buffer)

    # The residue: whatever the rules could not claim, as one explicit link per variance, so the
    # explained share can never be inflated by dollars quietly going missing.
    for (program_id, period), (variance_id, variance_amount) in variances.items():
        del program_id, period
        residue = quantize_amount(variance_amount - claimed[variance_id])
        if residue == 0:
            continue
        buffer.append(
            (
                str(uuid7()),
                run_id,
                str(variance_id),
                None,
                UNEXPLAINED,
                residue,
                Json({"note": "No rule claims these dollars."}),
            )
        )
        by_cause[variance_id][UNEXPLAINED] += residue
    if buffer:
        written += _flush(connection, buffer)

    _settle_variances(connection, variances, by_cause, rule_failures)
    with connection.cursor() as cur:
        cur.execute(
            MERGE_ONLY_SQL,
            (stage_timing_param("attribution", elapsed.ms, attempt), run_id),
        )
    connection.commit()

    explained = sum(
        (
            abs(amount)
            for causes in by_cause.values()
            for cause, amount in causes.items()
            if cause != UNEXPLAINED
        ),
        Decimal("0"),
    )
    residue_total = sum(
        (abs(causes.get(UNEXPLAINED, Decimal("0"))) for causes in by_cause.values()), Decimal("0")
    )
    absolute = sum((abs(amount) for _, amount in variances.values()), Decimal("0"))
    if rule_failures:
        log.error(
            "attribution_rules_failed",
            ingest_run_id=run_id,
            failures=rule_failures,
        )
    log.info(
        "variances_attributed",
        ingest_run_id=run_id,
        links=written,
        variances=len(variances),
        explained=f"{explained:f}",
        residue=f"{residue_total:f}",
        rule_failures=len(rule_failures),
    )
    return AttributionOutcome(
        links=written,
        variances=len(variances),
        explained_amount=explained,
        absolute_amount=absolute,
        residue_amount=residue_total,
        rule_failures=dict(rule_failures),
    )


def _batched(cursor: Any, size: int) -> Iterator[dict[str, Any]]:
    """Yield rows a block at a time, so the fetch size is ours and not a default's."""
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            return
        yield from rows


def _flush(connection: psycopg.Connection[Any], buffer: list[tuple[Any, ...]]) -> int:
    count = len(buffer)
    with (
        connection.cursor() as cur,
        cur.copy(_copy_statement("variance_line_links", _LINK_COLUMNS)) as copy,
    ):
        for row in buffer:
            copy.write_row(row)
    buffer.clear()
    return count


def _settle_variances(
    connection: psycopg.Connection[Any],
    variances: dict[tuple[UUID, date], tuple[UUID, Decimal]],
    by_cause: dict[UUID, dict[str, Decimal]],
    rule_failures: dict[str, int],
) -> None:
    """Write each variance's dominant cause and its breakdown.

    `root_cause` and `explained` are set in the **same statement**, so 0026's
    `check (explained = false or root_cause <> 'unattributed')` is structurally satisfied rather
    than something the writer has to remember.

    The dominant cause is the largest **absolute** contribution, tie-broken by position in
    `ROOT_CAUSES` so the answer is deterministic. The residue never wins: it is not a cause, and a
    variance whose biggest single component is "we could not explain it" should say `unattributed`
    rather than name an explanation it does not have.
    """
    updates: list[tuple[Any, ...]] = []
    for variance_id, variance_amount in variances.values():
        causes = by_cause.get(variance_id, {})
        ranked = sorted(
            ((cause, amount) for cause, amount in causes.items() if cause != UNEXPLAINED),
            key=lambda item: (-abs(item[1]), ROOT_CAUSES.index(item[0])),
        )
        dominant = ranked[0][0] if ranked else UNATTRIBUTED
        # Quantised, so a zero residue serialises as "0.00" and not "0" - `Decimal("0"):f` is
        # `"0"`, which fails the export schema's own money pattern on the twelve variances that
        # have no residue at all.
        residue = quantize_amount(causes.get(UNEXPLAINED, Decimal("0")))
        explained_here = sum((abs(a) for c, a in causes.items() if c != UNEXPLAINED), Decimal("0"))
        total_here = explained_here + abs(residue)
        detail = {
            "byCause": [
                {"rootCause": cause, "amount": f"{amount:f}"}
                for cause, amount in sorted(causes.items(), key=lambda i: (-abs(i[1]), i[0]))
            ],
            "explainedShare": (f"{(explained_here / total_here):.4f}" if total_here else "1.0000"),
            "residueAmount": f"{residue:f}",
            "varianceAmount": f"{variance_amount:f}",
            # Empty on a clean run. Present and non-empty means some dollars went to residue
            # because a rule could not run, not because no rule applied.
            "ruleFailures": [
                {"rule": rule, "lineCount": count} for rule, count in sorted(rule_failures.items())
            ],
        }
        updates.append((dominant, dominant != UNATTRIBUTED, Json(detail), str(variance_id)))

    if updates:
        with connection.cursor() as cur:
            cur.executemany(
                "update variances set root_cause = %s, explained = %s, detail = %s, "
                "updated_at = now() where id = %s",
                updates,
            )
