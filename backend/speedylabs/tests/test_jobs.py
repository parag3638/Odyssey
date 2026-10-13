"""`src/jobs` (step 0.4): the procrastinate queue, backed by Postgres (decision:
"procrastinate on Postgres, not Celery + Redis").

Everything that touches `src.jobs.app.app` runs as a subprocess with its own live
`DATABASE_URL`, never imported directly into this test process. Two reasons, both
load-bearing, not stylistic:

1. `src.jobs.app` builds `app` at *module* scope from `config.load()`, which is
   `lru_cache`d process-wide. `conftest.py` sets a dead `DATABASE_URL` by default (for
   the 0.1/0.3 "Postgres down" tests), and once any test in this session has imported
   `src.config` or `src.main`, that dead URL is locked into the cached `Settings` for
   the rest of the process - importing `src.jobs.app` here would build its connector
   from that same dead URL, not the live one, regardless of what this test sets in
   `os.environ` afterwards.
2. Even with the right URL, `defer_ping`'s sync `.defer()` call bridges to an async
   connector via `asgiref.sync.AsyncToSync`, which refuses to run inside a thread that
   already has an asyncio event loop active - so it cannot be called from inside
   `asyncio.run(...)` in this same process either.

A subprocess sidesteps both: a fresh process gets a fresh, correctly-live
`config.load()`, and runs the sync defer call with no event loop in the way - exactly
how a real deployment's API process (sync service layer) and worker process (its own
`procrastinate ... worker`, the same command Compose's `worker` service and the
Makefile's `worker` target run) are already separate processes.

One more wrinkle discovered while writing these: if `docker compose up -d` already
has its long-running `worker` service up (the normal state in this environment), it
is *also* watching this same queue via `LISTEN`/`NOTIFY` and will typically pick up a
trivial `ping` job within milliseconds - faster than this test process can defer and
then re-check it. Tests that only care about the job eventually succeeding (the
first) poll rather than assume a fixed race winner; the one test that specifically
needs a job to stay queued (the duplicate-enqueue one) pauses that container for its
duration via `_paused_compose_worker`, a no-op when Compose isn't managing one here.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Generator
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

LIVE_DATABASE_URL = "postgresql+psycopg://speedy:speedy@127.0.0.1:5432/speedy_onboard"
LIVE_MIGRATION_DATABASE_URL = (
    "postgresql+psycopg://speedy_migrator:speedy@127.0.0.1:5432/speedy_onboard"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["DATABASE_URL"] = LIVE_DATABASE_URL
    env["MIGRATION_DATABASE_URL"] = LIVE_MIGRATION_DATABASE_URL
    env.setdefault("IP_HASH_SALT", "0123456789abcdef")
    env.setdefault("TRUSTED_PROXY_HOPS", "0")
    return subprocess.run(
        [sys.executable, *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _run_snippet(code: str) -> subprocess.CompletedProcess[str]:
    return _run("-c", code)


def _run_worker_one_shot() -> subprocess.CompletedProcess[str]:
    return _run("-m", "procrastinate", "--app=src.jobs.app.app", "worker", "--one-shot")


def _defer(token: str) -> int:
    """Defer `ping` for `token`, returning the new job id. Fails the test (via the
    assert) if the subprocess itself failed for a reason other than the duplicate-
    enqueue case, which has its own helper below."""
    result = _run_snippet(
        "from src.jobs.app import app\n"
        "from src.jobs.tasks import defer_ping\n"
        "app.open()\n"
        "try:\n"
        f"    print(defer_ping({token!r}))\n"
        "finally:\n"
        "    app.close()\n"
    )
    assert result.returncode == 0, result.stderr
    return int(result.stdout.strip())


def _defer_raises_already_enqueued(token: str) -> bool:
    result = _run_snippet(
        "from src.jobs.app import app\n"
        "from src.jobs.tasks import defer_ping\n"
        "from procrastinate.exceptions import AlreadyEnqueued\n"
        "app.open()\n"
        "try:\n"
        f"    defer_ping({token!r})\n"
        "    print('NO_ERROR')\n"
        "except AlreadyEnqueued:\n"
        "    print('ALREADY_ENQUEUED')\n"
        "finally:\n"
        "    app.close()\n"
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip() == "ALREADY_ENQUEUED"


def _status(job_id: int) -> str:
    result = _run_snippet(
        "from src.jobs.app import app\n"
        "app.open()\n"
        "try:\n"
        f"    print(app.job_manager.get_job_status({job_id}).value)\n"
        "finally:\n"
        "    app.close()\n"
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _unique_token(label: str) -> str:
    return f"tester-{label}-{uuid.uuid4().hex[:12]}"


@contextlib.contextmanager
def _paused_compose_worker() -> Generator[None]:
    """Pause Compose's long-running `worker` service for a test that needs a job to
    stay queued long enough to exercise the lock - without this, whether that
    container (if up) grabs the job before or after the test's second assertion is
    pure chance. A no-op, not a skip, when Compose isn't managing a `worker` here.
    """
    stopped = subprocess.run(
        ["docker", "compose", "stop", "worker"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        yield
    finally:
        if stopped.returncode == 0:
            subprocess.run(
                ["docker", "compose", "start", "worker"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )


def test_a_deferred_ping_reaches_succeeded_once_a_worker_runs(require_live_db: None) -> None:
    """Acceptance criterion: deferred -> `get_job_status` reaches `succeeded` within
    5s. Polls rather than asserting a fixed path to get there: Compose's own
    long-running `worker` (if up) may already have finished the job before this test
    gets to check, which is a pass, not a race to avoid - a one-shot worker only runs
    here as a fallback for an environment with no such container.
    """
    del require_live_db
    token = _unique_token("basic")
    job_id = _defer(token)

    deadline = time.monotonic() + 5
    status = _status(job_id)
    while status != "succeeded" and time.monotonic() < deadline:
        _run_worker_one_shot()
        time.sleep(0.2)
        status = _status(job_id)

    assert status == "succeeded"


def test_deferring_the_same_token_twice_while_queued_raises_already_enqueued(
    require_live_db: None,
) -> None:
    del require_live_db
    token = _unique_token("duplicate")

    with _paused_compose_worker():
        first_job_id = _defer(token)
        assert _defer_raises_already_enqueued(token)

    # Drain the queue so this token's job doesn't linger for a later test run.
    worker = _run_worker_one_shot()
    assert worker.returncode == 0, worker.stderr
    assert _status(first_job_id) == "succeeded"


def test_a_different_token_gets_its_own_job(require_live_db: None) -> None:
    del require_live_db
    job_a = _defer(_unique_token("a"))
    job_b = _defer(_unique_token("b"))
    assert job_a != job_b

    worker = _run_worker_one_shot()
    assert worker.returncode == 0, worker.stderr


def test_already_enqueued_only_protects_while_the_job_is_still_queued(
    require_live_db: None,
) -> None:
    """Worth stating plainly rather than overclaiming (per the handover): the
    `queueing_lock` blocks a *duplicate enqueue*, not a *duplicate run*. Once the
    first job finishes the lock releases, and a second defer of the same token
    succeeds - correct procrastinate behaviour, and the acceptance criterion's "no
    duplicate side effect" holds here only because `ping` itself has none. A task
    that writes money would need its own idempotency check (e.g. an upsert keyed on
    the token), not this lock alone, to make a post-completion re-defer safe.
    """
    del require_live_db
    token = _unique_token("relock")
    first_job_id = _defer(token)

    worker = _run_worker_one_shot()
    assert worker.returncode == 0, worker.stderr
    assert _status(first_job_id) == "succeeded"

    second_job_id = _defer(token)  # must NOT raise AlreadyEnqueued now
    assert second_job_id != first_job_id

    worker = _run_worker_one_shot()
    assert worker.returncode == 0, worker.stderr
    assert _status(second_job_id) == "succeeded"


# --- to_libpq_dsn: a pure function, no DB and no subprocess needed ------------------


def test_to_libpq_dsn_strips_only_the_sqlalchemy_driver_suffix() -> None:
    from src.jobs.app import to_libpq_dsn

    assert (
        to_libpq_dsn("postgresql+psycopg://speedy:speedy@localhost:5432/speedy_onboard")
        == "postgresql://speedy:speedy@localhost:5432/speedy_onboard"
    )


def test_to_libpq_dsn_does_not_mangle_a_plus_sign_in_the_password() -> None:
    from src.jobs.app import to_libpq_dsn

    assert (
        to_libpq_dsn("postgresql+psycopg://user:p+ss@127.0.0.1:5432/db")
        == "postgresql://user:p+ss@127.0.0.1:5432/db"
    )


def test_to_libpq_dsn_does_not_mangle_percent_encoded_characters_in_the_password() -> None:
    from src.jobs.app import to_libpq_dsn

    assert to_libpq_dsn("postgresql+psycopg://user:p%40ss%23w%21@127.0.0.1:5432/db") == (
        "postgresql://user:p%40ss%23w%21@127.0.0.1:5432/db"
    )


def test_to_libpq_dsn_passes_a_url_without_the_sqlalchemy_prefix_through_unchanged() -> None:
    from src.jobs.app import to_libpq_dsn

    assert to_libpq_dsn("postgresql://already:plain@127.0.0.1:5432/db") == (
        "postgresql://already:plain@127.0.0.1:5432/db"
    )
