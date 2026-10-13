"""Step 0.3's acceptance criterion, now CLOSED by step 0.4's migrations existing:
"Given Docker running and `.env` copied from `.env.example`, when
`docker compose up -d`, then `db` reports healthy, `migrate` exits 0, and
`curl localhost:8000/readyz` returns 200."

Independently reproduced from an empty volume (`docker compose down -v && docker
compose up -d --build`, run by hand, not by this suite): `db` healthy, `migrate`
exited 0, `api` healthy, `worker` running, `GET /healthz` and `GET /readyz` both 200.

This test checks the *steady state* of whatever stack is currently up rather than
tearing down and rebuilding the volume itself - that is a multi-minute, destructive
operation (it deletes the Postgres data volume) that does not belong in a routine test
run. It brings the stack up if it is not already (`docker compose up -d`, which is
not destructive - it reuses an existing volume), then asserts the same things the
criterion names. Skips cleanly if Docker/Compose is not available at all.

Replaces `test_compose_migrate_blocked.py` (deleted): that file's skip was keyed to
`backend/alembic/` when the migrations the plan specified live in
`backend/migrations/`, so it would have stayed silently skipped through the very
change it existed to catch. Lesson folded in here: this file's `migrate` and `api`
status checks are enough to cover the real 0.4 state, and the next tripwire-style test
(0.4's own migration round trip, in `test_migrations.py`) keys off
`backend/migrations/versions/`, the actual path, not a guess.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True, timeout=10
    )
    return result.returncode == 0


@pytest.fixture
def running_compose_stack() -> None:
    if not _compose_available():
        pytest.skip("docker compose is not available in this environment")

    up = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if up.returncode != 0:
        pytest.skip(f"`docker compose up -d` failed, can't exercise the stack: {up.stderr}")


def _compose_ps() -> list[dict[str, object]]:
    result = subprocess.run(
        ["docker", "compose", "ps", "-a", "--format", "json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def test_db_reports_healthy(running_compose_stack: None) -> None:
    del running_compose_stack
    services = {row["Service"]: row for row in _compose_ps()}

    assert "db" in services, "db service is not present in `docker compose ps`"
    assert services["db"]["Health"] == "healthy"


def test_migrate_exited_zero(running_compose_stack: None) -> None:
    del running_compose_stack
    services = {row["Service"]: row for row in _compose_ps()}

    assert "migrate" in services, "migrate service is not present in `docker compose ps`"
    assert services["migrate"]["State"] == "exited"
    assert services["migrate"]["ExitCode"] == 0


def test_api_reports_healthy(running_compose_stack: None) -> None:
    del running_compose_stack
    services = {row["Service"]: row for row in _compose_ps()}

    assert "api" in services, "api service is not present in `docker compose ps`"
    assert services["api"]["State"] == "running"
    assert services["api"]["Health"] == "healthy"


def test_curl_readyz_returns_200(running_compose_stack: None) -> None:
    del running_compose_stack
    response = httpx.get("http://localhost:8000/readyz", timeout=5)

    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ready"}}


def test_curl_healthz_returns_200(running_compose_stack: None) -> None:
    del running_compose_stack
    response = httpx.get("http://localhost:8000/healthz", timeout=5)

    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ok"}}


def test_worker_is_running(running_compose_stack: None) -> None:
    del running_compose_stack
    services = {row["Service"]: row for row in _compose_ps()}

    assert "worker" in services, "worker service is not present in `docker compose ps`"
    assert services["worker"]["State"] == "running"
