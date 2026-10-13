"""`features/rebates/run.py::run_calculation`, called directly against a live Postgres - the same
pattern `test_readiness_one_pass_speed_adversarial.py` uses for the materialiser.

Two kinds of fixture here, deliberately:

* A handful of tests run against the **full 48,150-row demo month**, via `full_run_id`, which
  depends on the `seeded_demo` fixture (`tests/conftest.py`) to establish (reset-and-reseed, once
  per session) that run itself rather than assume a previous run left it behind - because they
  are about scale: speed, a single streaming read, byte-identical reruns, and the stored
  sum-equals-its-own-parts invariant over all 18 real rows.
* Everything else builds a **small synthetic customer** (`tests/rebate_fixtures.py`) because the
  thing under test - a mid-batch failure, `MAX_CALC_LINES`, effective-date clipping, a
  `no_lines_matched` / `out_of_effective_range` outcome, a period that nets negative - is a
  specific shape of data, not a scale property, and building it by hand is faster and more exact
  than perturbing the shared demo fixture (which several other suites also read).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from src.features.rebates import run as run_module
from src.features.rebates.reference_engine import ReferenceRebateEngine
from src.features.rebates.run import CalcTooLargeError, run_calculation
from tests.live import APP_DB_URL, DEMO_SLUG
from tests.rebate_fixtures import (
    canonical_line,
    insert_canonical_lines,
    make_ingest_run,
    make_program,
    make_reference,
    synthetic_customer,
)

__all__ = ["synthetic_customer"]  # re-exported fixture; see tests/rebate_fixtures.py


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    connection = psycopg.connect(APP_DB_URL, autocommit=False)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _attempts(connection: psycopg.Connection[Any], run_id: str) -> list[dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select outcome, reason, lines_examined, lines_matched, periods_written, program_id "
            "from rebate_calc_attempts where ingest_run_id = %s",
            (run_id,),
        )
        return cur.fetchall()


def _calcs(connection: psycopg.Connection[Any], run_id: str) -> list[dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select id as rebate_calc_id, program_id, period_start, period_end, basis_qty, "
            "amount, contributed_line_count, skipped_line_count, out_of_scope_line_count "
            "from rebate_calcs where ingest_run_id = %s order by period_start, program_id",
            (run_id,),
        )
        return cur.fetchall()


def _calc_lines_count(connection: psycopg.Connection[Any], run_id: str) -> int:
    with connection.cursor() as cur:
        cur.execute("select count(*) from rebate_calc_lines where ingest_run_id = %s", (run_id,))
        row = cur.fetchone()
        assert row is not None
        return int(row[0])


def _calculated_at(connection: psycopg.Connection[Any], run_id: str) -> Any:
    with connection.cursor() as cur:
        cur.execute("select calculated_at from ingest_runs where id = %s", (run_id,))
        row = cur.fetchone()
        assert row is not None
        return row[0]


# --- full-scale invariants, against the real demo month (skip if not seeded+calculated) --------


def _find_full_month1_run() -> str | None:
    with psycopg.connect(APP_DB_URL, autocommit=True) as probe:
        row = probe.execute(
            """
            select r.id
            from ingest_runs r
            join customers c on c.id = r.customer_id
            where c.slug = %s and r.calculated_at is not null
            order by r.created_at desc
            limit 1
            """,
            (DEMO_SLUG,),
        ).fetchone()
    return str(row[0]) if row else None


@pytest.fixture
def full_run_id(seeded_demo: None) -> str:
    del seeded_demo
    found = _find_full_month1_run()
    if found is None:
        # Defensive only: the seeded_demo fixture should already have made this true, or
        # skipped the test itself (reset disabled / stack unreachable) before this fixture ran.
        pytest.skip(
            "no calculated demo run found even after the seeded_demo fixture ran - see its "
            "docstring in tests/conftest.py"
        )
    return found


def _customer_of(connection: psycopg.Connection[Any], run_id: str) -> str:
    with connection.cursor() as cur:
        cur.execute("select customer_id from ingest_runs where id = %s", (run_id,))
        row = cur.fetchone()
        assert row is not None
        return str(row[0])


def test_rerunning_the_full_demo_month_is_byte_identical_and_under_15_seconds(
    full_run_id: str, conn: psycopg.Connection[Any]
) -> None:
    customer_id = _customer_of(conn, full_run_id)
    before = {
        (row["program_id"], row["period_start"]): (row["amount"], row["basis_qty"])
        for row in _calcs(conn, full_run_id)
    }
    assert len(before) == 18

    started = time.monotonic()
    outcome = run_calculation(
        conn, run_id=full_run_id, customer_id=customer_id, engine=ReferenceRebateEngine()
    )
    elapsed = time.monotonic() - started
    assert elapsed < 15, f"rebate calculation took {elapsed:.2f}s against the 15s budget"
    assert outcome.lines_written == 48_150
    assert outcome.periods_written == 18

    after = {
        (row["program_id"], row["period_start"]): (row["amount"], row["basis_qty"])
        for row in _calcs(conn, full_run_id)
    }
    assert after == before, "a rerun over unchanged data must produce byte-identical results"
    assert _calc_lines_count(conn, full_run_id) == 48_150


def test_every_stored_period_amount_equals_the_sum_of_its_own_lines(
    full_run_id: str, conn: psycopg.Connection[Any]
) -> None:
    """The headline must be the sum of its own parts, checked by query rather than by argument -
    the same invariant `docs/decisions.md` records as already verified, reproduced
    independently here."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select count(*) from rebate_calcs c
            where c.ingest_run_id = %s
              and (c.amount, c.basis_qty) <> (
                select coalesce(sum(l.amount_contribution), 0),
                       coalesce(sum(l.basis_contribution), 0)
                from rebate_calc_lines l where l.rebate_calc_id = c.id
              )
            """,
            (full_run_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 0, "at least one period's amount disagrees with the sum of its own lines"


def test_one_select_against_canonical_lines_for_the_whole_run(
    full_run_id: str, conn: psycopg.Connection[Any]
) -> None:
    """One streaming read, not one per batch - the materialiser's and readiness pass's shape,
    asserted the same way `test_readiness_one_pass_speed_adversarial.py` asserts it."""
    from unittest.mock import patch

    customer_id = _customer_of(conn, full_run_id)
    statements: list[str] = []
    real_execute = psycopg.ServerCursor.execute

    def _as_text(query: Any) -> str:
        if isinstance(query, str):
            return query
        as_string = getattr(query, "as_string", None)
        return str(as_string(None)) if callable(as_string) else str(query)

    def _count(self: Any, query: Any, *args: Any, **kwargs: Any) -> Any:
        text = _as_text(query)
        if "canonical_lines" in text:
            statements.append(text)
        return real_execute(self, query, *args, **kwargs)

    with patch.object(psycopg.ServerCursor, "execute", _count):
        run_calculation(
            conn, run_id=full_run_id, customer_id=customer_id, engine=ReferenceRebateEngine()
        )

    assert len(statements) == 1, f"expected exactly one canonical_lines read, got {statements}"
    assert "order by source_row_number" in statements[0]


# --- synthetic, targeted scenarios ---------------------------------------------------------------


def _single_program_setup(customer_id: str, *, brand: str = "Acme Brand") -> tuple[str, str]:
    """A run, a reference row and one case-volume program for one brand. Returns (run_id,
    program_id)."""
    make_reference(customer_id, brand, brand_values=[brand])
    program_id = make_program(
        customer_id, name="Acme program", brand_manufacturer=brand, rate="1.000000"
    )
    run_id = make_ingest_run(customer_id)
    return run_id, program_id


def test_a_program_whose_brand_matches_no_line_is_no_lines_matched(
    synthetic_customer: str, conn: psycopg.Connection[Any]
) -> None:
    customer_id = synthetic_customer
    run_id, _program_id = _single_program_setup(customer_id, brand="Acme Brand")
    # Lines for a *different*, known brand - so the reference list is loaded but this program's
    # brand matches zero of them.
    make_reference(customer_id, "Other Brand", brand_values=["Other Brand"])
    insert_canonical_lines(
        [canonical_line(run_id, brand_manufacturer="Other Brand", uom="CS", qty="5")]
    )

    run_calculation(conn, run_id=run_id, customer_id=customer_id, engine=ReferenceRebateEngine())
    conn.commit()

    [attempt] = _attempts(conn, run_id)
    assert attempt["outcome"] == "no_lines_matched"
    assert attempt["reason"] is not None and "Acme Brand" in attempt["reason"]
    assert attempt["lines_matched"] == 0
    assert _calculated_at(conn, run_id) is not None  # still "computed", per the plan's "the trap"


def test_a_program_whose_effective_window_excludes_the_whole_file_is_out_of_effective_range(
    synthetic_customer: str, conn: psycopg.Connection[Any]
) -> None:
    customer_id = synthetic_customer
    make_reference(customer_id, "Acme Brand", brand_values=["Acme Brand"])
    make_program(
        customer_id,
        name="Acme program",
        brand_manufacturer="Acme Brand",
        rate="1.000000",
        effective_from=date(2025, 1, 1),
        effective_to=date(2025, 12, 31),  # a year before every line below
    )
    run_id = make_ingest_run(customer_id)
    insert_canonical_lines(
        [
            canonical_line(
                run_id,
                brand_manufacturer="Acme Brand",
                uom="CS",
                qty="5",
                invoice_date=date(2026, 3, 10),
            )
        ]
    )

    run_calculation(conn, run_id=run_id, customer_id=customer_id, engine=ReferenceRebateEngine())
    conn.commit()

    [attempt] = _attempts(conn, run_id)
    assert attempt["outcome"] == "out_of_effective_range"
    assert attempt["reason"] is not None and "2025-01-01" in attempt["reason"]


def test_an_effective_to_that_clips_part_of_the_file_zeroes_only_the_later_months(
    synthetic_customer: str, conn: psycopg.Connection[Any]
) -> None:
    """A program effective through the end of February, over a Jan-March file: Jan and Feb
    contribute, March's `rebate_calcs` row still exists (the skeleton is written for every month
    in the run's span regardless of any one program's window) but at zero - "computed and found
    nothing" is a row, not an absence."""
    customer_id = synthetic_customer
    run_id, program_id = _single_program_setup(customer_id, brand="Acme Brand")
    with psycopg.connect(APP_DB_URL, autocommit=True) as admin:
        admin.execute(
            "update rebate_programs set effective_to = %s where id = %s",
            (date(2026, 2, 28), program_id),
        )
    insert_canonical_lines(
        [
            canonical_line(
                run_id,
                brand_manufacturer="Acme Brand",
                uom="CS",
                qty="5",
                invoice_date=date(2026, 1, 10),
            ),
            canonical_line(
                run_id,
                brand_manufacturer="Acme Brand",
                uom="CS",
                qty="7",
                invoice_date=date(2026, 2, 10),
            ),
            canonical_line(
                run_id,
                brand_manufacturer="Acme Brand",
                uom="CS",
                qty="9",
                invoice_date=date(2026, 3, 10),
            ),
        ]
    )

    run_calculation(conn, run_id=run_id, customer_id=customer_id, engine=ReferenceRebateEngine())
    conn.commit()

    calcs = {row["period_start"]: row for row in _calcs(conn, run_id)}
    assert len(calcs) == 3
    assert calcs[date(2026, 1, 1)]["amount"] == Decimal("5.00")
    assert calcs[date(2026, 2, 1)]["amount"] == Decimal("7.00")
    march = calcs[date(2026, 3, 1)]
    assert march["amount"] == Decimal("0.00")
    assert march["contributed_line_count"] == 0
    assert march["basis_qty"] == Decimal("0.0000")

    [attempt] = _attempts(conn, run_id)
    assert attempt["outcome"] == "calculated"  # it matched *some* lines, just not every month


