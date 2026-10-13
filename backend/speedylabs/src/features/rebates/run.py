"""The calculation pass: one streaming read, one `COPY`, one transaction.

Worker-side over a plain psycopg connection, for the reason the materialiser and the readiness pass
are: the work is 48,150 lines in and 48,150 rows out, and `cursor.copy` plus a **named (server-side)
cursor** keeps both sides of that in bounded memory and inside the budget.

**One transaction for the whole run.** A worker killed after the second `COPY` batch leaves no
durable rows at all, so the retry starts from a clean slate instead of reconciling a partial one.
The delete comes first, inside that same transaction, which is what makes a re-run replace rather
than append.

**Ordering inside the transaction is load-bearing.** The `rebate_calcs` skeleton is inserted
*before* any line is written, because `rebate_calc_lines.rebate_calc_id` is a foreign key and the
ids have to exist to be referenced. Then lines stream by, accumulators fill, and the skeleton is
updated from them at the end - so a period total is the sum of the rows beneath it by construction,
not by a second query that could disagree.

**A zero must never be indistinguishable from a failure.** That is this phase's version of the
mistake Phase 10 made with drift, and it is why every program gets a `rebate_calc_attempts` row
every time, including the ones that matched nothing and the ones that could not run. On an engine
failure the whole calculation rolls back, the errored attempts are written in a second short
transaction so the evidence survives the rollback, `calculated_at` stays null, and the job re-raises
so procrastinate retries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from src.features.rebates.codes import (
    CALCULATED,
    CONTRIBUTED,
    ERRORED,
    NO_LINES_MATCHED,
    OUT_OF_EFFECTIVE_RANGE,
    SKIPPED,
)
from src.features.rebates.engine import LineRebate, RebateEngine, RebateLine, RebateProgramInput
from src.lib import logger
from src.lib.ids import uuid7
from src.lib.manufacturers import lookup_from_rows
from src.lib.money import quantize_amount, quantize_qty
from src.lib.timing import MERGE_SQL, StageTimer, stage_timing_param

log = logger.get_logger(__name__)

#: Rows per round trip from the server-side cursor, and per `COPY`. The materialiser's and the
#: readiness pass's number, for the same reason: big enough that per-batch overhead disappears,
#: small enough to stay a bounded amount of memory.
ENGINE_BATCH_ROWS = 10_000

#: The most `rebate_calc_lines` rows one run may write. The demo writes 48,150 - one per canonical
#: line, because each brand in the seeded catalog has exactly one program - so this is a 10x margin.
#: It exists because the real count is a *product* of lines and matching programs: a pathological
#: program list should become a named error, not an outage. Published rather than hidden, so nobody
#: has to read the source to find out why a job refused (`docs/decisions.md:264`).
MAX_CALC_LINES = 500_000

#: Distinct brand spellings quoted in a `no_lines_matched` reason. Enough to name the problem,
#: bounded because the set is unbounded in a hostile file.
BRAND_SAMPLE_CAP = 5

_COPY_COLUMNS = (
    "id",
    "ingest_run_id",
    "rebate_calc_id",
    "canonical_line_id",
    "line_status",
    "skipped_reason",
    "basis_contribution",
    "amount_contribution",
)

#: The seven columns the engine needs per line. Written out rather than `select *` (§11), and the
#: tuple is the contract between this query and `_line_from`.
_LINE_COLUMNS = (
    "id",
    "invoice_date",
    "brand_manufacturer",
    "uom",
    "qty",
    "pack_size",
    "catch_weight_lbs",
)

#: A literal, not composed and not formatted: semgrep blocks a query that is not one, and the
#: readiness pass already made this call for the same reason. The column list therefore appears
#: twice, and the import-time check below makes drift between the two impossible.
_SELECT_LINES = """
    select id, invoice_date, brand_manufacturer, uom, qty, pack_size, catch_weight_lbs
    from canonical_lines
    where ingest_run_id = %(run_id)s
    order by source_row_number
"""

_SELECT_PROGRAMS = """
    select id, external_ref, name, kind, uom_basis, rate, effective_from, effective_to,
           brand_manufacturer
    from rebate_programs
    where customer_id = %(customer_id)s
    order by external_ref
"""

_SELECT_REFERENCE = """
    select name, brand_values, direct_vendor_values, redistributor_only
    from customer_manufacturers
    where customer_id = %(customer_id)s
    order by name
"""

_SELECT_SPAN = """
    select min(invoice_date) as first_day, max(invoice_date) as last_day,
           count(*) as line_count
    from canonical_lines
    where ingest_run_id = %(run_id)s
