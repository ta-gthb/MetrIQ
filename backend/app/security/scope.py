"""Record-scope helpers: laboratory and assignment scoping (PRD 16.3, 19.1)."""

from __future__ import annotations

import uuid

from app.models import EvaluationCase, User
from app.security.permissions import (
    APPROVER,
    AUDITOR,
    ENGINEER,
    LAB_ADMIN,
    P,
    REVIEWER,
    SUPER_ADMIN,
    has_permission,
)


def unrestricted_case_access(user: User) -> bool:
    """Super Admin, Lab Admin, Reviewer, Approver and Auditor see the whole lab."""
    return user.role_code in {SUPER_ADMIN, LAB_ADMIN, REVIEWER, APPROVER, AUDITOR} or has_permission(
        user.role_code, P.CASES_VIEW
    )


def case_visible(user: User, case: EvaluationCase) -> bool:
    if user.role_code == SUPER_ADMIN:
        return True
    if case.laboratory_id != user.laboratory_id and user.laboratory_id is not None:
        return False
    if unrestricted_case_access(user):
        return True
    if user.role_code == ENGINEER:
        return user.id in {case.engineer_id, case.reviewer_id, case.approver_id, case.created_by}
    return False


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
