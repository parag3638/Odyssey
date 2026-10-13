"""The only place the application opens a database connection.

BACKEND.md §2: one home per job. §6: one connection pool per process, sized to the
database's limit. The engine is created in the app lifespan and disposed on shutdown, so
a reload does not leak pools.

The driver is psycopg 3 for both sync and async: procrastinate has no asyncpg connector,
so a single `postgresql+psycopg://` URL covers the API, the worker and Alembic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.lib.errors import DependencyUnavailable

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init(database_url: str, *, pool_size: int = 10) -> None:
    """Create the process-wide engine. Called once, from the app lifespan."""
    global _engine, _sessionmaker
    if _engine is not None:
        return
    _engine = create_async_engine(
        database_url,
        pool_size=pool_size,
        pool_pre_ping=True,
        max_overflow=0,
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)


def engine() -> AsyncEngine:
    """The engine itself, for the rare caller that needs a connection it owns.

    Almost everything wants `session()`. This exists for work that must stay on **one physical
    connection** across several commits - a session-scoped Postgres advisory lock is the case that
    forced it, because that lock belongs to the backend connection and a pooled session does not
    promise to keep one.
    """
    if _engine is None:
        raise DependencyUnavailable("The database is not reachable. Try again in a moment.")
    return _engine


async def dispose() -> None:
    """Close the pool on shutdown (BACKEND.md §7, graceful shutdown)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def session() -> AsyncSession:
    """A new session. Callers own the transaction boundary."""
    if _sessionmaker is None:
        raise DependencyUnavailable("The database is not reachable. Try again in a moment.")
    return _sessionmaker()


async def sessions() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency form."""
    async with session() as s:
        yield s


async def ping() -> None:
    """Prove the database answers. Raises `DependencyUnavailable` if it does not.

    The message is deliberately generic: BACKEND.md §3 forbids returning connection
    strings, hostnames or driver errors to a caller.
    """
    if _engine is None:
        raise DependencyUnavailable("The database is not reachable. Try again in a moment.")
    try:
        async with _engine.connect() as conn:
            await conn.execute(text("select 1"))
    except Exception as exc:
        raise DependencyUnavailable(
            "The database is not reachable. Try again in a moment."
        ) from exc
