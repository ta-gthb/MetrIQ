"""Database engine, session factory and declarative base helpers."""

from __future__ import annotations

import os
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
import json
from typing import Any
import uuid

from sqlalchemy import JSON, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.utils.decimals import decimal_str

# Portable JSON column: JSONB on PostgreSQL, JSON elsewhere.
JSONType = JSON().with_variant(JSONB, "postgresql")


def _json_default(value: Any) -> Any:
    """Serialise the value types our JSON columns legitimately carry.

    Metrological numbers are stored as plain decimal strings so a JSON round
    trip never reintroduces binary floating point (PRD 11.5).
    """
    if isinstance(value, Decimal):
        return decimal_str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "value") and hasattr(value, "name"):  # Enum
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serialisable")


def _json_serializer(value: Any) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False)


def _engine_options(url: str) -> dict[str, Any]:
    options: dict[str, Any] = {
        "pool_pre_ping": True,
        "future": True,
        "echo": settings.SQL_ECHO,
        "json_serializer": _json_serializer,
    }
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    else:
        options["pool_size"] = 5
        options["max_overflow"] = 10
        options["pool_recycle"] = 1800
    return options


def _normalise_url(url: str) -> str:
    """Supabase hands out `postgres://`; SQLAlchemy 2.x wants `postgresql://`."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    return url


DATABASE_URL = _normalise_url(settings.DATABASE_URL)

if DATABASE_URL.startswith("sqlite:///"):
    db_path = DATABASE_URL.replace("sqlite:///", "", 1)
    if db_path and db_path != ":memory:":
        directory = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(directory, exist_ok=True)

engine: Engine = create_engine(DATABASE_URL, **_engine_options(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and background work."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
