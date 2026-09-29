"""Identity, role and permission tables (PRD FR-01, FR-02, section 14.1)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(default=100)

    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )


class Permission(Base, TimestampMixin):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(80), primary_key=True)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(60), default="general")


class RolePermission(Base, TimestampMixin):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_code", "permission_code", name="uq_role_permission"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_code: Mapped[str] = mapped_column(ForeignKey("roles.code", ondelete="CASCADE"), index=True)
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("permissions.code", ondelete="CASCADE"), index=True
    )

    role: Mapped[Role] = relationship(back_populates="permissions")


class RefreshToken(Base, TimestampMixin):
    """One issued refresh token, and the rotation history around it (item 13).

    A refresh token is single-use. Presenting one that has already been used is
    treated as theft: the whole family is revoked, so the thief and the victim
    are both signed out rather than the thief quietly keeping access. The token
    itself is never stored - only its ``jti``, which is what makes this table
    safe to keep.
    """

    __tablename__ = "refresh_tokens"

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(120))
    replaced_by_jti: Mapped[str | None] = mapped_column(String(36))


class Laboratory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "laboratories"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    location: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(String(255))
    accreditation_no: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    supabase_user_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    designation: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(40))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role_code: Mapped[str] = mapped_column(ForeignKey("roles.code"), nullable=False, index=True)
    laboratory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("laboratories.id", ondelete="SET NULL"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # True for the seeded demonstration accounts, so the opt-in demo panel lists
    # exactly those and never an account an operator created for real.
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Supabase Auth remains the identity source of truth; account creation is
    # mirrored here so the backend can enforce role + laboratory scope.
    auth_provider: Mapped[str] = mapped_column(String(32), default="local")

    role: Mapped[Role] = relationship(lazy="joined")
    laboratory: Mapped[Laboratory | None] = relationship(lazy="joined")

    @property
    def permissions(self) -> set[str]:
        return {item.permission_code for item in (self.role.permissions if self.role else [])}