"""


def _selected_columns(statement: str) -> tuple[str, ...]:
    body = statement.split("select", 1)[1].split("from", 1)[0]
    return tuple(name.strip() for name in body.replace("\n", " ").split(",") if name.strip())


if _selected_columns(_SELECT_LINES) != _LINE_COLUMNS:
    # Import-time, because a mismatch means `_line_from` reads a column the query did not ask for -
    # a KeyError on the first row of a 48,150-row pass, found in production rather than at import.
    raise RuntimeError(
        "the rebate line query and _LINE_COLUMNS disagree: "
        f"{_selected_columns(_SELECT_LINES)} vs {_LINE_COLUMNS}"
    )


class CalcTooLargeError(RuntimeError):
    """The run would write more than `MAX_CALC_LINES` rows. Nothing is written."""


@dataclass
class _Accumulator:
    """One program-period's running totals. The accumulator pattern `lib/profiling` uses."""

    basis: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    contributed: int = 0
    skipped: int = 0
    out_of_scope: int = 0

    def add(self, result: LineRebate) -> None:
        if result.line_status == CONTRIBUTED:
            self.contributed += 1
            self.basis += result.basis_contribution
            self.amount += result.amount_contribution
        elif result.line_status == SKIPPED:
            self.skipped += 1
        else:
            self.out_of_scope += 1


@dataclass
class _ProgramState:
    """What this run made of one program, and the row it will leave behind."""

    program: RebateProgramInput
    external_ref: str
    name: str
    #: The manufacturer this program is keyed to, by name - what a `no_lines_matched` reason has to
    #: say, because "no line names 'GHO'" names a spelling the user never typed.
    brand_manufacturer: str
    matched: int = 0
    periods: dict[date, UUID] = field(default_factory=dict[date, UUID])


@dataclass(frozen=True)
class CalcOutcome:
    programs: int
    periods_written: int
    lines_written: int
    total_amount: Decimal


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _month_end(start: date) -> date:
    following = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return date.fromordinal(following.toordinal() - 1)


def _months(first: date, last: date) -> list[date]:
    """Every calendar month the run touches, inclusive. Periods are months (§Data)."""
    months: list[date] = []
    cursor = _month_start(first)
    stop = _month_start(last)
    while cursor <= stop:
        months.append(cursor)
        cursor = (
            cursor.replace(year=cursor.year + 1, month=1)
            if cursor.month == 12
            else cursor.replace(month=cursor.month + 1)
        )
    return months


def _copy_statement(table: str, columns: tuple[str, ...]) -> sql.Composed:
    """`COPY` from identifiers rather than an f-string - the materialiser's helper, same reason."""
    return sql.SQL("copy {} ({}) from stdin").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
    )


def _line_from(row: dict[str, Any]) -> RebateLine:
    return RebateLine(
        canonical_line_id=row["id"],
        invoice_date=row["invoice_date"],
        brand_manufacturer=str(row["brand_manufacturer"]),
        uom=str(row["uom"]),
        qty=Decimal(row["qty"]),
        pack_size=None if row["pack_size"] is None else int(row["pack_size"]),
        catch_weight_lbs=None
        if row["catch_weight_lbs"] is None
        else Decimal(row["catch_weight_lbs"]),
    )


def _programs_for(connection: psycopg.Connection[Any], *, customer_id: str) -> list[_ProgramState]:
    """Programs with every spelling of their manufacturer attached.

    The two-hop match: a program names a manufacturer, `customer_manufacturers.brand_values` lists
    every spelling a canonical row might carry, and the engine compares against that list. A program
    comparing its own spelling to a canonical row matches nothing on the demo file, because the ERP
    writes `BLC` and the mapping stores `Bayline Creamery`. Migration 0023's foreign key means the
    lookup below cannot miss.
    """
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_REFERENCE, {"customer_id": customer_id})
        lookup = lookup_from_rows(cur.fetchall())
        cur.execute(_SELECT_PROGRAMS, {"customer_id": customer_id})
        rows = cur.fetchall()

    states: list[_ProgramState] = []
    for row in rows:
        entry = lookup.find(str(row["brand_manufacturer"]))
        # The foreign key guarantees a reference row exists; if the name were somehow
        # unmatchable the program would match nothing, reported as `no_lines_matched`, not hidden.
        spellings = entry.brand_values if entry is not None else (str(row["brand_manufacturer"]),)
        states.append(
            _ProgramState(
                program=RebateProgramInput(
                    program_id=row["id"],
                    kind=str(row["kind"]),
                    uom_basis=str(row["uom_basis"]),
                    rate=Decimal(row["rate"]),
                    brand_values=tuple(spellings),
                    effective_from=row["effective_from"],
                    effective_to=row["effective_to"],
                ),
                external_ref=str(row["external_ref"] or row["name"]),
                name=str(row["name"]),
                brand_manufacturer=str(row["brand_manufacturer"]),
            )
        )
    return states


