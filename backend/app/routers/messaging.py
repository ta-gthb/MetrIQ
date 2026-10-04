"""Case discussions and the Super Admin support channel (FR-05, PRD 18.3).

* Every evaluation case carries a discussion between the laboratory that owns
  it and the people assigned to it.
* Every account has one support thread with the platform administrator, so any
  role can reach the Super Admin without leaving the application.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.models import CaseMessage, Laboratory, SupportMessage, User
from app.routers._helpers import get_case_or_404
from app.schemas.messaging import (
    CaseMessageOut,
    MessageCreate,
    SupportMessageOut,
    SupportThreadOut,
)
from app.security.permissions import APPROVER, ENGINEER, LAB_ADMIN, REVIEWER, SUPER_ADMIN
from app.services import audit_service

router = APIRouter(tags=["Messaging"])

MAX_MESSAGE_LENGTH = 4000


def _require_super_admin(user: User) -> None:
    if user.role_code != SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Support threads are visible to the Super Admin only.",
        )


def _require_body(payload: MessageCreate) -> str:
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="A message cannot be empty.")
    if len(body) > MAX_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"A message may be at most {MAX_MESSAGE_LENGTH} characters.",
        )
    return body


def _may_post_case_message(case, user: User) -> bool:
    """The laboratory's team and the people assigned to the case may post."""
    if user.role_code == SUPER_ADMIN:
        return True
    if user.role_code == LAB_ADMIN:
        return user.laboratory_id is not None and user.laboratory_id == case.laboratory_id
    if user.role_code == ENGINEER:
        return case.engineer_id == user.id
    if user.role_code == REVIEWER:
        return case.reviewer_id == user.id
    if user.role_code == APPROVER:
        return case.approver_id == user.id
    return False


def _message_payload(message, sender: User | None) -> dict:
    return {
        "id": message.id,
        "body": message.body,
        "created_at": message.created_at,
        "sender_id": message.sender_id,
        "sender_name": sender.full_name if sender else None,
        "sender_role": message.sender_role,
        "sender_user_code": sender.user_code if sender else None,
    }


def _case_payload(message: CaseMessage, sender: User | None) -> dict:
    payload = _message_payload(message, sender)
    payload["case_id"] = message.case_id
    return payload


def _support_payload(message: SupportMessage, sender: User | None) -> dict:
    payload = _message_payload(message, sender)
    payload["thread_user_id"] = message.thread_user_id
    return payload


def _case_recipients(db: Session, case) -> set[uuid.UUID]:
    """Everyone who takes part in the case discussion except the sender."""
    recipients = {case.engineer_id, case.reviewer_id, case.approver_id}
    lab_admins = db.execute(
        select(User.id).where(
            User.role_code == LAB_ADMIN,
            User.laboratory_id == case.laboratory_id,
            User.is_active.is_(True),
        )
    ).scalars().all()
    recipients.update(lab_admins)
    return {item for item in recipients if item is not None}


def _super_admin_ids(db: Session) -> list[uuid.UUID]:
    return list(
        db.execute(
            select(User.id).where(
                User.role_code == SUPER_ADMIN, User.is_active.is_(True)
            )
        ).scalars().all()
    )