def test_a_period_that_nets_negative_is_stored_and_served_correctly(
    synthetic_customer: str, conn: psycopg.Connection[Any]
) -> None:
    """548 of the 48,150 seeded lines are credits (negative qty); this builds the sharper case
    where credits *exceed* sales within one period, so the period's own total goes negative -
    the scenario the demo file never exercises end to end."""
    customer_id = synthetic_customer
    run_id, _program_id = _single_program_setup(customer_id, brand="Acme Brand")
    insert_canonical_lines(
        [
            canonical_line(run_id, brand_manufacturer="Acme Brand", uom="CS", qty="10"),
            canonical_line(run_id, brand_manufacturer="Acme Brand", uom="CS", qty="-30"),
        ]
    )

    run_calculation(conn, run_id=run_id, customer_id=customer_id, engine=ReferenceRebateEngine())
    conn.commit()

    [calc] = _calcs(conn, run_id)
    assert calc["amount"] == Decimal("-20.00")
    assert calc["basis_qty"] == Decimal("-20.0000")
    assert calc["contributed_line_count"] == 2

    # And the amount-equals-sum-of-lines invariant holds for a negative total too.
    with conn.cursor() as cur:
        cur.execute(
            "select coalesce(sum(amount_contribution), 0) from rebate_calc_lines "
            "where rebate_calc_id = %s",
            (calc["rebate_calc_id"],),
        )
        row = cur.fetchone()
    assert row is not None
    assert Decimal(row[0]) == Decimal("-20.00")


