"""`src/lib/db.py`: the one engine/pool (BACKEND.md §2, §6).

No live Postgres exists in this environment (Docker is not installed), so these cover
only the pre-connection guard paths - calling `session()`/`ping()` before `init()` has
ever run. The "database unreachable after init" path is covered end-to-end, through
the real app, by `test_readyz.py`.
"""

from __future__ import annotations

import asyncio

import pytest

from src.lib import db
from src.lib.errors import DependencyUnavailable


def test_session_raises_dependency_unavailable_before_init() -> None:
    with pytest.raises(DependencyUnavailable):
        db.session()


def test_ping_raises_dependency_unavailable_before_init() -> None:
    with pytest.raises(DependencyUnavailable):
        asyncio.run(db.ping())


def test_guard_error_message_names_no_connection_detail() -> None:
    with pytest.raises(DependencyUnavailable) as excinfo:
        db.session()

    assert excinfo.value.message == "The database is not reachable. Try again in a moment."


def test_dispose_before_init_is_a_safe_no_op() -> None:
    asyncio.run(db.dispose())

    assert db._engine is None  # pyright: ignore[reportPrivateUsage]
    assert db._sessionmaker is None  # pyright: ignore[reportPrivateUsage]
