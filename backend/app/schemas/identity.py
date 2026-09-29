"""Authentication, user, role and laboratory schemas."""

from __future__ import annotations

import uuid

from pydantic import AliasChoices, BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    """One sign-in: the user ID the platform issued, and the password.

    The address on the account is accepted in the same field, because that is
    what the field carried before identifiers existed and an account may have
    been reached that way ever since.
    """

    user_id: str = Field(
        min_length=3,
        max_length=255,
        validation_alias=AliasChoices("user_id", "email"),
    )
    password: str = Field(min_length=1, max_length=256)


class TokenRefreshRequest(BaseModel):
    # Optional: a browser presents the refresh token as an HttpOnly cookie and
    # sends no body at all (audit item 13).
    refresh_token: str | None = None


class UserOut(ORMModel):
    id: uuid.UUID
    user_code: str
    email: str
    full_name: str
    designation: str | None = None
    role_code: str
    laboratory_id: uuid.UUID | None = None
    is_active: bool
    auth_provider: str
    supabase_user_id: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class SupabaseConfigOut(BaseModel):
    """What the browser needs in order to sign in through Supabase Auth.

    Public values only. The anon key is designed to be shipped to browsers, and
    row-level security plus this API's own checks are what protect the data; the
    service-role key is never part of this payload, and a test asserts that.
    """

    url: str
    auth_url: str
    anon_key: str


class AuthConfigOut(BaseModel):
    """How this deployment expects a browser to sign in (audit item 4)."""

    provider: str
    #: Present only when Supabase sign-in is configured.
    supabase: SupabaseConfigOut | None = None
    #: Whether MetrIQ's own password check may be used: development and demo only.
    local_login: bool
    demo_mode: bool
    #: Where a user goes to reset a password, when the deployment supports it.
    password_reset: str | None = None


class PermissionOut(ORMModel):
    code: str
    description: str | None = None
    category: str


class MeOut(BaseModel):
    user: UserOut
    role_name: str
    permissions: list[str]
    laboratory: "LaboratoryOut | None" = None


class LaboratoryOut(ORMModel):
    id: uuid.UUID
    name: str
    code: str
    location: str | None = None
    address: str | None = None
    contact_email: str | None = None
    accreditation_no: str | None = None
    is_active: bool


class LaboratoryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    code: str = Field(min_length=1, max_length=64)
    location: str | None = None
    address: str | None = None
    contact_email: str | None = None
    accreditation_no: str | None = None
    is_active: bool = True


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=8, max_length=256)
    role_code: str
    laboratory_id: uuid.UUID | None = None
    designation: str | None = None
    phone: str | None = None
    is_active: bool = True

    @field_validator("role_code")
    @classmethod
    def _known_role(cls, value: str) -> str:
        from app.security.permissions import ROLE_DEFINITIONS

        if value not in ROLE_DEFINITIONS:
            raise ValueError(f"unknown role '{value}'")
        return value


class UserUpdate(BaseModel):
    full_name: str | None = None
    designation: str | None = None
    phone: str | None = None
    role_code: str | None = None
    laboratory_id: uuid.UUID | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)


MeOut.model_rebuild()
