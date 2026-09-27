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