def run_calculation(
    connection: psycopg.Connection[Any],
    *,
    run_id: str,
    customer_id: str,
    engine: RebateEngine,
    attempt: int = 1,
) -> CalcOutcome:
    """Calculate this run's rebates. Replaces anything an earlier attempt wrote."""
    elapsed = StageTimer()
    states = _programs_for(connection, customer_id=customer_id)
    if not states:
        # The route checks this first and returns 422; reaching here means the programs were deleted
        # between the check and the job, so refusing loudly is right.
        raise ValueError("this customer has no rebate programs")

    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT_SPAN, {"run_id": run_id})
        span = cur.fetchone()
    assert span is not None
    if span["first_day"] is None:
        raise ValueError("this run has no canonical rows")

    line_count = int(span["line_count"])
    if line_count * len(states) > MAX_CALC_LINES:
        raise CalcTooLargeError(
            f"{line_count:,} lines across {len(states)} programs would write up to "
            f"{line_count * len(states):,} rows, over the {MAX_CALC_LINES:,} row cap"
        )

    months = _months(span["first_day"], span["last_day"])

    try:
        outcome = _calculate(
            connection,
            run_id=run_id,
            states=states,
            months=months,
            line_count=line_count,
            engine=engine,
            elapsed=elapsed,
            attempt=attempt,
        )
    except Exception as exc:
        connection.rollback()
        _record_failure(connection, run_id=run_id, states=states, engine=engine, reason=str(exc))
        log.error("rebate_calculation_failed", ingest_run_id=run_id, error=str(exc))
        # Re-raised so procrastinate retries. The attempt rows survive because they were written
        # after the rollback, in their own transaction.
        raise
    return outcome


def _calculate(
    connection: psycopg.Connection[Any],
    *,
    run_id: str,
    states: list[_ProgramState],
    months: list[date],
    line_count: int,
    engine: RebateEngine,
    elapsed: StageTimer,
    attempt: int,
) -> CalcOutcome:
    with connection.cursor() as cur:
        # Delete first, in the same transaction as the writes, so a retry replaces rather than
        # appends. Lines before calcs: the foreign key points that way.
        cur.execute("delete from rebate_calc_lines where ingest_run_id = %s", (run_id,))
        cur.execute("delete from rebate_calcs where ingest_run_id = %s", (run_id,))
        cur.execute("delete from rebate_calc_attempts where ingest_run_id = %s", (run_id,))

    # The skeleton, at zero, before any line is written: `rebate_calc_lines.rebate_calc_id` is a
    # foreign key, so these ids must exist to be referenced.
    skeleton: list[tuple[Any, ...]] = []
    for state in states:
        for month in months:
            calc_id = uuid7()
            state.periods[month] = calc_id
            skeleton.append(
                (
                    str(calc_id),
                    run_id,
                    str(state.program.program_id),
                    month,
                    _month_end(month),
                    engine.engine_name,
                    engine.engine_version,
                )
            )
    with connection.cursor() as cur:
        cur.executemany(
            "insert into rebate_calcs (id, ingest_run_id, program_id, period_start, period_end, "
            "engine_name, engine_version) values (%s, %s, %s, %s, %s, %s, %s)",
            skeleton,
        )

    accumulators: dict[tuple[UUID, date], _Accumulator] = {}
    by_program = {state.program.program_id: state for state in states}
    programs = [state.program for state in states]
    spellings_seen: set[str] = set()
    written = 0
    buffer: list[tuple[Any, ...]] = []

    with connection.cursor(name="rebate_lines", row_factory=dict_row) as cursor:
        cursor.itersize = ENGINE_BATCH_ROWS
        cursor.execute(_SELECT_LINES, {"run_id": run_id})
        batch: list[RebateLine] = []
        dates: dict[UUID, date] = {}
        for row in cursor:
            line = _line_from(row)
            batch.append(line)
            dates[line.canonical_line_id] = line.invoice_date
            if len(spellings_seen) < BRAND_SAMPLE_CAP:
                spellings_seen.add(line.brand_manufacturer)
            if len(batch) >= ENGINE_BATCH_ROWS:
                written += _price_batch(
                    connection,
                    run_id,
                    batch,
                    programs,
                    dates,
                    accumulators,
                    by_program,
                    buffer,
                    engine,
                )
                batch = []
                dates = {}
        if batch:
            written += _price_batch(
                connection,
                run_id,
                batch,
                programs,
                dates,
                accumulators,
                by_program,
                buffer,
                engine,
            )
    if buffer:
        written += _flush(connection, buffer)

    periods_written = _update_skeleton(connection, states, accumulators)
    _record_attempts(
        connection,
        run_id=run_id,
        states=states,
        accumulators=accumulators,
        months=months,
        line_count=line_count,
        engine=engine,
        spellings=sorted(spellings_seen),
    )

    with connection.cursor() as cur:
        cur.execute(
            f"update ingest_runs set calculated_at = %s, {MERGE_SQL}, updated_at = now() "
            "where id = %s",
            (datetime.now(UTC), stage_timing_param("rebate_calc", elapsed.ms, attempt), run_id),
        )
    connection.commit()

    total = quantize_amount(
        sum((acc.amount for acc in accumulators.values()), Decimal("0")),
    )
    log.info(
        "rebates_calculated",
        ingest_run_id=run_id,
        programs=len(states),
        periods=periods_written,
        lines=written,
        total_amount=f"{total:f}",
        engine=f"{engine.engine_name} {engine.engine_version}",
    )
    return CalcOutcome(
        programs=len(states),
        periods_written=periods_written,
        lines_written=written,
        total_amount=total,
    )


