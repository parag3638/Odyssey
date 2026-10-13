"""The readiness pass: one streaming read, eight accumulators, two tables written.

Runs in the worker over a plain psycopg connection, for the same reason the materialiser does:
the work is a scan of 48,150 rows, and a **named (server-side) cursor** streams them in batches
instead of materialising the whole result in memory. The async routes read at most sixteen rows
and stay on SQLAlchemy.

The checks themselves know nothing about any of this - `run_checks` is a pure function of
(rows, context), so its rules are unit-tested with hand-written rows and a failure is a failure
of the rule rather than of the plumbing.

Persistence is delete-then-insert inside one transaction, like the materialiser: a second pass
replaces the first rather than colliding with the unique key, so a redelivered or retried job
converges on exactly one set of results.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from src.features.readiness.business_days import OBSERVED_HOLIDAYS
from src.features.readiness.checks import run_checks
from src.features.readiness.codes import SEVERITY
from src.features.readiness.context import FooterFacts, ReadinessContext
from src.features.readiness.rows import CheckResult, ReadinessRow
from src.lib import logger
from src.lib.ids import uuid7
from src.lib.manufacturers import lookup_from_rows
from src.lib.timing import MERGE_SQL, StageTimer, stage_timing_param

log = logger.get_logger(__name__)

#: Rows fetched per round trip from the server-side cursor. The same order of magnitude as the
#: materialiser's `COPY` batch, for the same reason: big enough that per-batch overhead
#: disappears, small enough to be a bounded amount of memory.
FETCH_ROWS = 10_000

#: The twelve columns the checks read. Written out rather than `select *` (§11), and the
#: tuple is the contract between this query and `ReadinessRow`.
_ROW_COLUMNS = (
    "source_row_number",
    "invoice_no",
    "invoice_date",
    "dist_item_id",
    "brand_manufacturer",
    "invoice_vendor",
    "qty",
    "uom",
    "pack_size",
    "catch_weight_lbs",
    "unit_price",
    "ext_amount",
)

#: Written out as a **literal**, not composed and not formatted. `runs/repo.py` made this call
#: already and its reasoning applies unchanged: semgrep blocks a query that is not a literal, and
#: while the interpolated part here is a module constant with no user input near it, a rule with a
#: hand-verified exception costs a reviewer more than typing the column list out.
#:
#: The cost is the list appearing twice, so the import-time check below makes drift impossible
#: rather than merely unlikely.
_SELECT_ROWS = """
    select source_row_number, invoice_no, invoice_date, dist_item_id, brand_manufacturer,
           invoice_vendor, qty, uom, pack_size, catch_weight_lbs, unit_price, ext_amount
    from canonical_lines
    where ingest_run_id = %(run_id)s
    order by source_row_number
"""


def _selected_columns(statement: str) -> tuple[str, ...]:
    """The column names a `select … from` statement names, in order."""
    body = statement.split("select", 1)[1].split("from", 1)[0]
    return tuple(name.strip() for name in body.replace("\n", " ").split(",") if name.strip())


if _selected_columns(_SELECT_ROWS) != _ROW_COLUMNS:
    # Import-time, because a mismatch means `_row_from` reads a column the query did not ask
    # for - a `KeyError` on the first row of a 48,150-row pass, found in production rather than
    # at import.
    raise RuntimeError(
        "readiness's row query and _ROW_COLUMNS disagree: "
        f"{_selected_columns(_SELECT_ROWS)} vs {_ROW_COLUMNS}"
    )

_SELECT_FOOTER = """
    select footer_row_count, footer_declared_rows, footer_total
    from file_layouts
    where source_file_id = %(source_file_id)s
    order by version desc
    limit 1
"""

_SELECT_REFERENCE = """
    select name, brand_values, direct_vendor_values, redistributor_only
    from customer_manufacturers
    where customer_id = %(customer_id)s
    order by name
"""

_COUNT_REJECTED = """
    select count(*) as rejected from materialization_errors where ingest_run_id = %(run_id)s
"""

_INSERT_CHECK_RUN = """
    insert into readiness_check_runs (
        id, ingest_run_id, check_code, outcome, not_applicable_reason, errored_message,
        rows_examined, context
    ) values (
        %(id)s, %(ingest_run_id)s, %(check_code)s, %(outcome)s, %(not_applicable_reason)s,
        %(errored_message)s, %(rows_examined)s, %(context)s
    )
"""

_INSERT_FINDING = """
    insert into readiness_findings (
        id, ingest_run_id, check_code, severity, affected_rows, affected_amount, detail,
        example_row_numbers
    ) values (
        %(id)s, %(ingest_run_id)s, %(check_code)s, %(severity)s, %(affected_rows)s,
        %(affected_amount)s, %(detail)s, %(example_row_numbers)s
    )
