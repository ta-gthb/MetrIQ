"""Workflow actions, audit trail, notifications, AI events, settings."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import JSONType
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AuditEventType:
    CREATE = "CREATE"
    EDIT = "EDIT"
    SUBMIT = "SUBMIT"
    CORRECTION_REQUEST = "CORRECTION_REQUEST"
    VERIFY = "VERIFY"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    FINALIZE = "FINALIZE"
    DOWNLOAD = "DOWNLOAD"
    ROLE_CHANGE = "ROLE_CHANGE"
    RULE_ACTIVATION = "RULE_ACTIVATION"
    # Governed ruleset lifecycle (audit items 6 and 10).
    RULE_SUBMIT = "RULE_SUBMIT"
    RULE_REVIEW = "RULE_REVIEW"
    RULE_APPROVAL = "RULE_APPROVAL"
    RULE_REJECTION = "RULE_REJECTION"
    RULE_SCHEDULE = "RULE_SCHEDULE"
    RULE_DEACTIVATION = "RULE_DEACTIVATION"
    AI_ACTION = "AI_ACTION"
    UPLOAD = "UPLOAD"
    DELETE = "DELETE"
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    # Presented a refresh token that had already been used: treated as a stolen
    # credential and answered by revoking the whole family (audit item 13).
    TOKEN_REUSE = "TOKEN_REUSE"
    OVERRIDE = "OVERRIDE"
    REPORT_GENERATE = "REPORT_GENERATE"


class WorkflowAction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "workflow_actions"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(40), index=True)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    actor_role: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    target_test_instance_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    payload: Mapped[dict | None] = mapped_column(JSONType)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class AuditLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "audit_logs"

    event_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_type: Mapped[str] = mapped_column(String(60), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    actor_email: Mapped[str | None] = mapped_column(String(255))
    actor_role: Mapped[str | None] = mapped_column(String(64))
    laboratory_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    field_changed: Mapped[str | None] = mapped_column(String(120))
    before_value: Mapped[dict | None] = mapped_column(JSONType)
    after_value: Mapped[dict | None] = mapped_column(JSONType)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    extra: Mapped[dict | None] = mapped_column(JSONType)


class Notification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), default="workflow")
    link_url: Mapped[str | None] = mapped_column(String(512))
    severity: Mapped[str] = mapped_column(String(16), default="info")
    is_read: Mapped[bool] = mapped_column(default=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Traceability for every AI action (PRD 12.5, 14.4)."""

    __tablename__ = "ai_events"

    case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    test_instance_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    feature_code: Mapped[str] = mapped_column(String(60), index=True)
    provider: Mapped[str] = mapped_column(String(60), default="stub")
    model: Mapped[str | None] = mapped_column(String(80))
    input_reference: Mapped[str | None] = mapped_column(String(512))
    output_summary: Mapped[dict | None] = mapped_column(JSONType)
    confidence: Mapped[str | None] = mapped_column(String(16))
    user_disposition: Mapped[str | None] = mapped_column(String(24))
    disposition_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column()
    degraded: Mapped[bool] = mapped_column(default=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))


class SystemSetting(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    value: Mapped[dict | None] = mapped_column(JSONType)
    category: Mapped[str] = mapped_column(String(60), default="general")
    description: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
