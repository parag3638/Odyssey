"""Helpers for tests that run against the live Compose stack on :8000 and :5432.

A module rather than a conftest fixture set because these are plain functions a test
calls, not state pytest needs to manage - and a module the next live suite imports is
how the third copy of `_post_upload` gets avoided. `test_uploads_and_runs_routes.py`
predates this and still carries its own copies; it is left alone deliberately, because
rewriting a live suite's scaffolding is a change to the thing doing the measuring.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any, Final

import httpx
import psycopg
import pytest

from src.lib.ids import uuid7

API_BASE = "http://localhost:8000"
APP_DB_URL = "postgresql://speedy:speedy@127.0.0.1:5432/speedy_onboard"
ADMIN_DB_URL = "postgresql://speedy_admin:speedy@127.0.0.1:5432/speedy_onboard"

#: The committed demo customer's slug (`src/features/demo/service.py::DEMO_SLUG`). Every live
#: suite that reads the seeded demo names this same literal rather than importing the app
#: constant, by the existing convention in this test directory (`test_variance_routes_contract.py`
#: etc.) - a live black-box suite names the contract, not the implementation.
DEMO_SLUG: Final = "demo-distributor"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "backend" / "fixtures" / "sample"
SAMPLE_CSV = FIXTURES / "month1_invoices.csv"
SAMPLE_FWF = FIXTURES / "month1_invoices.txt"


def db(url: str) -> psycopg.Connection[tuple[Any, ...]]:
    """`psycopg.connect`, autocommit on, with an explicit row type for strict pyright."""
    return psycopg.connect(url, autocommit=True)


def compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True, timeout=10
    )
    return result.returncode == 0


@pytest.fixture
def live_api() -> None:
    """Skip, not fail, when the stack is not up: the suite must run clean without it."""
    if not compose_available():
        pytest.skip("docker compose is not available in this environment")
    try:
        httpx.get(f"{API_BASE}/healthz", timeout=2)
    except httpx.HTTPError:
        pytest.skip("API is not reachable on :8000 - run `docker compose up -d`")


@pytest.fixture
def customer(live_api: None) -> Generator[str]:
    """A customer row via the admin role, self-deleting (cascades to runs and files)."""
    del live_api
    customer_id = str(uuid7())
    with db(ADMIN_DB_URL) as conn:
        conn.execute(
            "insert into customers (id, name, slug) values (%s, %s, %s)",
            (customer_id, "Layout Test Co", f"layout-test-{uuid.uuid4().hex[:8]}"),
        )
    try:
        yield customer_id
    finally:
        with db(ADMIN_DB_URL) as conn:
            conn.execute("delete from customers where id = %s", (customer_id,))


def reset_rate_limits() -> None:
    """Give this test a full budget. Every test shares one source IP, and the table
    persists between runs, so without this an earlier run eats a later test's window."""
    with db(ADMIN_DB_URL) as conn:
        conn.execute("delete from rate_limits")


#: Any status the seeded month-2 run (`month2_invoices.csv`, full scale) can land at once
#: `finish_demo_seed` (`src/jobs/tasks.py`) has actually parsed it, as opposed to the run not
#: existing yet. The job parses month 2 **last**, synchronously, in the same call that already
#: drove month 1 through materialisation, readiness, calculation, variance computation and
#: attribution - so this run reaching any of these is proof the whole pipeline ahead of it has
#: already committed, not just that this one file settled. `drift_blocked` is the status the demo
#: is supposed to land on (month 2 drifts against month 1's approved spec); `profiled` and
#: `failed` are included so a genuine defect in drift detection shows up as a wrong *value* in a
#: later assertion rather than this helper hanging for the full budget first.
_MONTH2_TERMINAL_STATUSES: Final = frozenset({"drift_blocked", "profiled", "mapped", "failed"})

#: Measured settle time for a full reset after the 2026-10-12 attribution fix (docs/decisions.md)
#: is 10-13s. 90s is generous against that without being so long that a genuinely wedged reset
#: (the exact failure `tests/test_demo_reset_lock_bug_report.py` guards against) burns the whole
#: test session before this helper gives up and says so.
_DEMO_SEED_BUDGET_S: Final = 90.0


