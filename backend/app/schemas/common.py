"""Shared schema building blocks."""

from __future__ import annotations

import uuid
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class MessageOut(BaseModel):
    message: str
    detail: str | None = None


class PageMeta(BaseModel):
    total: int
    page: int
    page_size: int
    pages: int
    has_next: bool
    has_prev: bool


class Paginated(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class ErrorOut(BaseModel):
    detail: str
    code: str | None = None
    field: str | None = None


class AuditEntryOut(ORMModel):
    id: uuid.UUID
    event_type: str
    entity_type: str
    entity_id: str | None = None
    case_id: uuid.UUID | None = None
    actor_email: str | None = None
    actor_role: str | None = None
    field_changed: str | None = None
    before_value: dict | None = None
    after_value: dict | None = None
    reason: str | None = None
    occurred_at: object | None = None


class NotificationOut(ORMModel):
    id: uuid.UUID
    title: str
    body: str | None = None
    category: str
    severity: str
    link_url: str | None = None
    is_read: bool
    case_id: uuid.UUID | None = None
    created_at: object | None = None
