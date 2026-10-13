"""The `speedy` / `speedy_migrator` role split against a real Postgres (step 0.3
acceptance criterion: "Given the `speedy` app role, when it attempts `CREATE TABLE`,
then Postgres refuses with insufficient privilege").

BACKEND.md §4: "The app's DB user has only the rights it needs: no superuser, no
schema changes. Migrations run as a separate user." `backend/sql/roles.sql` is what
enforces that at the database level - these tests exercise the live roles it creates,
not the SQL file's text, because a grant that looks right on paper and a grant that
Postgres actually enforces are not the same claim.

Uses `psycopg` directly (already a project dependency, no new one added) rather than
`src.lib.db`'s async engine - a privilege check is a one-shot synchronous query, and
pulling in an event loop for it would test asyncio, not Postgres.

Requires `docker compose up -d db` (skips with a clear reason otherwise). Every table
created here is dropped in the same test - step 0.4's first migration still needs a
schema with nothing in it.
"""

from __future__ import annotations

import psycopg
import pytest

APP_URL = "postgresql://speedy:speedy@127.0.0.1:5432/speedy_onboard"
MIGRATOR_URL = "postgresql://speedy_migrator:speedy@127.0.0.1:5432/speedy_onboard"


def test_app_role_is_refused_create_table_with_insufficient_privilege(
    require_live_db: None,
) -> None:
    del require_live_db
    with (
        psycopg.connect(APP_URL, connect_timeout=5, autocommit=True) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute("create table t_tester_privilege_probe (id int)")


def test_app_role_is_not_a_superuser_and_cannot_create_roles_or_databases(
    require_live_db: None,
) -> None:
    del require_live_db
    with psycopg.connect(APP_URL, connect_timeout=5, autocommit=True) as conn:
        row = conn.execute(
            "select rolsuper, rolcreatedb, rolcreaterole from pg_roles where rolname = 'speedy'"
        ).fetchone()

    assert row == (False, False, False)


def test_migrator_role_can_create_and_drop_a_table(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(MIGRATOR_URL, connect_timeout=5, autocommit=True) as conn:
        conn.execute("create table t_tester_migrator_probe (id int)")
        conn.execute("drop table t_tester_migrator_probe")


def test_migrator_role_is_also_not_a_superuser(require_live_db: None) -> None:
    del require_live_db
    with psycopg.connect(MIGRATOR_URL, connect_timeout=5, autocommit=True) as conn:
        row = conn.execute(
            "select rolsuper from pg_roles where rolname = 'speedy_migrator'"
        ).fetchone()

    assert row == (False,)


def test_app_role_can_use_a_table_the_migrator_creates(require_live_db: None) -> None:
    """The point of the split: the migrator owns the schema, the app role can read and
    write what it creates. `alter default privileges` in roles.sql is what makes this
    automatic rather than a manual grant per table."""
    del require_live_db
    with psycopg.connect(MIGRATOR_URL, connect_timeout=5, autocommit=True) as conn:
        conn.execute("create table t_tester_shared_probe (id int)")
    try:
        with psycopg.connect(APP_URL, connect_timeout=5, autocommit=True) as conn:
            conn.execute("insert into t_tester_shared_probe (id) values (1)")
            row = conn.execute("select id from t_tester_shared_probe").fetchone()
        assert row == (1,)
    finally:
        with psycopg.connect(MIGRATOR_URL, connect_timeout=5, autocommit=True) as conn:
            conn.execute("drop table t_tester_shared_probe")