def _demo_state() -> dict[str, Any] | None:
    try:
        resp = httpx.get(f"{API_BASE}/v1/demo/state", timeout=5)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    data: dict[str, Any] = resp.json()["data"]
    return data


def _demo_customer_id() -> str | None:
    """The demo customer's current row id, or `None` if it does not exist right now.

    `repo.clear()` (`src/features/demo/repo.py::_DELETE_CUSTOMER`) really deletes the row by
    slug, and `seed()`'s insert gives the replacement a fresh `uuid7()` - it is never the same
    row reused in place. That is what makes "the id changed" a reliable signal that a *new*
    reset actually ran, as opposed to this call simply observing the previous reset's leftover
    state (see `ensure_demo_seeded`'s docstring for why that distinction is the whole point).
    """
    with db(ADMIN_DB_URL) as conn:
        row = conn.execute("select id from customers where slug = %s", (DEMO_SLUG,)).fetchone()
    return None if row is None else str(row[0])


def _month2_run_status(customer_id: str) -> str | None:
    with db(ADMIN_DB_URL) as conn:
        row = conn.execute(
            """
            select r.status
            from ingest_runs r
            join source_files f on f.id = r.source_file_id
            where r.customer_id = %s and f.original_filename = 'month2_invoices.csv'
            order by r.created_at desc
            limit 1
            """,
            (customer_id,),
        ).fetchone()
    return None if row is None else str(row[0])


def ensure_demo_seeded(budget_s: float = _DEMO_SEED_BUDGET_S) -> None:
    """Make the seeded demo (`demo-distributor`) exist and be *fully* processed, regardless of
    what an earlier test in this session did to it - rather than assuming a previous `make demo`
    or test run left it behind.

    **Why this is needed at all**: `tests/test_migrations.py` runs `alembic downgrade base`
    against this same live database and restores `head` afterward, which recreates every table
    empty - the demo customer along with everything else. Default collection order happens to put
    that file ahead of the suites that read the demo (`test_migrations` sorts before
    `test_readiness_*` / `test_rebate_*` / `test_variance_routes_contract` alphabetically), but a
    `-k` selection or running one of those files alone bypasses that ordering entirely, so a test
    that wants the demo has to rebuild it rather than hope collection order protected it.

    **Why this polls the month-2 run rather than `demo/state`'s `seeded` flag**: `run_reset`
    marks the seed row complete right after `seed()` *enqueues* the pipeline's jobs, not after
    they finish (`src/features/demo/service.py::run_reset`) - so `seeded: true` can be true while
    materialisation, calculation, variance computation and attribution are still running on the
    worker. The month-2 run is parsed last inside `finish_demo_seed`, after all of that has
    already committed, which is what makes it a trustworthy "the whole thing is done" signal
    *for a given reset*.

    **Why this also tracks the customer's row id, not just the month-2 status**: on a demo that
    was already fully seeded by an earlier test session, the very first poll after `POST
    /v1/demo/reset` returns can observe the *previous* seed's leftover `drift_blocked` run before
    the worker has even started `repo.clear()` - a real race, measured directly: a second call to
    this function returned in under a second, handed back the *old* run id, and the test using it
    got a `ForeignKeyViolation` moments later when the new reset's clear actually ran underneath
    it (`test_rerunning_the_full_demo_month_is_byte_identical_and_under_15_seconds` in
    `test_rebate_calculation_job.py` is the regression test for this). `repo.clear()` really
    deletes the customer row by slug and `seed()`'s insert gives the rebuild a fresh id, so
    requiring the id to have *changed* - not merely the month-2 run to look terminal - is what
    tells "this reset finished" apart from "the previous one already had".

    Raises (does not skip) if the reset never settles within `budget_s` - a reset that starts and
    never finishes is the real defect the P15/P16 bug reports in this directory are about, and a
    test that swallowed that as a skip would hide it again.
    """
    state = _demo_state()
    if state is None:
        pytest.skip("API is not reachable on :8000 - run `docker compose up -d`")
    if not state.get("resetEnabled", False):
        pytest.skip("demo reset is disabled on this instance (DEMO_RESET_ENABLED=false)")

    previous_customer_id = _demo_customer_id()

    # Every live suite in this session shares one source IP and the same persistent `rate_limits`
    # table, so without this an earlier test file's own reset (or its own verification against
    # the reset route) could leave this one with no budget left to work with.
    reset_rate_limits()

    resp = httpx.post(f"{API_BASE}/v1/demo/reset", timeout=10)
    if resp.status_code == 403:
        pytest.skip("demo reset is disabled on this instance (DEMO_RESET_ENABLED=false)")
    if resp.status_code != 409:  # 409 = a reset is already running; fall through and wait for it
        assert resp.status_code == 202, resp.text

    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        current_customer_id = _demo_customer_id()
        if (
            current_customer_id is not None
            and current_customer_id != previous_customer_id
            and _month2_run_status(current_customer_id) in _MONTH2_TERMINAL_STATUSES
        ):
            return
        time.sleep(0.5)
    raise AssertionError(
        f"demo did not finish seeding within {budget_s}s (customer id was "
        f"{previous_customer_id!r} before the reset, is now {_demo_customer_id()!r})"
    )


