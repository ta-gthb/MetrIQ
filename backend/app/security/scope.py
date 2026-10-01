"""Record-scope helpers: laboratory and assignment scoping (PRD 16.3, 19.1)."""

from __future__ import annotations

import uuid

from sqlalchemy import false, or_

from app.models import EvaluationCase, User
from app.security.permissions import (
    APPROVER,
    AUDITOR,
    ENGINEER,
    LAB_ADMIN,
    REVIEWER,
    SUPER_ADMIN,
)


def unrestricted_case_access(user: User) -> bool:
    """Only system and laboratory administrators see every case in scope."""
    return user.role_code in {SUPER_ADMIN, LAB_ADMIN}


def case_visible(user: User, case: EvaluationCase) -> bool:
    if user.role_code == SUPER_ADMIN:
        return True
    if case.laboratory_id != user.laboratory_id and user.laboratory_id is not None:
        return False
    if unrestricted_case_access(user):
        return True
    if user.role_code == ENGINEER:
        return case.engineer_id == user.id
    if user.role_code == REVIEWER:
        return case.reviewer_id == user.id
    if user.role_code == APPROVER:
        return case.approver_id == user.id
    if user.role_code == AUDITOR:
        return user.laboratory_id is not None
    return False


def case_scope_clause(user: User):
    """Return the SQL predicate for cases this user may read."""
    if user.role_code == SUPER_ADMIN:
        return None
    if user.role_code == LAB_ADMIN or user.role_code == AUDITOR:
        return EvaluationCase.laboratory_id == user.laboratory_id if user.laboratory_id else false()
    if user.role_code == ENGINEER:
        return EvaluationCase.engineer_id == user.id
    if user.role_code == REVIEWER:
        return EvaluationCase.reviewer_id == user.id
    if user.role_code == APPROVER:
        return EvaluationCase.approver_id == user.id
    return false()


def case_editable_by(user: User, case: EvaluationCase) -> bool:
    from app.models import CaseStatus

    if case.status in CaseStatus.IMMUTABLE:
        return False
    if user.role_code in {SUPER_ADMIN, LAB_ADMIN}:
        return True
    if user.role_code == ENGINEER:
        return case.engineer_id == user.id and case.status in CaseStatus.EDITABLE
    return False


def laboratory_filter(user: User) -> uuid.UUID | None:
    """Return the laboratory a query must be constrained to (None = all)."""
    if user.role_code == SUPER_ADMIN:
        return None
    return user.laboratory_id
