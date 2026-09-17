"""SQLAlchemy engine + session factory.

Uses DATABASE_URL from config. SQLite today; switching to Postgres later is a
one-line change to DATABASE_URL (e.g. postgresql+psycopg2://user:pass@host/db).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from config import DATABASE_URL

Base = declarative_base()

# check_same_thread only matters for SQLite + Flask's threaded dev server.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, echo=False, future=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create all tables. Import models first so they register on Base."""
    from db import models_orm  # noqa: F401  (ensures models are registered)
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """Provide a transactional session scope.

    Yields:
        An active SQLAlchemy session; commits on success, rolls back on error.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
