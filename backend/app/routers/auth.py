"""Authentication endpoints (FR-01)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies.auth import (
    bearer_scheme,
    get_current_active_user,
    get_client_ip,
    resolve_user_for_principal,
)
from app.models import User, utcnow
from app.schemas.identity import (
    AuthConfigOut,
    LoginRequest,
    LoginResponse,
    MeOut,
    PermissionOut,
    SupabaseConfigOut,
    TokenRefreshRequest,
    UserOut,
)
from app.security import audit_helpers, refresh_store
from app.security.passwords import verify_password
from app.security.permissions import ROLE_DEFINITIONS, role_name, role_permissions
from app.security.tokens import TokenError, create_access_token, decode_token

router = APIRouter(tags=["Authentication"])


def _set_refresh_cookie(response: Response, token: str) -> None:
    """Put the refresh token where page JavaScript cannot read it (item 13)."""
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        token,
        max_age=settings.REFRESH_TOKEN_TTL_DAYS * 24 * 3600,
        httponly=True,
        secure=refresh_store.cookie_secure(),
        samesite="lax",
        path=settings.REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.REFRESH_COOKIE_NAME,
        path=settings.REFRESH_COOKIE_PATH,
        httponly=True,
        secure=refresh_store.cookie_secure(),
        samesite="lax",
    )


def _session_response(db: Session, user: User, *, family_id: str | None = None) -> LoginResponse:
    """Mint a session, record the refresh token and return the response body."""
    refresh_token, _jti, _family = refresh_store.issue(db, user, family_id=family_id)
    subject = user.supabase_user_id or str(user.id)
    return LoginResponse(
        access_token=create_access_token(subject=subject, email=user.email, role=user.role_code),
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/auth/login", response_model=LoginResponse, summary="Sign in")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    # Audit item 4: production signs in through Supabase Auth. Hiding the form is
    # not enough - a caller can post here directly - so the rule is enforced.
    if not settings.local_login_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This deployment signs in through Supabase Auth, so a MetrIQ password"
                " is not accepted. Sign in there and post the access token to"
                " /auth/session."
            ),
        )
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

    session = _session_response(db, user)
    db.commit()

    # The browser gets the refresh token as an HttpOnly cookie. It stays in the
    # body as well so a non-browser client - a script, the CLI, the tests - can
    # rotate without a cookie jar; the page itself never stores it.
    _set_refresh_cookie(response, session.refresh_token or "")
    return session


@router.post("/auth/refresh", response_model=LoginResponse, summary="Refresh an access token")
def refresh(
    request: Request,
    response: Response,
    payload: TokenRefreshRequest | None = None,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Rotate the refresh token and issue a new short-lived access token.

    Reads the HttpOnly cookie first and falls back to the body, so a browser
    never has to touch the token and an API client never has to hold a cookie.
    """
    # A token supplied explicitly is the one that is honoured: silently
    # falling back to the cookie would let a rejected token look accepted.
    presented = (payload.refresh_token if payload else None) or request.cookies.get(
        settings.REFRESH_COOKIE_NAME
    )
    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token was presented."
        )
    try:
        principal = decode_token(presented, expected_type="refresh")
    except TokenError as exc:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = resolve_user_for_principal(db, principal)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")

    try:
        # Rotates in place and raises on reuse, which also revokes the family: a
        # leaked token cannot be exchanged quietly (audit item 13). The response
        # must carry the token this call minted - issuing another one here would
        # leave the rotated token orphaned in a family of its own.
        rotated, _jti, _family = refresh_store.rotate(db, principal)
    except refresh_store.RefreshError as exc:
        if exc.reused:
            audit_helpers.record_token_reuse(db, user=user, ip_address=get_client_ip(request))
        db.commit()
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    subject = user.supabase_user_id or str(user.id)
    session = LoginResponse(
        access_token=create_access_token(subject=subject, email=user.email, role=user.role_code),
        refresh_token=rotated,
        expires_in=settings.ACCESS_TOKEN_TTL_MINUTES * 60,
        user=UserOut.model_validate(user),
    )
    db.commit()
    _set_refresh_cookie(response, rotated)
    return session


