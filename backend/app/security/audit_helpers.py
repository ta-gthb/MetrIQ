"""Small audit helpers shared by the authentication router."""

from __future__ import annotations

from sqlalchemy.orm import Session


def record_login(db: Session, *, user, ip_address: str | None = None) -> None:
    from app.services import audit_service

    audit_service.record(
        db,
        event_type="LOGIN",
        entity_type="user",
        entity_id=user.id,
        actor=user,
        ip_address=ip_address,
        extra={"role": user.role_code},
    )


def record_logout(db: Session, *, user, ip_address: str | None = None) -> None:
    """Signing out revokes the session server-side, so it belongs in the trail."""
    from app.services import audit_service

    audit_service.record(
        db,
        event_type="LOGOUT",
        entity_type="user",
        entity_id=user.id,
        actor=user,
        ip_address=ip_address,
        extra={"role": user.role_code},
    )


def record_token_reuse(db: Session, *, user, ip_address: str | None = None) -> None:
    """A refresh token was presented twice: the family was revoked."""
    from app.services import audit_service

    audit_service.record(
        db,
        event_type="TOKEN_REUSE",
        entity_type="user",
        entity_id=user.id,
        actor=user,
        ip_address=ip_address,
        extra={"role": user.role_code, "action": "refresh-token family revoked"},
    )
