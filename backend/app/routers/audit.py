"""Audit trail, workflow history and notifications (PRD 18.2)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.dependencies.permissions import require_any_permission
from app.models import AuditLog, EvaluationCase, Notification, User, WorkflowAction, utcnow
from app.routers._helpers import get_case_or_404, paginate
from app.schemas.common import AuditEntryOut, NotificationOut, Paginated
from app.security.permissions import P
from app.security.scope import laboratory_filter

router = APIRouter(tags=["Audit"])


@router.get("/cases/{case_id}/audit-logs", response_model=list[AuditEntryOut], summary="Case audit trail")
def case_audit_logs(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[AuditLog]:
    case = get_case_or_404(db, case_id, user)
    return db.execute(
        select(AuditLog).where(AuditLog.case_id == case.id).order_by(AuditLog.occurred_at.desc())
    ).scalars().all()


@router.get("/cases/{case_id}/workflow-actions", summary="Case workflow history")
def case_workflow_actions(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[dict]:
    case = get_case_or_404(db, case_id, user)
    rows = db.execute(
        select(WorkflowAction)
        .where(WorkflowAction.case_id == case.id)
        .order_by(WorkflowAction.acted_at.asc())
    ).scalars().all()
    return [
        {
            "id": row.id,
            "action": row.action,
            "from_status": row.from_status,
            "to_status": row.to_status,
            "actor_id": row.actor_id,
            "actor_role": row.actor_role,
            "reason": row.reason,
            "payload": row.payload,
            "acted_at": row.acted_at,
        }
        for row in rows
    ]


@router.get("/audit-logs", response_model=Paginated[AuditEntryOut], summary="Search audit logs")
def audit_logs(
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.AUDIT_VIEW, P.AUDIT_VIEW_SCOPE, P.AUDIT_VIEW_LIMITED)),
    event_type: str | None = None,
    entity_type: str | None = None,
    case_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = Query(50, le=200),
) -> Paginated[AuditEntryOut]:
    statement = select(AuditLog).order_by(AuditLog.occurred_at.desc())
    granted = {code for code in (P.AUDIT_VIEW, P.AUDIT_VIEW_SCOPE) if code in user.permissions}
    if P.AUDIT_VIEW not in granted:
        # Scoped roles (and roles limited to case-level entries) only see their own laboratory.
        laboratory_id = laboratory_filter(user)
        if laboratory_id is None:
            statement = statement.where(AuditLog.actor_id == user.id)
        else:
            statement = statement.where(
                (AuditLog.laboratory_id == laboratory_id) | (AuditLog.actor_id == user.id)
            )
    if event_type:
        statement = statement.where(AuditLog.event_type == event_type)
    if entity_type:
        statement = statement.where(AuditLog.entity_type == entity_type)
    if case_id:
        statement = statement.where(AuditLog.case_id == case_id)
    rows, meta = paginate(db, statement, page=page, page_size=page_size)
    return Paginated[AuditEntryOut](items=rows, meta=meta)


@router.get("/notifications", response_model=list[NotificationOut], summary="My notifications")
def list_notifications(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    unread_only: bool = False,
) -> list[Notification]:
    statement = (
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
    )
    if unread_only:
        statement = statement.where(Notification.is_read.is_(False))
    return db.execute(statement).scalars().all()


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut, summary="Mark read")
def mark_read(
    notification_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Notification:
    from fastapi import HTTPException

    row = db.get(Notification, notification_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Notification not found")
    row.is_read = True
    row.read_at = utcnow()
    db.commit()
    db.refresh(row)
    return row
