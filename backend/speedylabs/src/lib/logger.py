"""One logger, configured once, JSON on every line.

BACKEND.md §8: JSON, one line per event, carrying `requestId`, `userId` (id only),
route, status and duration. §4: logs never contain passwords, tokens, account numbers
or personal data beyond ids — which is why callers bind ids, never whole records.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

_configured = False


def configure(level: str = "info") -> None:
    """Install the JSON renderer. Idempotent, so importing twice cannot double-handle."""
    global _configured
    if _configured:
        return

    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str) -> Any:
    """A bound logger. Returns `Any` because structlog's bound type is dynamic."""
    return structlog.get_logger(name)