"""


@dataclass
class ReadinessOutcome:
    status: str
    blocking: int
    warnings: int
    checks_run: int
    rows_examined: int


def run_readiness(
    connection: psycopg.Connection[Any],
    *,
    run_id: str,
    source_file_id: str,
    customer_id: str,
    attempt: int = 1,
) -> ReadinessOutcome:
    """Check this run's canonical rows and store the results. Replaces any earlier pass."""
    elapsed = StageTimer()
    context = _context_for(
        connection, source_file_id=source_file_id, customer_id=customer_id, run_id=run_id
    )

    # A named cursor is a server-side cursor: rows arrive in batches of `FETCH_ROWS` instead
    # of the whole result landing in this process. `run_checks` consumes the iterator once,
    # which is the single pass the budget depends on.
    with connection.cursor(name="readiness_rows", row_factory=dict_row) as cursor:
        cursor.itersize = FETCH_ROWS
        cursor.execute(_SELECT_ROWS, {"run_id": run_id})
        results = run_checks((_row_from(row) for row in cursor), context)

    _persist(connection, run_id, results)
    blocking, warnings = _counts(results)
    status = "blocked" if blocking else "ready"

    with connection.cursor() as cursor:
        cursor.execute(
            f"update ingest_runs set status = %s, readiness_at = %s, {MERGE_SQL}, "
            "updated_at = now() where id = %s",
            (
                status,
                datetime.now(UTC),
                stage_timing_param("readiness", elapsed.ms, attempt),
                run_id,
            ),
        )
    connection.commit()

    examined = max((result.rows_examined for result in results), default=0)
    log.info(
        "readiness_complete",
        ingest_run_id=run_id,
        status=status,
        blocking=blocking,
        warnings=warnings,
        checks=len(results),
    )
    return ReadinessOutcome(
        status=status,
        blocking=blocking,
        warnings=warnings,
        checks_run=len(results),
        rows_examined=examined,
    )


def _row_from(row: dict[str, Any]) -> ReadinessRow:
    return ReadinessRow(
        source_row_number=int(row["source_row_number"]),
        invoice_no=str(row["invoice_no"]),
        invoice_date=row["invoice_date"],
        dist_item_id=str(row["dist_item_id"]),
        brand_manufacturer=str(row["brand_manufacturer"]),
        invoice_vendor=str(row["invoice_vendor"]),
        qty=Decimal(row["qty"]),
        uom=str(row["uom"]),
        pack_size=None if row["pack_size"] is None else int(row["pack_size"]),
        catch_weight_lbs=(
            None if row["catch_weight_lbs"] is None else Decimal(row["catch_weight_lbs"])
        ),
        unit_price=Decimal(row["unit_price"]),
        ext_amount=Decimal(row["ext_amount"]),
    )


def _context_for(
    connection: psycopg.Connection[Any], *, source_file_id: str, customer_id: str, run_id: str
) -> ReadinessContext:
    """Everything the checks need that is not a row. Three small queries, before the scan."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(_SELECT_FOOTER, {"source_file_id": source_file_id})
        layout = cursor.fetchone()
        cursor.execute(_SELECT_REFERENCE, {"customer_id": customer_id})
        reference = cursor.fetchall()
        cursor.execute(_COUNT_REJECTED, {"run_id": run_id})
        rejected_row = cursor.fetchone()

    footer = FooterFacts()
    if layout is not None:
        footer = FooterFacts(
            has_footer_record=int(layout["footer_row_count"] or 0) > 0,
            declared_rows=layout["footer_declared_rows"],
            declared_total=layout["footer_total"],
        )
    return ReadinessContext(
        footer=footer,
        manufacturers=lookup_from_rows(reference),
        holidays=OBSERVED_HOLIDAYS,
        rows_rejected=int((rejected_row or {}).get("rejected", 0)),
    )


def _counts(results: list[CheckResult]) -> tuple[int, int]:
    blocking = sum(1 for r in results if r.outcome == "findings" and SEVERITY[r.code] == "blocking")
    warnings = sum(1 for r in results if r.outcome == "findings" and SEVERITY[r.code] == "warning")
    return blocking, warnings


def _persist(connection: psycopg.Connection[Any], run_id: str, results: list[CheckResult]) -> None:
    """Replace this run's results. Delete and insert in the same transaction, so a retry
    converges instead of colliding with the unique key."""
    with connection.cursor() as cursor:
        cursor.execute("delete from readiness_findings where ingest_run_id = %s", (run_id,))
        cursor.execute("delete from readiness_check_runs where ingest_run_id = %s", (run_id,))
        for result in results:
            cursor.execute(
                _INSERT_CHECK_RUN,
                {
                    "id": uuid7(),
                    "ingest_run_id": run_id,
                    "check_code": result.code,
                    "outcome": result.outcome,
                    "not_applicable_reason": result.not_applicable_reason,
                    "errored_message": result.errored_message,
                    "rows_examined": result.rows_examined,
                    "context": Json(result.context) if result.context else None,
                },
            )
            finding = result.finding
            if finding is None:
                continue
            cursor.execute(
                _INSERT_FINDING,
                {
                    "id": uuid7(),
                    "ingest_run_id": run_id,
                    "check_code": result.code,
                    "severity": SEVERITY[result.code],
                    "affected_rows": finding.affected_rows,
                    "affected_amount": finding.affected_amount,
                    "detail": Json(finding.detail),
                    "example_row_numbers": list(finding.example_row_numbers),
                },
            )
