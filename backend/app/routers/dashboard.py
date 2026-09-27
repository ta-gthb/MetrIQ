"""Dashboard endpoints (PRD 15.4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.models import AIEvent, CaseStatus, EvaluationCase, TestInstance, User
from app.security.permissions import APPROVER, ENGINEER, LAB_ADMIN, REVIEWER, SUPER_ADMIN
from app.security.scope import laboratory_filter

router = APIRouter(tags=["Dashboard"])

OPEN_STATUSES = [
    CaseStatus.DRAFT,
    CaseStatus.ASSIGNED,
    CaseStatus.IN_PROGRESS,
    CaseStatus.TESTING_COMPLETED,
    CaseStatus.UNDER_REVIEW,
    CaseStatus.CORRECTION_REQUIRED,
    CaseStatus.VERIFIED,
    CaseStatus.UNDER_APPROVAL,
]


def _base_query(user: User):
    statement = select(EvaluationCase)
    laboratory_id = laboratory_filter(user)
    if laboratory_id is not None:
        statement = statement.where(EvaluationCase.laboratory_id == laboratory_id)
    if user.role_code == ENGINEER:
        statement = statement.where(
            (EvaluationCase.engineer_id == user.id) | (EvaluationCase.created_by == user.id)
        )
    return statement


@router.get("/dashboard/summary", summary="KPI cards, status chart and recent activity")
def dashboard_summary(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)) -> dict:
    base = _base_query(user)
    rows = db.execute(base).scalars().unique().all()

    by_status: dict[str, int] = {}
    for case in rows:
        by_status[case.status] = by_status.get(case.status, 0) + 1

    case_ids = [case.id for case in rows]
    test_counts: dict[str, int] = {}
    if case_ids:
        test_rows = db.execute(
            select(TestInstance.result_status, func.count(TestInstance.id))
            .where(TestInstance.case_id.in_(case_ids))
            .group_by(TestInstance.result_status)
        ).all()
        test_counts = {status_code or "PENDING": count for status_code, count in test_rows}

    recent = sorted(rows, key=lambda item: item.updated_at, reverse=True)[:8]
    from app.routers._helpers import serialise_case

    return {
        "kpis": {
            "total": len(rows),
            "in_progress": by_status.get(CaseStatus.IN_PROGRESS, 0),
            "awaiting_review": by_status.get(CaseStatus.TESTING_COMPLETED, 0)
            + by_status.get(CaseStatus.UNDER_REVIEW, 0),
            "awaiting_approval": by_status.get(CaseStatus.VERIFIED, 0)
            + by_status.get(CaseStatus.UNDER_APPROVAL, 0),
            "finalized": by_status.get(CaseStatus.FINALIZED, 0),
            "failed": test_counts.get("FAIL", 0),
            "open": sum(by_status.get(status, 0) for status in OPEN_STATUSES),
        },
        "status_chart": [
            {"status": status_code, "count": by_status.get(status_code, 0)}
            for status_code in CaseStatus.ALL
        ],
        "test_metrics": {
            "by_status": test_counts,
            "completed": sum(
                count for status_code, count in test_counts.items()
                if status_code in {"PASS", "FAIL", "NOT_APPLICABLE", "WAIVED"}
            ),
            "pending": sum(
                count for status_code, count in test_counts.items()
                if status_code in {"PENDING", "INCOMPLETE", "INVALID"}
            ),
        },
        "recent_cases": [serialise_case(case) for case in recent],
    }


@router.get("/dashboard/pending", summary="Role-specific pending action queue")
def dashboard_pending(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)) -> dict:
    from app.routers._helpers import serialise_case

    base = _base_query(user)
    queues: dict[str, list] = {}

    if user.role_code in {SUPER_ADMIN, LAB_ADMIN, ENGINEER}:
        drafts = db.execute(
            base.where(EvaluationCase.status.in_([CaseStatus.DRAFT, CaseStatus.ASSIGNED, CaseStatus.IN_PROGRESS]))
        ).scalars().unique().all()
        queues["my_testing"] = [serialise_case(case) for case in drafts]

    if user.role_code in {SUPER_ADMIN, LAB_ADMIN, REVIEWER}:
        review = db.execute(
            base.where(EvaluationCase.status.in_([CaseStatus.TESTING_COMPLETED, CaseStatus.UNDER_REVIEW]))
        ).scalars().unique().all()
        queues["awaiting_review"] = [serialise_case(case) for case in review]

    if user.role_code in {SUPER_ADMIN, APPROVER}:
        approval = db.execute(
            base.where(EvaluationCase.status.in_([CaseStatus.VERIFIED, CaseStatus.UNDER_APPROVAL]))
        ).scalars().unique().all()
        queues["awaiting_approval"] = [serialise_case(case) for case in approval]

    if user.role_code == ENGINEER:
        corrections = db.execute(
            base.where(EvaluationCase.status == CaseStatus.CORRECTION_REQUIRED)
        ).scalars().unique().all()
        queues["corrections_requested"] = [serialise_case(case) for case in corrections]

    return {"queues": queues, "total": sum(len(items) for items in queues.values())}


@router.get("/dashboard/ai-review", summary="AI items needing human disposition")
def ai_review_queue(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)) -> dict:
    statement = (
        select(AIEvent)
        .where(AIEvent.user_disposition.is_(None))
        .order_by(AIEvent.created_at.desc())
        .limit(50)
    )
    events = db.execute(statement).scalars().all()
    return {
        "items": [
            {
                "id": event.id,
                "feature_code": event.feature_code,
                "provider": event.provider,
                "model": event.model,
                "confidence": event.confidence,
                "degraded": event.degraded,
                "case_id": event.case_id,
                "test_instance_id": event.test_instance_id,
                "attachment_id": event.attachment_id,
                "created_at": event.created_at,
                "summary": (event.output_summary or {}).get("message"),
            }
            for event in events
        ],
        "total": len(events),
        "advisory_only": True,
    }
