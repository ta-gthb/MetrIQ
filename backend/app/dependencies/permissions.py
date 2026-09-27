"""Backend-enforced permission and record-scope checks (PRD 16.3, 19.1).

Frontend route hiding is a usability measure only; every protected endpoint
declares the permission it needs here.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, status

from app.dependencies.auth import get_current_active_user
from app.models import User
from app.security.permissions import role_permissions


def _assert(user: User, code: str) -> None:
    if code not in role_permissions(user.role_code):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your role ({user.role_code}) is not permitted to perform '{code}'.",
        )


def require_permission(*codes: str) -> Callable[..., User]:
    """Require every listed permission."""

    def dependency(user: User = Depends(get_current_active_user)) -> User:
        for code in codes:
            _assert(user, code)
        return user

    return dependency


def require_any_permission(*codes: str) -> Callable[..., User]:
    """Require at least one of the listed permissions."""

    def dependency(user: User = Depends(get_current_active_user)) -> User:
        granted = role_permissions(user.role_code)
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
