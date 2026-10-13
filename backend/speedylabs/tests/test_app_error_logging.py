"""The single `AppError` handler in `src/main.py` logs once (BACKEND.md §8, §9) - and
that log line must not leak what the response body also must not leak (BACKEND.md §4:
secrets, connection strings and internal names never reach logs either).

`src.lib.logger` configures structlog with its default `PrintLoggerFactory`, which
writes straight to `sys.stdout` rather than through the stdlib `logging` module -
`caplog` (which hooks stdlib logging handlers) cannot see these lines, so stdout is
captured directly via `capsys`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.main import app

LEAK_STRINGS = ["nobody", "59999", "127.0.0.1", "psycopg", "nothing", "Traceback"]


def test_readyz_failure_log_line_does_not_leak_connection_details(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with TestClient(app) as client:
        client.get("/readyz")

    out = capsys.readouterr().out
    for needle in LEAK_STRINGS:
        assert needle not in out, f"{needle!r} leaked into the app_error log line"


def test_readyz_failure_is_logged_exactly_once(capsys: pytest.CaptureFixture[str]) -> None:
    with TestClient(app) as client:
        client.get("/readyz")

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if '"app_error"' in line]
    assert len(lines) == 1
