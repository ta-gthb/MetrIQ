"""Authentication endpoints (FR-01)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies.auth import (
    get_current_active_user,
    get_client_ip,
    resolve_user_for_principal,
)
from app.models import User, utcnow
from app.schemas.identity import (
    LoginRequest,
    LoginResponse,
    MeOut,
    PermissionOut,
    TokenRefreshRequest,
    UserOut,
)
from app.security import audit_helpers
from app.security.passwords import verify_password
from app.security.permissions import ROLE_DEFINITIONS, role_name, role_permissions
from app.security.tokens import TokenError, create_access_token, create_refresh_token, decode_token

router = APIRouter(tags=["Authentication"])


@router.post("/auth/login", response_model=LoginResponse, summary="Sign in")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.execute(
        select(User).where(User.email == payload.email.strip().lower())
    ).scalars().first()
    # Constant-ish work regardless of whether the account exists.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email address or password.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is inactive. Contact an administrator.",
        )
    user.last_login_at = utcnow()
    audit_helpers.record_login(db, user=user, ip_address=get_client_ip(request))
    db.commit()

    subject = user.supabase_user_id or str(user.id)
    token = create_access_token(subject=subject, email=user.email, role=user.role_code)
    return LoginResponse(
        access_token=token,
        refresh_token=create_refresh_token(subject, email=user.email),
        expires_in=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/auth/refresh", response_model=LoginResponse, summary="Refresh an access token")
def refresh(payload: TokenRefreshRequest, db: Session = Depends(get_db)) -> LoginResponse:
    try:
        principal = decode_token(payload.refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user = resolve_user_for_principal(db, principal)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")
    subject = user.supabase_user_id or str(user.id)
    return LoginResponse(
        access_token=create_access_token(subject=subject, email=user.email, role=user.role_code),
        refresh_token=create_refresh_token(subject, email=user.email),
        expires_in=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=MeOut, summary="Current user profile and effective permissions")
def read_me(user: User = Depends(get_current_active_user)) -> MeOut:
    return MeOut(
        user=UserOut.model_validate(user),
        role_name=role_name(user.role_code),
        permissions=sorted(role_permissions(user.role_code)),
        laboratory=user.laboratory,
    )


@router.get("/me/permissions", response_model=list[PermissionOut], summary="Effective permission list")
def read_my_permissions(user: User = Depends(get_current_active_user)) -> list[PermissionOut]:
    from app.security.permissions import PERMISSION_CATALOGUE

    granted = role_permissions(user.role_code)
    return [
        PermissionOut(code=code, description=description, category=category)
        for code, description, category in PERMISSION_CATALOGUE
        if code in granted
    ]


@router.get("/roles", summary="Available roles and their permission sets")
def list_roles(user: User = Depends(get_current_active_user)) -> list[dict]:
    return [
        {
            "code": definition.code,
            "name": definition.name,
            "description": definition.description,
            "rank": definition.rank,
            "permissions": sorted(definition.permissions),
        }
        for definition in sorted(ROLE_DEFINITIONS.values(), key=lambda item: item.rank)
    ]
