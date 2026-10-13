"""structlog JSON logger (BACKEND.md §8): `configure()` must be idempotent - every
module that logs calls `logger.get_logger(__name__)`, and nothing in the app
coordinates who configures structlog first, so a second call (e.g. a module reimported
under a test runner, or a second worker in the same process) must not double-handle.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.lib import logger


def test_configure_only_calls_structlog_configure_once_across_repeated_calls() -> None:
    logger._configured = False  # pyright: ignore[reportPrivateUsage]
    try:
        with patch("structlog.configure") as mock_configure:
            logger.configure("info")
            logger.configure("info")
            logger.configure("debug")  # even with a different argument

            assert mock_configure.call_count == 1
    finally:
        logger._configured = False  # pyright: ignore[reportPrivateUsage]


def test_get_logger_emits_one_json_line_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    logger._configured = False  # pyright: ignore[reportPrivateUsage]
    try:
        logger.configure("info")
        log = logger.get_logger("test_logger")
        log.info("hello", foo="bar")

        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line.strip()]
        assert len(lines) == 1
        assert '"event": "hello"' in lines[0]
        assert '"foo": "bar"' in lines[0]
    finally:
        logger._configured = False  # pyright: ignore[reportPrivateUsage]