# ------------------------------------------------------------ case discussion
@router.get(
    "/cases/{case_id}/messages",
    response_model=list[CaseMessageOut],
    summary="Discussion on one evaluation case",
)
def list_case_messages(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[dict]:
    case = get_case_or_404(db, case_id, user)
    rows = db.execute(
        select(CaseMessage, User)
        .join(User, User.id == CaseMessage.sender_id, isouter=True)
        .where(CaseMessage.case_id == case.id)
        .order_by(CaseMessage.created_at.asc())
        .limit(500)
    ).all()
    return [_case_payload(message, sender) for message, sender in rows]


@router.post(
    "/cases/{case_id}/messages",
    response_model=CaseMessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Post to an evaluation case discussion",
)
def post_case_message(
    case_id: uuid.UUID,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    case = get_case_or_404(db, case_id, user)
    if not _may_post_case_message(case, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only the laboratory's administrator, the people assigned to this case "
                "and the Super Admin may post in its discussion."
            ),
        )
    body = _require_body(payload)
    message = CaseMessage(
        case_id=case.id, sender_id=user.id, sender_role=user.role_code, body=body
    )
    db.add(message)
    db.flush()
    for recipient_id in sorted(_case_recipients(db, case) - {user.id}, key=str):
        audit_service.notify(
            db,
            user_id=recipient_id,
            case_id=case.id,
            title=f"New message on {case.application_no}",
            body=f"{user.full_name} ({user.role_code}): {body[:180]}",
            link_url=f"/evaluation.html?case={case.id}",
        )
    db.commit()
    db.refresh(message)
    return _case_payload(message, user)


# ------------------------------------------------------- support: own thread
@router.get(
    "/support/messages",
    response_model=list[SupportMessageOut],
    summary="My conversation with the Super Admin",
)
def list_my_support_messages(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[dict]:
    rows = db.execute(
        select(SupportMessage, User)
        .join(User, User.id == SupportMessage.sender_id, isouter=True)
        .where(SupportMessage.thread_user_id == user.id)
        .order_by(SupportMessage.created_at.asc())
        .limit(500)
    ).all()
    return [_support_payload(message, sender) for message, sender in rows]


@router.post(
    "/support/messages",
    response_model=SupportMessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Contact the Super Admin",
)
def post_support_message(
    payload: MessageCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    body = _require_body(payload)
    message = SupportMessage(
        thread_user_id=user.id,
        sender_id=user.id,
        sender_role=user.role_code,
        body=body,
    )
    db.add(message)
    db.flush()
    for admin_id in _super_admin_ids(db):
        if admin_id == user.id:
            continue
        audit_service.notify(
            db,
            user_id=admin_id,
            title=f"Support message from {user.full_name}",
            body=body[:200],
            category="support",
            link_url=f"/support.html?thread={user.id}",
        )
    db.commit()
    db.refresh(message)
    return _support_payload(message, user)


# --------------------------------------------------- support: Super Admin side
@router.get(
    "/support/threads",
    response_model=list[SupportThreadOut],
    summary="Support threads (Super Admin)",
)
def list_support_threads(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[dict]:
    _require_super_admin(user)
    rows = db.execute(
        select(
            SupportMessage.thread_user_id,
            func.max(SupportMessage.created_at).label("last_message_at"),
            func.count(SupportMessage.id).label("message_count"),
        )
        .group_by(SupportMessage.thread_user_id)
        .order_by(func.max(SupportMessage.created_at).desc())
    ).all()
    threads: list[dict] = []
    for thread_user_id, last_message_at, message_count in rows:
        thread_user = db.get(User, thread_user_id)
        if thread_user is None:
            continue
        last = db.execute(
            select(SupportMessage)
            .where(SupportMessage.thread_user_id == thread_user_id)
            .order_by(SupportMessage.created_at.desc())
            .limit(1)
        ).scalars().first()
        laboratory = db.get(Laboratory, thread_user.laboratory_id) if thread_user.laboratory_id else None
        threads.append(
            {
                "user_id": thread_user.id,
                "user_name": thread_user.full_name,
                "user_code": thread_user.user_code,
                "role_code": thread_user.role_code,
                "laboratory_name": laboratory.name if laboratory else None,
                "last_message_at": last_message_at,
                "last_message": last.body[:200] if last else None,
                "message_count": message_count,
            }
        )
    return threads


@router.get(
    "/support/threads/{thread_user_id}",
    response_model=list[SupportMessageOut],
    summary="One support thread (Super Admin)",
)
def get_support_thread(
    thread_user_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[dict]:
    _require_super_admin(user)
    if db.get(User, thread_user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    rows = db.execute(
        select(SupportMessage, User)
        .join(User, User.id == SupportMessage.sender_id, isouter=True)
        .where(SupportMessage.thread_user_id == thread_user_id)
        .order_by(SupportMessage.created_at.asc())
        .limit(500)
    ).all()
    return [_support_payload(message, sender) for message, sender in rows]


@router.post(
    "/support/threads/{thread_user_id}",
    response_model=SupportMessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Reply to a support thread (Super Admin)",
)
def reply_support_thread(
    thread_user_id: uuid.UUID,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    _require_super_admin(user)
    thread_user = db.get(User, thread_user_id)
    if thread_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    body = _require_body(payload)
    message = SupportMessage(
        thread_user_id=thread_user.id,
        sender_id=user.id,
        sender_role=user.role_code,
        body=body,
    )
    db.add(message)
    db.flush()
    audit_service.notify(
        db,
        user_id=thread_user.id,
        title="Reply from the Super Admin",
        body=body[:200],
        category="support",
        link_url="/support.html",
    )
    db.commit()
    db.refresh(message)
    return _support_payload(message, user)