def _price_batch(
    connection: psycopg.Connection[Any],
    run_id: str,
    batch: list[RebateLine],
    programs: list[RebateProgramInput],
    dates: dict[UUID, date],
    accumulators: dict[tuple[UUID, date], _Accumulator],
    by_program: dict[UUID, _ProgramState],
    buffer: list[tuple[Any, ...]],
    engine: RebateEngine,
) -> int:
    """Price one batch, accumulate it, and flush whole `COPY` batches as they fill."""
    written = 0
    for result in engine.calculate(batch, programs):
        month = _month_start(dates[result.canonical_line_id])
        state = by_program[result.program_id]
        state.matched += 1
        accumulators.setdefault((result.program_id, month), _Accumulator()).add(result)
        buffer.append(
            (
                str(uuid7()),
                run_id,
                str(state.periods[month]),
                str(result.canonical_line_id),
                result.line_status,
                result.skipped_reason,
                result.basis_contribution,
                result.amount_contribution,
            )
        )
        if len(buffer) >= ENGINE_BATCH_ROWS:
            written += _flush(connection, buffer)
    return written


def _flush(connection: psycopg.Connection[Any], buffer: list[tuple[Any, ...]]) -> int:
    count = len(buffer)
    with (
        connection.cursor() as cur,
        cur.copy(_copy_statement("rebate_calc_lines", _COPY_COLUMNS)) as copy,
    ):
        for row in buffer:
            copy.write_row(row)
    buffer.clear()
    return count


def _update_skeleton(
    connection: psycopg.Connection[Any],
    states: list[_ProgramState],
    accumulators: dict[tuple[UUID, date], _Accumulator],
) -> int:
    """Fill the zeroed rows from the accumulators. The headline becomes the sum of its parts."""
    updates: list[tuple[Any, ...]] = []
    for state in states:
        for month, calc_id in state.periods.items():
            acc = accumulators.get((state.program.program_id, month))
            if acc is None:
                continue
            updates.append(
                (
                    quantize_qty(acc.basis),
                    quantize_amount(acc.amount),
                    acc.contributed,
                    acc.skipped,
                    acc.out_of_scope,
                    str(calc_id),
                )
            )
    if updates:
        with connection.cursor() as cur:
            cur.executemany(
                "update rebate_calcs set basis_qty = %s, amount = %s, "
                "contributed_line_count = %s, skipped_line_count = %s, "
                "out_of_scope_line_count = %s, updated_at = now() where id = %s",
                updates,
            )
    return len(updates)


