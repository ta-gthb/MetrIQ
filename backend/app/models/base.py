"""Declarative base, mixins and shared column conventions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _metadata_schema() -> str | None:
    """The schema MetrIQ's tables live in, when one is configured.

    Resolved once, at import, so the tables carry the schema from the moment
    they are declared and every statement is generated schema-qualified. That
    matters when a database is shared with another application: unqualified DDL
    would otherwise skip tables that exist under the same name in `public`.
    """
    if settings.DATABASE_URL.startswith("sqlite"):
        return None
    return settings.DB_SCHEMA


class Base(DeclarativeBase):
    """Declarative base for every MetrIQ table."""

    metadata = MetaData(schema=_metadata_schema())


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class AuditActorMixin:
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class CodeMixin:
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
