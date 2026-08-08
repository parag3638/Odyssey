from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        # Supabase's session-mode pooler hard-caps this project at 15 total
        # client connections. SQLAlchemy's own defaults (pool_size=5 +
        # max_overflow=10 = 15) let this single process exhaust that entire
        # budget by itself under nothing more than a handful of concurrent
        # requests, with zero headroom left for anything else — that's what
        # caused the EMAXCONNSESSION outage. Keep this process's ceiling well
        # under the shared cap, and recycle idle connections proactively
        # rather than relying solely on pool_pre_ping to notice they died.
        _engine = create_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=2,
            pool_recycle=300,
        )
    return _engine


def get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def get_db():
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
