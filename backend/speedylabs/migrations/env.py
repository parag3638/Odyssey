"""Alembic environment.

Migrations connect as `speedy_migrator`, never as the app role — `MIGRATION_DATABASE_URL`,
not `DATABASE_URL`. That is the whole point of the two-role model in BACKEND.md §4: the
app cannot change the schema, so a compromised app process cannot either.

`src.config` is the single source of connection settings, so a malformed URL is refused
here by the same validators that guard application boot.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from src import config

# Models are defined per-feature and migrations are hand-written, so there is no single
# declarative metadata to autogenerate from. BACKEND.md §5 wants one migration per
# change, reviewed - not a generated diff.
target_metadata = None


def _url() -> str:
    return config.load().migration_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
