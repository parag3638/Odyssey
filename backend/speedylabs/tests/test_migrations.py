"""Alembic migrations (step 0.4) against the real Postgres from `docker compose up -d
db`. `alembic` is driven as a subprocess, the same way an operator or CI would.

Every test that moves the schema (`downgrade`/`upgrade`) restores `head` in a
`finally` block before returning, pass or fail, so other test files in this session
that assume the schema already exists (`test_schema_constraints.py`, `test_jobs.py`)
are never surprised by test order or by a left-over `downgrade base`.

Found while writing these, worth a line of its own: if Compose's long-running
`worker` service is up, dropping and recreating procrastinate's functions out from
under its already-open connection (exactly what `downgrade base` does) crashes it -
`function procrastinate_fetch_job_v2(...) does not exist`, then the worker container
exits. That is not a bug in the migration (Postgres plan/OID caching across a DDL
change on a long-lived session is a known class of issue, not specific to this
schema), but it is a real operational edge worth knowing about independently of
these tests: a migration that touches procrastinate's schema while a worker is live
should be followed by restarting that worker. `_ensure_head()` below does exactly
that - restart the worker, not just the schema - after every test that moves the
schema, so this suite doesn't leave the Compose stack worse off than it found it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

MIGRATOR_URL = "postgresql://speedy_migrator:speedy@127.0.0.1:5432/speedy_onboard"

APP_SQLALCHEMY_URL = "postgresql+psycopg://speedy:speedy@127.0.0.1:5432/speedy_onboard"
MIGRATOR_SQLALCHEMY_URL = (
    "postgresql+psycopg://speedy_migrator:speedy@127.0.0.1:5432/speedy_onboard"
)

APPLICATION_TABLES = {"customers", "rebate_programs"}
PROCRASTINATE_TABLES = {
    "procrastinate_jobs",
    "procrastinate_events",
    "procrastinate_periodic_defers",
    "procrastinate_workers",
}
ALL_TABLES = APPLICATION_TABLES | PROCRASTINATE_TABLES


def _alembic(
    *args: str, migration_database_url: str = MIGRATOR_SQLALCHEMY_URL
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["DATABASE_URL"] = APP_SQLALCHEMY_URL
    env["MIGRATION_DATABASE_URL"] = migration_database_url
    env.setdefault("IP_HASH_SALT", "0123456789abcdef")
    env.setdefault("TRUSTED_PROXY_HOPS", "0")
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _existing_tables() -> set[str]:
    with psycopg.connect(MIGRATOR_URL, connect_timeout=5, autocommit=True) as conn:
        rows = conn.execute("select tablename from pg_tables where schemaname = 'public'")
        return {row[0] for row in rows.fetchall()}


def _procrastinate_function_and_type_counts() -> tuple[int, int]:
    with psycopg.connect(MIGRATOR_URL, connect_timeout=5, autocommit=True) as conn:
        funcs = conn.execute(
            "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = current_schema() and p.proname like 'procrastinate%'"
        ).fetchone()
        types = conn.execute(
            "select count(*) from pg_type where typname like 'procrastinate%'"
        ).fetchone()
    assert funcs is not None
    assert types is not None
    return funcs[0], types[0]


def _ensure_head() -> None:
    result = _alembic("upgrade", "head")
    assert result.returncode == 0, (
        f"could not restore the schema to head after a test: {result.stderr}"
    )
    # A no-op when Compose isn't managing a `worker` here. When it is, this clears
    # the stale-function crash described above - better to pay for a restart on
    # every destructive test than to leave the stack broken for whatever runs next.
    subprocess.run(
        ["docker", "compose", "restart", "worker"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_upgrade_head_creates_customers_programs_and_the_procrastinate_queue(
    require_live_db: None,
) -> None:
    del require_live_db
    try:
        result = _alembic("upgrade", "head")
        assert result.returncode == 0, result.stderr

        tables = _existing_tables()
        assert tables >= ALL_TABLES, f"missing: {ALL_TABLES - tables}"
    finally:
        _ensure_head()


def test_running_upgrade_head_twice_is_a_no_op_and_exits_0(require_live_db: None) -> None:
    del require_live_db
    try:
        first = _alembic("upgrade", "head")
        assert first.returncode == 0, first.stderr

        second = _alembic("upgrade", "head")
        assert second.returncode == 0, second.stderr
        # Nothing left to run: alembic prints nothing when already at head.
        assert second.stdout.strip() == ""
        assert "Running upgrade" not in second.stderr
    finally:
        _ensure_head()


def test_downgrade_base_removes_customers_programs_and_the_procrastinate_queue(
    require_live_db: None,
) -> None:
    del require_live_db
    try:
        up = _alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr

        down = _alembic("downgrade", "base")
        assert down.returncode == 0, down.stderr

        tables = _existing_tables()
        assert tables & ALL_TABLES == set(), f"still present after downgrade: {tables & ALL_TABLES}"

        funcs, types = _procrastinate_function_and_type_counts()
        assert funcs == 0, "procrastinate functions survived `downgrade base`"
        assert types == 0, "procrastinate types survived `downgrade base`"
    finally:
        _ensure_head()


def test_downgrade_then_upgrade_round_trips_twice_without_drift(require_live_db: None) -> None:
    """Worth attacking beyond a single round trip: does a second cycle land in the same
    place as the first, or does something (a sequence, a default, a leftover type)
    drift between runs?"""
    del require_live_db
    try:
        for cycle in range(2):
            down = _alembic("downgrade", "base")
            assert down.returncode == 0, f"cycle {cycle} downgrade: {down.stderr}"
            assert _existing_tables() & ALL_TABLES == set(), f"cycle {cycle}: not empty"

            up = _alembic("upgrade", "head")
            assert up.returncode == 0, f"cycle {cycle} upgrade: {up.stderr}"
            assert _existing_tables() >= ALL_TABLES, f"cycle {cycle}: tables missing"
    finally:
        _ensure_head()


def test_a_failure_partway_through_the_migration_leaves_no_partial_objects(
    require_live_db: None,
) -> None:
    """Transactionality, forced rather than assumed: `rebate_programs` is created
    *after* `customers` in the same migration (see `upgrade()`), so pre-creating a
    conflicting `rebate_programs` table makes the migration fail on its second
    `create_table`, after the first has already run in the same transaction. If the
    migration is genuinely transactional, `customers` must not survive the failure.
    """
    del require_live_db
    try:
        down = _alembic("downgrade", "base")
        assert down.returncode == 0, down.stderr
        assert _existing_tables() & ALL_TABLES == set()

        with psycopg.connect(MIGRATOR_URL, connect_timeout=5, autocommit=True) as conn:
            conn.execute("create table rebate_programs (id int)")

        try:
            result = _alembic("upgrade", "head")
            assert result.returncode != 0, "expected the migration to fail on the collision"

            tables = _existing_tables()
            assert "customers" not in tables, (
                "customers survived a failed migration - the transaction did not roll back"
            )
        finally:
            with psycopg.connect(MIGRATOR_URL, connect_timeout=5, autocommit=True) as conn:
                conn.execute("drop table if exists rebate_programs")
    finally:
        _ensure_head()


def test_migrating_as_the_app_role_fails_with_a_permission_error(require_live_db: None) -> None:
    """Acceptance criterion: `MIGRATION_DATABASE_URL` pointing at `speedy` (the app
    role, no DDL rights - BACKEND.md §4) must fail loudly, not silently migrate as the
    app user. Exercised from an empty schema, where the first statement is a real
    `CREATE TABLE` - at `head` already, there is nothing to run and the app role's
    read access (granted via `alter default privileges`) would make this a false
    no-op pass instead of a real permission check.
    """
    del require_live_db
    try:
        down = _alembic("downgrade", "base")
        assert down.returncode == 0, down.stderr

        result = _alembic("upgrade", "head", migration_database_url=APP_SQLALCHEMY_URL)

        assert result.returncode != 0
        assert "InsufficientPrivilege" in result.stderr or "permission denied" in result.stderr
        assert _existing_tables() & APPLICATION_TABLES == set(), (
            "a table exists even though the app-role migration should have failed immediately"
        )
    finally:
        _ensure_head()