def test_max_calc_lines_refuses_an_oversized_run_and_writes_nothing(
    synthetic_customer: str, conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_module, "MAX_CALC_LINES", 3)
    customer_id = synthetic_customer
    run_id, _program_id = _single_program_setup(customer_id, brand="Acme Brand")
    # 4 lines x 1 program = 4 rows, over the patched cap of 3.
    insert_canonical_lines(
        [
            canonical_line(run_id, brand_manufacturer="Acme Brand", uom="CS", qty="1")
            for _ in range(4)
        ]
    )

    with pytest.raises(CalcTooLargeError) as excinfo:
        run_calculation(
            conn, run_id=run_id, customer_id=customer_id, engine=ReferenceRebateEngine()
        )
    assert "3" in str(excinfo.value)
    conn.rollback()

    assert _calcs(conn, run_id) == []
    assert _attempts(conn, run_id) == []
    assert _calc_lines_count(conn, run_id) == 0


class _RaisesOnBatch:
    """Wraps a real engine, raising on the Nth call to `.calculate()` - simulating a worker
    killed (or a dependency failing) mid-pass without needing 48,150 rows to get multiple
    batches."""

    engine_name = "raises-on-batch"
    engine_version = "0.0.1"

    def __init__(self, raise_on_call: int) -> None:
        self._raise_on_call = raise_on_call
        self._calls = 0
        self._real = ReferenceRebateEngine()

    def calculate(self, lines: Any, programs: Any) -> Any:
        self._calls += 1
        if self._calls == self._raise_on_call:
            raise RuntimeError("simulated engine failure mid-batch")
        return self._real.calculate(lines, programs)


