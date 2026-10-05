"""Message payloads for case discussions and the System Administrator support channel."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class MessageOut(ORMModel):
    id: uuid.UUID
    body: str
    created_at: datetime
    sender_id: uuid.UUID | None = None
    sender_name: str | None = None
    sender_role: str | None = None
    sender_user_code: str | None = None


class CaseMessageOut(MessageOut):
    case_id: uuid.UUID


class SupportMessageOut(MessageOut):
    thread_user_id: uuid.UUID


class SupportThreadOut(BaseModel):
    user_id: uuid.UUID
    user_name: str
    user_code: str | None = None
    role_code: str
    laboratory_name: str | None = None
    last_message_at: datetime | None = None
    last_message: str | None = None
    message_count: int = 0


class MessagePostedOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
