"""Shared router helpers: pagination, lookup and scope enforcement."""

from __future__ import annotations

import math
import uuid
from typing import Any, Sequence, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import EvaluationCase, User
from app.schemas.common import PageMeta
from app.security.scope import case_visible

T = TypeVar("T")


def get_case_or_404(
    db: Session,
    case_id: uuid.UUID,
    user: User,
    *,
    include_deleted: bool = False,
) -> EvaluationCase:
    """Load a case and enforce laboratory/record scope (PRD 16.3, 19.1)."""
    case = db.get(EvaluationCase, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation case not found")
    if not case_visible(user, case):
        # Deliberately 404 rather than 403 so cross-tenant probing learns nothing.
        raise not_found()
    return case


def not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation case not found")


def paginate(
    db: Session,
    statement: Select,
    *,
    page: int = 1,
    page_size: int = 25,
    max_page_size: int = 200,
) -> tuple[list[Any], PageMeta]:
    page = max(1, page)
    page_size = min(max(1, page_size), max_page_size)
    total = db.execute(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ).scalar_one()
    rows = db.execute(statement.limit(page_size).offset((page - 1) * page_size)).scalars().unique().all()
    pages = max(1, math.ceil(total / page_size)) if total else 1
    meta = PageMeta(
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        has_next=page < pages,
        has_prev=page > 1,
    )
    return list(rows), meta


def require_reason(reason: str | None, *, minimum: int = 5, field: str = "reason") -> str:
    if not reason or len(reason.strip()) < minimum:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"A {field} of at least {minimum} characters is required for this action.",
        )
    return reason.strip()


def next_application_number(db: Session) -> str:
    from datetime import datetime, timezone

    year = datetime.now(timezone.utc).year
    prefix = f"NAWI-{year}-"
    count = db.execute(
        select(func.count(EvaluationCase.id)).where(EvaluationCase.application_no.like(f"{prefix}%"))
    ).scalar_one()
    candidate_index = count + 1
    while True:
        candidate = f"{prefix}{candidate_index:06d}"
        exists = db.execute(
            select(EvaluationCase.id).where(EvaluationCase.application_no == candidate)
        ).first()
        if exists is None:
            return candidate
        candidate_index += 1


def to_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def serialise_case(case: EvaluationCase, *, detail: bool = False) -> dict[str, Any]:
    from app.services.metrology_service import summarise_case

    summary = summarise_case(case)
    payload: dict[str, Any] = {
        "id": case.id,
        "application_no": case.application_no,
        "title": case.title,
        "status": case.status,
        "revision_no": case.revision_no,
        "priority": case.priority,
        "laboratory_id": case.laboratory_id,
        "instrument_id": case.instrument_id,
        "standard_version_id": case.standard_version_id,
        "engineer_id": case.engineer_id,
        "reviewer_id": case.reviewer_id,
        "approver_id": case.approver_id,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
        "finalized_at": case.finalized_at,
        "instrument_model": case.instrument.model if case.instrument else None,
        "applicant_name": case.applicant.name if case.applicant else None,
        "overall_result": summary["overall"],
        "test_counts": summary,
    }
    if detail:
        payload.update(
            {
                "purpose": case.purpose,
                "scope_notes": case.scope_notes,
                "submitted_at": case.submitted_at,
                "verified_at": case.verified_at,
                "approved_at": case.approved_at,
                "last_correction_reason": case.last_correction_reason,
                "instrument": case.instrument,
                "applicant": case.applicant,
                "manufacturer": case.manufacturer,
                "engineer": case.engineer,
                "reviewer": case.reviewer,
                "approver": case.approver,
                "standard_version_label": case.standard_version.version_label if case.standard_version else None,
                "template_version_label": case.template_version.version_label if case.template_version else None,
                "conditions": case.conditions,
            }
        )
    return payload