def test_a_mid_batch_engine_failure_leaves_zero_durable_rows_and_errored_attempts(
    synthetic_customer: str, conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_module, "ENGINE_BATCH_ROWS", 5)
    customer_id = synthetic_customer
    run_id, _program_id = _single_program_setup(customer_id, brand="Acme Brand")
    # 12 lines at a batch size of 5 -> 3 batches (5, 5, 2); fail on the 2nd.
    insert_canonical_lines(
        [
            canonical_line(run_id, brand_manufacturer="Acme Brand", uom="CS", qty="1")
            for _ in range(12)
        ]
    )

    engine = _RaisesOnBatch(raise_on_call=2)
    with pytest.raises(RuntimeError, match="simulated engine failure"):
        run_calculation(conn, run_id=run_id, customer_id=customer_id, engine=engine)
    conn.rollback()

    # A *second* connection, because `_record_failure` commits on its own connection-local
    # transaction inside `run_calculation` - reading back through the same `conn` after a
    # client-side rollback would only prove the client's own view, not the database's.
    with psycopg.connect(APP_DB_URL, autocommit=True) as verify:
        assert _calcs(verify, run_id) == []
        assert _calc_lines_count(verify, run_id) == 0
        attempts = _attempts(verify, run_id)
        assert len(attempts) == 1
        assert attempts[0]["outcome"] == "errored"
        assert attempts[0]["periods_written"] == 0
        assert _calculated_at(verify, run_id) is None


def test_a_failed_recalculation_does_not_leave_the_previous_good_run_standing(
    synthetic_customer: str, conn: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression `docs/decisions.md` records: a failed *recalculation* must clear the prior
    successful run's results, not leave them standing beside new errored attempts. Verified
    independently here against a fresh synthetic run, not taking the decision log's word for it."""
    customer_id = synthetic_customer
    run_id, _program_id = _single_program_setup(customer_id, brand="Acme Brand")
    insert_canonical_lines(
        [canonical_line(run_id, brand_manufacturer="Acme Brand", uom="CS", qty="10")]
    )

    # First pass: succeeds.
    run_calculation(conn, run_id=run_id, customer_id=customer_id, engine=ReferenceRebateEngine())
    conn.commit()
    with psycopg.connect(APP_DB_URL, autocommit=True) as verify:
        assert len(_calcs(verify, run_id)) == 1
        assert _calc_lines_count(verify, run_id) == 1
        assert _calculated_at(verify, run_id) is not None

    # Second pass over the *same run*: fails on the only batch.
    monkeypatch.setattr(run_module, "ENGINE_BATCH_ROWS", 1)
    failing_engine = _RaisesOnBatch(raise_on_call=1)
    with pytest.raises(RuntimeError, match="simulated engine failure"):
        run_calculation(conn, run_id=run_id, customer_id=customer_id, engine=failing_engine)
    conn.rollback()

    with psycopg.connect(APP_DB_URL, autocommit=True) as verify:
        assert _calcs(verify, run_id) == [], "the previous good calc rows must not survive"
        assert _calc_lines_count(verify, run_id) == 0, "the previous good lines must not survive"
        attempts = _attempts(verify, run_id)
        assert len(attempts) == 1
        assert attempts[0]["outcome"] == "errored"
        assert _calculated_at(verify, run_id) is None, (
            "calculated_at must go back to null - a run cannot claim a total and an errored "
            "attempt for the same program at the same time"
        )
