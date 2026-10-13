"""Attack item 3: "readiness claims a single streaming read over `canonical_lines` - prove it
by counting statements on the connection - and under 20s for 48,150 rows."

Needs the full 48,150-row month materialised first. Rather than assuming a previous `make demo`
or test run left that behind, `full_run_id` depends on the `seeded_demo` fixture
(`tests/conftest.py`), which resets and fully re-seeds the demo once per test session - see that
fixture's docstring for why an earlier run's leftovers cannot be trusted (`test_migrations.py`'s
`alembic downgrade base` wipes it).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import psycopg
import pytest

from src.features.readiness.run import run_readiness
from tests.live import APP_DB_URL, DEMO_SLUG


def _find_full_month1_run() -> str | None:
    with psycopg.connect(APP_DB_URL, autocommit=True) as conn:
        row = conn.execute(
            """
            select r.id
            from ingest_runs r
            join customers c on c.id = r.customer_id
            join source_files f on f.id = r.source_file_id
            where c.slug = %s and f.original_filename = 'month1_invoices.csv'
              and f.row_count >= 40000
            order by r.created_at desc
            limit 1
            """,
            (DEMO_SLUG,),
        ).fetchone()
    return str(row[0]) if row else None


@pytest.fixture
def full_run_id(seeded_demo: None) -> str:
    del seeded_demo
    run_id = _find_full_month1_run()
    if run_id is None:
        # Should not happen once `seeded_demo` has run without itself skipping or raising - kept
        # as a defensive skip rather than a bare assertion in case the row_count threshold ever
        # drifts from what a fresh seed actually produces.
        pytest.skip(
            "no full (48,150-row) month1 run found for the demo customer even after the "
            "seeded_demo fixture ran - see its docstring in tests/conftest.py"
        )
    return run_id


@pytest.fixture
def live_conn() -> Iterator[psycopg.Connection[Any]]:
    conn = psycopg.connect(APP_DB_URL, autocommit=False)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _row_facts(conn: psycopg.Connection[Any], run_id: str) -> tuple[str, str]:
    with conn.cursor() as cur:
        cur.execute("select source_file_id, customer_id from ingest_runs where id = %s", (run_id,))
        row = cur.fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


def test_readiness_issues_exactly_one_select_against_canonical_lines(
    full_run_id: str, live_conn: psycopg.Connection[Any]
) -> None:
    """Counting statements, not reading the code: wrap every cursor `psycopg` opens on this
    connection and tally how many times a statement mentioning `canonical_lines` is executed.
    `run_readiness` must issue exactly one, via its named (server-side) cursor - eight checks
    sharing one pass, not eight passes."""
    source_file_id, customer_id = _row_facts(live_conn, full_run_id)

    canonical_lines_statements: list[str] = []
    real_cursor_execute = psycopg.Cursor.execute
    real_server_cursor_execute = psycopg.ServerCursor.execute

    def _as_text(query: Any) -> str:
        if isinstance(query, str):
            return query
        as_string = getattr(query, "as_string", None)
        return str(as_string(None)) if callable(as_string) else str(query)

    def _count(self: Any, query: Any, *args: Any, **kwargs: Any) -> Any:
        text = _as_text(query)
        if "canonical_lines" in text:
            canonical_lines_statements.append(text)
        return real_cursor_execute(self, query, *args, **kwargs)

    def _count_server(self: Any, query: Any, *args: Any, **kwargs: Any) -> Any:
        text = _as_text(query)
        if "canonical_lines" in text:
            canonical_lines_statements.append(text)
        return real_server_cursor_execute(self, query, *args, **kwargs)

    with (
        patch.object(psycopg.Cursor, "execute", _count),
        patch.object(psycopg.ServerCursor, "execute", _count_server),
    ):
        run_readiness(
            live_conn,
            run_id=full_run_id,
            source_file_id=source_file_id,
            customer_id=customer_id,
        )

    assert len(canonical_lines_statements) == 1, (
        f"expected exactly one statement touching canonical_lines, got "
        f"{len(canonical_lines_statements)}: {canonical_lines_statements}"
    )
    assert "order by source_row_number" in canonical_lines_statements[0]


def test_readiness_completes_within_20_seconds_on_48150_rows(
    full_run_id: str, live_conn: psycopg.Connection[Any]
) -> None:
    source_file_id, customer_id = _row_facts(live_conn, full_run_id)
    started = time.monotonic()
    outcome = run_readiness(
        live_conn,
        run_id=full_run_id,
        source_file_id=source_file_id,
        customer_id=customer_id,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 20, f"readiness took {elapsed:.2f}s against the budget's 20s (48,150 rows)"
    assert outcome.checks_run == 8
    assert outcome.rows_examined > 0
