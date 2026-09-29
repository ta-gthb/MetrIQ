"""Bearer-token authentication dependency (PRD FR-01, 16.3, 19.1)."""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, utcnow
from app.security.rls import apply_session_scope
from app.security.tokens import TokenError, decode_token

bearer_scheme = HTTPBearer(auto_error=False, description="Supabase or MetrIQ access token")


def get_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def resolve_user_for_principal(db: Session, principal) -> User | None:
    """Map a verified principal onto a local user record.

    Supabase identities are matched on ``supabase_user_id``; locally issued
    tokens carry the MetrIQ user id as their subject, so the id is tried as
    well. Email is the final fallback, which also covers Supabase accounts that
    have not been mirrored with their Supabase id yet.
    """
    user = db.execute(
        select(User).where(User.supabase_user_id == principal.subject)
    ).scalars().first()
    if user is None:
        try:
            user = db.get(User, uuid.UUID(str(principal.subject)))
        except (ValueError, AttributeError, TypeError):
            user = None
    if user is None and principal.email:
        user = db.execute(select(User).where(User.email == principal.email)).scalars().first()
    return user


_resolve_user = resolve_user_for_principal


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    if credentials is None or not credentials.credentials:
        return None
    try:
        principal = decode_token(credentials.credentials, expected_type="access")
    except TokenError:
        return None
    return _resolve_user(db, principal)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise _unauthorised("Authentication credentials were not provided")
    try:
        principal = decode_token(credentials.credentials, expected_type="access")
    except TokenError as exc:
        raise _unauthorised(f"Invalid or expired token: {exc}") from exc

    user = _resolve_user(db, principal)
    if user is None:
        raise _unauthorised(
            "No MetrIQ account is linked to this identity. Ask an administrator to provision access."
        )
    # Second line of defence (audit item 14): tell PostgreSQL which laboratory
    # this connection may see, so the row-level policies apply even to a query
    # that somehow bypasses the application-layer scope. No-op off PostgreSQL.
    apply_session_scope(db, user.laboratory_id)
    return user


def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is inactive. Contact an administrator.",
        )
    return user


def touch_last_login(db: Session, user: User) -> None:
    user.last_login_at = utcnow()
    db.add(user)