@router.post("/auth/logout", summary="Sign out and revoke this refresh-token family")
def logout(
    request: Request,
    response: Response,
    payload: TokenRefreshRequest | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Revoke the session server-side, not just in the browser (item 13).

    Without this, "sign out" only deletes a local copy and a captured refresh
    token keeps working. Answering 204 would hide the outcome; the count of
    revoked sessions is reported instead, and never leaks whether a token was
    valid.
    """
    presented = (payload.refresh_token if payload else None) or request.cookies.get(
        settings.REFRESH_COOKIE_NAME
    )
    user_id = refresh_store.revoke(db, presented, reason="signed out")
    if user_id is not None:
        user = db.get(User, user_id)
        if user is not None:
            audit_helpers.record_logout(db, user=user, ip_address=get_client_ip(request))
    db.commit()
    _clear_refresh_cookie(response)
    return {"revoked": user_id is not None}


@router.get(
    "/auth/config",
    response_model=AuthConfigOut,
    summary="How this deployment expects a browser to sign in",
)
def auth_config() -> AuthConfigOut:
    """Public: the sign-in page reads this before it draws the form.

    Only public values are returned, so the frontend carries no baked-in project
    URL or key: the deployment decides the identity provider, and switching it is
    a configuration change rather than a rebuild (audit items 3 and 4).
    """
    supabase = None
    if settings.supabase_login_enabled:
        supabase = SupabaseConfigOut(
            url=(settings.SUPABASE_URL or "").rstrip("/"),
            auth_url=settings.supabase_auth_url or "",
            anon_key=settings.SUPABASE_ANON_KEY or "",
        )
    return AuthConfigOut(
        provider=settings.AUTH_PROVIDER,
        supabase=supabase,
        local_login=settings.local_login_allowed,
        demo_mode=settings.DEMO_MODE,
        password_reset="supabase" if supabase else None,
    )


@router.post(
    "/auth/session",
    response_model=LoginResponse,
    summary="Exchange a Supabase access token for a MetrIQ session",
)
def create_session(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Supabase authenticates; MetrIQ authorises (audit item 4).

    The browser signs in against Supabase Auth and posts the access token here.
    The token is verified - signature, algorithm, issuer, audience and role - then
    mapped onto a MetrIQ user, creating the link on first use, and exchanged for a
    MetrIQ session: a short-lived access token plus the same HttpOnly, rotating
    refresh cookie a password sign-in would have received.

    Keeping our own session is deliberate. It means the lifetime of a Supabase
    token never becomes the session length, the refresh token stays out of page
    storage (item 13), and revocation keeps working exactly as it did. The role
    always comes from the user row, so nothing in the Supabase token can elevate
    a session.
    """
    if not settings.supabase_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supabase sign-in is not enabled on this deployment.",
        )
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A Supabase access token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        principal = decode_token(credentials.credentials, expected_type="access")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if principal.source != "supabase":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This endpoint exchanges Supabase access tokens only.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = resolve_user_for_principal(db, principal)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "No MetrIQ account is linked to this identity. Ask an administrator"
                " to provision access."
            ),
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is inactive. Contact an administrator.",
        )

    # First Supabase sign-in for this account: remember the link, so later
    # requests resolve by identity rather than by matching an email address. The
    # endpoint owns the transaction, so this is committed with the session.
    if not user.supabase_user_id:
        user.supabase_user_id = principal.subject
    user.auth_provider = "supabase"
    user.last_login_at = utcnow()
    audit_helpers.record_login(
        db, user=user, ip_address=get_client_ip(request), method="supabase"
    )

    session = _session_response(db, user)
    db.commit()
    _set_refresh_cookie(response, session.refresh_token or "")
    return session


@router.get("/auth/demo-accounts", summary="Seeded demonstration accounts (demo mode only)")
def demo_accounts(db: Session = Depends(get_db)) -> dict:
    """The demonstration sign-in list.

    Answers only while ``DEMO_MODE`` is on, so a real deployment exposes nothing
    by default. The password is never part of the frontend bundle: it lives in
    the host's environment (``DEMO_PASSWORD``), and the UI marks the mode
    visibly when it is served (audit item 3).
    """
    if not settings.DEMO_MODE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Demo mode is disabled.")
    users = (
        db.execute(
            select(User).where(User.is_demo.is_(True), User.is_active.is_(True))
        ).scalars().all()
    )
    rank = {code: index for index, code in enumerate(
        [definition.code for definition in sorted(ROLE_DEFINITIONS.values(), key=lambda item: item.rank)]
    )}
    return {
        "demo_mode": True,
        "password": settings.DEMO_PASSWORD,
        "accounts": [
            {
                "email": user.email,
                "full_name": user.full_name,
                "designation": user.designation,
                "role_code": user.role_code,
                "role_name": role_name(user.role_code),
            }
            for user in sorted(users, key=lambda item: rank.get(item.role_code, 99))
        ],
    }


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
