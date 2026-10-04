"""Backend-enforced permission and record-scope checks (PRD 16.3, 19.1).

Frontend route hiding is a usability measure only; every protected endpoint
declares the permission it needs here. Permissions are resolved through the
database-backed service, so an edit a Super Admin makes in the administration
console takes effect immediately, without a restart.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.models import User
from app.services.permission_service import effective_permissions


def _assert(db: Session, user: User, code: str) -> None:
    if code not in effective_permissions(db, user.role_code):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your role ({user.role_code}) is not permitted to perform '{code}'.",
        )


def require_permission(*codes: str) -> Callable[..., User]:
    """Require every listed permission."""

    def dependency(
        user: User = Depends(get_current_active_user),
        db: Session = Depends(get_db),
    ) -> User:
        for code in codes:
            _assert(db, user, code)
        return user

    return dependency


def require_any_permission(*codes: str) -> Callable[..., User]:
    """Require at least one of the listed permissions."""

    def dependency(
        user: User = Depends(get_current_active_user),
        db: Session = Depends(get_db),
    ) -> User:
        granted = effective_permissions(db, user.role_code)
        if not any(code in granted for code in codes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Your role ({user.role_code}) is not permitted to perform this action. "
                    f"Required: one of {', '.join(codes)}."
                ),
            )
        return user

    return dependency
