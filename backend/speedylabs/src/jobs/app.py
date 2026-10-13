"""The procrastinate App: one queue, backed by Postgres.

No Redis and no broker (decision: "procrastinate on Postgres, not Celery + Redis").
`PsycopgConnector` keeps a dedicated connection for `LISTEN`/`NOTIFY`, so job pickup is
push-latency rather than polling — which is why no connection pooler may sit in front of
this in production.
"""

from __future__ import annotations

import procrastinate

from src import config

_SQLALCHEMY_PREFIX = "postgresql+psycopg://"
_LIBPQ_PREFIX = "postgresql://"


def to_libpq_dsn(sqlalchemy_url: str) -> str:
    """Strip SQLAlchemy's driver suffix.

    SQLAlchemy wants `postgresql+psycopg://`; psycopg's own `conninfo` wants
    `postgresql://`. One validated variable serves both rather than a second env var that
    can drift out of step with the first.
    """
    if sqlalchemy_url.startswith(_SQLALCHEMY_PREFIX):
        return _LIBPQ_PREFIX + sqlalchemy_url[len(_SQLALCHEMY_PREFIX) :]
    return sqlalchemy_url


def create_app() -> procrastinate.App:
    settings = config.load()
    return procrastinate.App(
        connector=procrastinate.PsycopgConnector(
            conninfo=to_libpq_dsn(settings.database_url),
        ),
        import_paths=["src.jobs.tasks"],
    )


#: The worker entry point: `procrastinate --app=src.jobs.app.app worker`.
app = create_app()
