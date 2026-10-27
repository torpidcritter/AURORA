"""Engine and session management (synchronous SQLAlchemy over psycopg 3).

FastAPI runs sync endpoints in a worker threadpool, so blocking DB calls do not block the event loop.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    engine = create_engine(
        s.database_url,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_timeout=s.db_pool_timeout_s,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"connect_timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def _set_timeouts(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        with dbapi_conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(s.db_statement_timeout_ms)}")
            cur.execute("SET TIME ZONE 'UTC'")
        dbapi_conn.commit()

    return engine


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, rolled back on error."""
    db = get_sessionmaker()()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for workers and scripts."""
    db = get_sessionmaker()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
