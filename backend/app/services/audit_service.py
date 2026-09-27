"""Audit and notification helpers (PRD 18.2, 18.3)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, Notification, WorkflowAction, utcnow


def _actor_values(actor) -> tuple[uuid.UUID | None, str | None, str | None, uuid.UUID | None]:
    if actor is None:
        return None, None, None, None
    return (
        getattr(actor, "id", None),
        getattr(actor, "email", None),
        getattr(actor, "role_code", None),
        getattr(actor, "laboratory_id", None),
    )


def _as_json(value: Any) -> Any:
    from datetime import date, datetime
    from decimal import Decimal

    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _as_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_as_json(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def record(
    db: Session,
    *,
    event_type: str,
    entity_type: str,
    entity_id: Any = None,
    actor=None,
    case_id: uuid.UUID | None = None,
    field_changed: str | None = None,
    before: Any = None,
    after: Any = None,
    reason: str | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    extra: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an audit entry. The caller owns the transaction."""
    actor_id, actor_email, actor_role, laboratory_id = _actor_values(actor)
    entry = AuditLog(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        case_id=case_id,
        actor_id=actor_id,
        actor_email=actor_email,
        actor_role=actor_role,
        laboratory_id=laboratory_id,
        field_changed=field_changed,
        before_value=_as_json(before),
        after_value=_as_json(after),
        reason=reason,
        request_id=request_id,
        ip_address=ip_address,
        occurred_at=utcnow(),
        extra=_as_json(extra),
    )
    db.add(entry)
    return entry


def log_workflow(
    db: Session,
    *,
    case,
    action: str,
    actor=None,
    from_status: str | None = None,
    to_status: str | None = None,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
    target_test_instance_id: uuid.UUID | None = None,
) -> WorkflowAction:
    row = WorkflowAction(
        case_id=case.id,
        action=action,
        from_status=from_status or getattr(case, "status", None),
        to_status=to_status or getattr(case, "status", None),
        actor_id=getattr(actor, "id", None),
        actor_role=getattr(actor, "role_code", None),
        reason=reason,
        target_test_instance_id=target_test_instance_id,
        payload=_as_json(payload),
        acted_at=utcnow(),
    )
    db.add(row)
    return row


def notify(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    title: str,
    body: str | None = None,
    case_id: uuid.UUID | None = None,
    category: str = "workflow",
    severity: str = "info",
    link_url: str | None = None,
) -> Notification | None:
    if user_id is None:
        return None
    notification = Notification(
        user_id=user_id,
        case_id=case_id,
        title=title,
        body=body,
        category=category,
        severity=severity,
        link_url=link_url,
    )
    db.add(notification)
    return notification
