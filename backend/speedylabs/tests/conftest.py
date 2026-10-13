"""Shared test setup.

`src/main.py` builds the FastAPI app at *module* scope (`app = create_app()`), and
`create_app()` calls `config.load()`, which is `lru_cache`d. That means every variable
`Settings` requires with no default must exist in the environment before the *first*
test module imports `src.main` or `src.config` anywhere in this session - so they're
set here, in `pytest_configure`, which runs before collection. As of step 0.3 that is
four variables, not two: `DATABASE_URL`, `MIGRATION_DATABASE_URL`, `IP_HASH_SALT`
(min length 8) and `TRUSTED_PROXY_HOPS` (int >= 0).

The two database URLs point at an address nothing is listening on by default. From the
app's point of view that is indistinguishable from "Postgres stopped" - the exact case
the /readyz acceptance criterion (mvp.md step 0.1, #4) describes - so every test that
needs a "database is down" scenario reuses this default rather than mocking one up.

Docker is now installed and `docker compose up -d db` is expected to be running
(step 0.3 landed Postgres 16 + the `speedy`/`speedy_migrator` roles). Tests that need
the real database import `LIVE_DATABASE_URL`/`LIVE_MIGRATOR_DATABASE_URL` below and use
the `require_live_db` fixture, which skips with a clear reason rather than failing when
nothing is listening on 5432 - the suite must still run clean on a machine that hasn't
started Compose.

Tests that need the *absence* of a required variable (config exit-code tests) must not
rely on this file's defaults at all - they run `config.load()` in a subprocess with
their own fully-controlled environment, so a `SystemExit` there can never take down the
test runner.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from src import config
from src.lib import db, llm

DEAD_DATABASE_URL = "postgresql+psycopg://nobody:nobody@127.0.0.1:59999/nothing"

# The local topology documented in `.env.example` / `docker-compose.yml`: Postgres 16
# on the host's 5432, with the `speedy` (no DDL) and `speedy_migrator` (owns the
# schema) roles that `backend/sql/roles.sql` creates. Fake values nowhere here - these
# are the committed, non-secret local-dev defaults.
LIVE_DATABASE_URL = "postgresql+psycopg://speedy:speedy@127.0.0.1:5432/speedy_onboard"
LIVE_MIGRATOR_DATABASE_URL = (
    "postgresql+psycopg://speedy_migrator:speedy@127.0.0.1:5432/speedy_onboard"
)


def pytest_configure(config: pytest.Config) -> None:
    del config
    os.environ.setdefault("DATABASE_URL", DEAD_DATABASE_URL)
    os.environ.setdefault("MIGRATION_DATABASE_URL", DEAD_DATABASE_URL)
    os.environ.setdefault("IP_HASH_SALT", "test-salt-not-for-production")
    os.environ.setdefault("TRUSTED_PROXY_HOPS", "0")


def _live_postgres_is_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5432), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture
def require_live_db() -> None:
    """Skip (not fail) a test that needs the real Postgres from `docker compose up -d db`.

    A bare TCP connect, not a full login - good enough to tell "nothing is listening"
    from "something is", which is all a skip decision needs.
    """
    if not _live_postgres_is_reachable():
        pytest.skip("no Postgres listening on 127.0.0.1:5432 - run `docker compose up -d db`")


@pytest.fixture(scope="session")
def seeded_demo() -> None:
    """Establishes the fully-processed demo (`demo-distributor`) once per test session; tests
    that need it depend on this instead of assuming a previous run left it behind (see
    `tests.live.ensure_demo_seeded` for why that assumption fails in this suite - in short,
    `test_migrations.py`'s `alembic downgrade base` wipes it, and collection order is not a
    guarantee).

    **Defined here, in `conftest.py`, rather than in `tests/live.py` - this is load-bearing, not
    a style choice.** A session-scoped fixture only gets pytest's "exactly one real `FixtureDef`,
    cached once for the whole session no matter which test asked for it" guarantee when pytest
    discovers it directly from a `conftest.py`. Defining it in `tests/live.py` and importing it
    into several test modules (`from tests.live import seeded_demo`) was tried first and measured
    to **not** behave that way: `parsefactories` registers a separate `FixtureDef` - with its own
    independent cache - per module that imports it, so a "session-scoped" fixture reached through
    an import re-runs once per importing file instead of once per session. With four live test
    files importing it, that produced three extra `POST /v1/demo/reset` calls partway through one
    `pytest` invocation (confirmed in `procrastinate_jobs`/`procrastinate_events`: `reset_demo`
    jobs firing every ~9-10s instead of once), and a test that had already looked up a run id
    from an earlier file's reset got a `ForeignKeyViolation` when a later file's "own" reset
    deleted it mid-test. `tests/rebate_fixtures.py::synthetic_customer` and the other
    function-scoped fixtures this suite imports across files do not have this problem - only
    scopes broader than "function" do, because only they rely on cross-call caching at all.

    Session-scoped (not module- or function-scoped) because every test that needs this fixture
    wants the *same* seeded state (the committed eighteen variance rows, the full-month
    calculated run, the drift-blocked month-2 run) and a reset costs ~10-13s - paying that once
    per session, not once per module or per test, is the point of this fixture existing. Do not
    combine a test using this fixture with one that itself calls `POST /v1/demo/reset` or
    otherwise mutates the demo customer in the same session: a later test would then see that
    mutation instead of the state this fixture established.
    """
    import httpx

    from tests.live import API_BASE, compose_available, ensure_demo_seeded

    if not compose_available():
        pytest.skip("docker compose is not available in this environment")
    try:
        httpx.get(f"{API_BASE}/healthz", timeout=2)
    except httpx.HTTPError:
        pytest.skip("API is not reachable on :8000 - run `docker compose up -d`")
    ensure_demo_seeded()


@pytest.fixture(autouse=True)
def _reset_db_engine_state() -> Iterator[None]:
    """Isolate `src.lib.db`'s process-wide engine/sessionmaker across tests.

    Some tests trigger the real app lifespan (`with TestClient(app) as client:`),
    which calls `db.init()`; others call `db.init()`/`db.session()` directly to probe
    the module in isolation, sometimes pre-empting the lifespan's call on purpose (to
    point the app at a real database without touching the cached `Settings`
    singleton). Without this, whichever engine a test created would leak into the
    next test, and a healthz test asserting "no engine was ever created" would depend
    on run order.
    """
    db._engine = None  # pyright: ignore[reportPrivateUsage]
    db._sessionmaker = None  # pyright: ignore[reportPrivateUsage]
    yield
    if db._engine is not None:  # pyright: ignore[reportPrivateUsage]
        asyncio.run(db.dispose())
    db._engine = None  # pyright: ignore[reportPrivateUsage]
    db._sessionmaker = None  # pyright: ignore[reportPrivateUsage]


@pytest.fixture(autouse=True)
def _no_paid_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in the default suite may reach OpenAI, whatever `.env` holds.

    `.env` now carries a real, paid `OPENAI_API_KEY`. Three tests written for "no model
    configured" silently became live calls (about $0.16 in one `make check`) because that
    assumption lived in a docstring, not in the test. This is the backstop: `llm._client` is
    the one place an OpenAI client is built, so making it refuse turns "a test would spend money"
    into "a test fails with this message". Tests that exercise the client machinery already patch
    `_client` themselves (with a fake or with an explicit local `base_url`), and a monkeypatch
    applied inside the test body wins over this one, so none of them are affected.

    A test that *means* "unconfigured" should not rely on this - it should use
    `unconfigured_llm` below, which makes the settings themselves say so.
    """

    def refuse(api_key: str, timeout_seconds: int) -> object:
        del api_key, timeout_seconds
        raise AssertionError(
            "this test reached for a real OpenAI client. The default suite never makes a paid "
            "call: stub `llm.parse`, patch `llm._client`, or use the `unconfigured_llm` fixture."
        )

    monkeypatch.setattr(llm, "_client", refuse)


@pytest.fixture
def unconfigured_llm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Settings in which no model is configured, regardless of `.env` or the shell.

    `config.load()` is `lru_cache`d and `Settings` reads `./.env`, so forcing "unconfigured"
    takes three things together: remove the variables from the environment, run from a
    directory with no `.env` (a host-run pytest from the repo root would otherwise read the real
    one), and drop the cached singleton - before the test, so it is rebuilt without the key, and
    after, so the next test does not inherit this one's settings.

    `llm.parse` is also replaced by a tripwire: if the settings were somehow still enabled,
    the test fails here instead of spending money.
    """
    monkeypatch.chdir(tmp_path)
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_PRICE_INPUT_PER_MTOK",
        "OPENAI_PRICE_CACHED_INPUT_PER_MTOK",
        "OPENAI_PRICE_OUTPUT_PER_MTOK",
    ):
        monkeypatch.delenv(name, raising=False)
    config.load.cache_clear()
    assert not config.load().llm_enabled, "the fixture failed to unconfigure the model"

    def tripwire(**_: object) -> object:
        raise AssertionError("an unconfigured-model test called llm.parse")

    monkeypatch.setattr(llm, "parse", tripwire)
    yield
    config.load.cache_clear()
