"""Refresh-token rotation and revocation (project-audit item 13).

A refresh token is single-use. Every use records the fact and mints a successor
in the same *family*; presenting a token whose row already shows a use is
treated as theft, and the whole family is revoked so both the thief and the
victim have to sign in again. Signing out revokes the family too, which is what
makes "log out" mean something on the server rather than only in the browser.

Only the token's ``jti`` is stored; the token itself is never kept.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RefreshToken, User
from app.security.tokens import Principal, TokenError, create_refresh_token, decode_token, refresh_expiry


class RefreshError(Exception):
    """The refresh token cannot be honoured. The caller turns this into a 401."""

    def __init__(self, message: str, *, reused: bool = False) -> None:
        super().__init__(message)
        # True when the token had already been used, which is the case worth
        # recording in the audit trail (the family has been revoked).
        self.reused = reused


def issue(db: Session, user: User, *, family_id: str | None = None) -> tuple[str, str, str]:
    """Record a refresh token for ``user``.

    Returns ``(token, jti, family_id)``. The row and the token's ``jti`` claim
    always agree, so rotation and reuse detection can look the token up.
    """
    jti = str(uuid.uuid4())
    family = family_id or str(uuid.uuid4())
    subject = user.supabase_user_id or str(user.id)
    token = create_refresh_token(subject, email=user.email, jti=jti)
    db.add(
        RefreshToken(
            jti=jti,
            user_id=user.id,
            family_id=family,
            issued_at=datetime.now(timezone.utc),
            expires_at=refresh_expiry(),
        )
    )
    db.flush()
    return token, jti, family


def rotate(db: Session, principal: Principal) -> tuple[str, str, str]:
    """Consume the presented token and mint its successor.

    Returns ``(token, jti, family_id)``. Raises :class:`RefreshError` when the
    token is unknown, expired, revoked or already used - the last case revokes
    the whole family first.
    """
    jti = str(principal.claims.get("jti") or "")
    row = db.get(RefreshToken, jti) if jti else None
    if row is None:
        raise RefreshError("This refresh token is not recognised. Sign in again.")

    now = datetime.now(timezone.utc)
    if row.used_at is not None:
        # Reuse: the only safe reading is that the token was copied. Both the
        # holder of the copy and the legitimate holder are signed out.
        revoke_family(db, row.family_id, reason="refresh token reused")
        raise RefreshError(
            "This refresh token has already been used. Sign in again.", reused=True
        )
    if row.revoked_at is not None:
        raise RefreshError("This refresh token was revoked. Sign in again.")
    expires = _aware(row.expires_at)
    if expires is not None and expires <= now:
        raise RefreshError("This refresh token expired. Sign in again.")

    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        revoke_family(db, row.family_id, reason="account unavailable")
        raise RefreshError("Account unavailable")

    row.used_at = now
    token, successor_jti, family = issue(db, user, family_id=row.family_id)
    row.replaced_by_jti = successor_jti
    return token, successor_jti, family


def revoke_family(db: Session, family_id: str, *, reason: str) -> int:
    """Revoke every token in a family. Returns how many rows it touched."""
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(RefreshToken).where(RefreshToken.family_id == family_id)
    ).scalars().all()
    for row in rows:
        if row.revoked_at is None:
            row.revoked_at = now
            row.revoked_reason = reason
    return len(rows)


def revoke(db: Session, token: str | None, *, reason: str) -> uuid.UUID | None:
    """Revoke the family a token belongs to.

    Returns the id of the user whose session was revoked, or None when the
    token is not recognised - so the caller can record the sign-out without
    learning anything about tokens it does not hold.
    """
    if not token:
        return None
    try:
        principal = decode_token(token, expected_type="refresh")
    except TokenError:
        return None
    jti = str(principal.claims.get("jti") or "")
    row = db.get(RefreshToken, jti) if jti else None
    if row is None:
        return None
    revoke_family(db, row.family_id, reason=reason)
    return row.user_id


def active_family_count(db: Session, user: User) -> int:
    """How many unrevoked families a user has, for tests and diagnostics."""
    rows = db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    ).scalars().all()
    return len({row.family_id for row in rows})


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; treat them as UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def cookie_secure() -> bool:
    """Only send the cookie over HTTPS where the deployment is HTTPS-only."""
    return settings.ENVIRONMENT == "production"