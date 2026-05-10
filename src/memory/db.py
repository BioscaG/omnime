"""Database engine and session helpers."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from src.config import settings


logger = logging.getLogger(__name__)


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=1800,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def wait_for_database(max_attempts: int = 60, delay: float = 1.0) -> None:
    """Block until Postgres accepts a connection or raise."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            engine = get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            if attempt > 1:
                logger.info("Database ready after %d attempt(s)", attempt)
            return
        except OperationalError as exc:
            last_exc = exc
            logger.info("Database not ready (attempt %d/%d)", attempt, max_attempts)
            time.sleep(delay)
    raise RuntimeError(f"Database not reachable after {max_attempts} attempts: {last_exc}")


def run_migrations() -> None:
    """Apply alembic migrations programmatically (idempotent)."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(settings.project_root / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    alembic_cfg.set_main_option("script_location", str(settings.project_root / "alembic"))
    command.upgrade(alembic_cfg, "head")