#: **The `seeded_demo` pytest fixture itself lives in `tests/conftest.py`, not here**, even
#: though `ensure_demo_seeded` above is the function it calls. Measured the hard way: a
#: session-scoped fixture defined in this module and imported (`from tests.live import
#: seeded_demo`) into several test files does **not** execute once per session - pytest's
#: `parsefactories` registers a distinct `FixtureDef` per importing module, each with its own
#: `cached_result`, so a "session-scoped" fixture reached through an import re-runs once per
#: file that imports it. Running `test_readiness_one_pass_speed_adversarial.py`,
#: `test_rebate_calculation_job.py`, `test_rebate_engine_matches_oracle.py` and
#: `test_variance_routes_contract.py` together produced *three* extra `POST /v1/demo/reset`
#: calls partway through the session (visible in `procrastinate_jobs`/`procrastinate_events`:
#: `reset_demo` jobs firing every ~9-10s rather than once), and the test that happened to be
#: mid-flight against the run a previous file had found got a `ForeignKeyViolation` when a later
#: file's "session-scoped" reset deleted it out from under it -
#: `test_rerunning_the_full_demo_month_is_byte_identical_and_under_15_seconds` is the regression
#: test for this, in `test_rebate_calculation_job.py`. A fixture only gets pytest's "one real
#: `FixtureDef`, cached for the whole session no matter who asks" promise when it is discovered
#: from `conftest.py` directly, which is why `seeded_demo` is defined there and this module keeps
#: only the plain function it wraps.


def post_upload(
    file_path: Path, *, customer_id: str, kind: str = "erp_invoice_lines"
) -> httpx.Response:
    with file_path.open("rb") as handle:
        return httpx.post(
            f"{API_BASE}/v1/uploads",
            data={"customerId": customer_id, "kind": kind},
            files={"file": (file_path.name, handle, "text/plain")},
            timeout=60,
        )


def upload_and_settle(file_path: Path, *, customer_id: str, budget_s: float = 60) -> dict[str, Any]:
    """Upload, then poll until the run settles. Returns the final run payload.

    Polls `isSettled` and never a status list: the server owns that vocabulary
    (`features/runs/schemas.py`), and a test that restates it goes stale silently the
    next time the pipeline grows a stage - which is exactly what happened once already.
    """
    reset_rate_limits()
    response = post_upload(file_path, customer_id=customer_id)
    assert response.status_code == 202, response.text
    run_id = response.json()["data"]["ingestRunId"]

    deadline = time.monotonic() + budget_s
    run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        run = httpx.get(f"{API_BASE}/v1/ingest-runs/{run_id}", timeout=10).json()["data"]
        if run["isSettled"]:
            return run
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} did not settle within {budget_s}s: {run}")


def get_layout(file_id: str, *, version: int | None = None) -> httpx.Response:
    params = {} if version is None else {"version": version}
    return httpx.get(f"{API_BASE}/v1/source-files/{file_id}/layout", params=params, timeout=10)