def _record_attempts(
    connection: psycopg.Connection[Any],
    *,
    run_id: str,
    states: list[_ProgramState],
    accumulators: dict[tuple[UUID, date], _Accumulator],
    months: list[date],
    line_count: int,
    engine: RebateEngine,
    spellings: list[str],
) -> None:
    """One row per program, every time. The only proof a `0.00` was computed rather than failed."""
    rows: list[tuple[Any, ...]] = []
    for state in states:
        periods = sum(1 for month in months if (state.program.program_id, month) in accumulators)
        covers_a_month = any(
            state.program.effective_from <= _month_end(month)
            and (state.program.effective_to is None or state.program.effective_to >= month)
            for month in months
        )
        if state.matched:
            outcome, reason = CALCULATED, None
        elif not covers_a_month:
            outcome = OUT_OF_EFFECTIVE_RANGE
            reason = (
                f"{state.name} was in effect from {state.program.effective_from.isoformat()}"
                + (
                    f" to {state.program.effective_to.isoformat()}"
                    if state.program.effective_to is not None
                    else " with no end date"
                )
                + f", which covers no period on this file ({months[0].isoformat()} to "
                f"{_month_end(months[-1]).isoformat()})."
            )
        else:
            outcome = NO_LINES_MATCHED
            quoted = ", ".join(repr(value) for value in spellings) or "none"
            reason = (
                f"No line on this file is for {state.brand_manufacturer}. The brand values present "
                f"include {quoted}. Check the brand column's mapping, or the manufacturer list."
            )
        rows.append(
            (
                str(uuid7()),
                run_id,
                str(state.program.program_id),
                outcome,
                reason,
                line_count,
                state.matched,
                periods,
                engine.engine_name,
                engine.engine_version,
            )
        )
    with connection.cursor() as cur:
        cur.executemany(
            "insert into rebate_calc_attempts (id, ingest_run_id, program_id, outcome, reason, "
            "lines_examined, lines_matched, periods_written, engine_name, engine_version) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )


def _record_failure(
    connection: psycopg.Connection[Any],
    *,
    run_id: str,
    states: list[_ProgramState],
    engine: RebateEngine,
    reason: str,
) -> None:
    """Leave the run *uncalculated*, with errored attempts saying why, in one transaction.

    The evidence has to outlive the thing that failed, so this runs after the rollback.
    `periods_written` is zero and a check constraint enforces it, so an errored attempt can never
    be read as a total.

    **A failed recalculation clears the previous results.** This was found by measurement, not
    reasoning: the rollback discards the failed pass's writes, but it also discards its *delete*, so
    an earlier successful calculation's `rebate_calcs` rows and `calculated_at` survive while this
    function replaces that run's attempt rows with errored ones. The result was a run reporting a
    $387,648.02 total *and* that all three of its programs had errored, with the two tables naming
    different engines. That mixed state is exactly the "a number is indistinguishable from a
    failure" class this phase exists to prevent, and it is worse than having no number: a gate can
    refuse an empty run, but it cannot tell which half of a contradictory one to trust.

    So the invariant is made absolute instead: **either a run has a complete set of results with
    `calculated_at` set and no errored attempts, or it has errored attempts, no results, and a null
    `calculated_at`.** Nothing in between is representable.

    The cost, stated plainly: a transient engine failure discards numbers that were good. They are
    deterministic, take under a second to recompute, and procrastinate retries three times on its
    own - so the price is small and it buys an invariant every later reader can rely on.
    """
    sentence = (
        "The calculation did not finish for this program, so no amount was computed. "
        "Start it again."
    )
    try:
        with connection.cursor() as cur:
            # Lines before calcs: the foreign key points that way.
            cur.execute("delete from rebate_calc_lines where ingest_run_id = %s", (run_id,))
            cur.execute("delete from rebate_calcs where ingest_run_id = %s", (run_id,))
            cur.execute("delete from rebate_calc_attempts where ingest_run_id = %s", (run_id,))
            cur.execute(
                "update ingest_runs set calculated_at = null, updated_at = now() where id = %s",
                (run_id,),
            )
            cur.executemany(
                "insert into rebate_calc_attempts (id, ingest_run_id, program_id, outcome, reason, "
                "lines_examined, lines_matched, periods_written, engine_name, engine_version) "
                "values (%s, %s, %s, %s, %s, 0, 0, 0, %s, %s)",
                [
                    (
                        str(uuid7()),
                        run_id,
                        str(state.program.program_id),
                        ERRORED,
                        sentence,
                        engine.engine_name,
                        engine.engine_version,
                    )
                    for state in states
                ],
            )
        connection.commit()
    except Exception:
        # A failure writing the failure record must not mask the original error.
        connection.rollback()
        log.error("rebate_failure_record_failed", ingest_run_id=run_id, original_error=reason)
