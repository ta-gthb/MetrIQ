"""Authentication, user, role and laboratory schemas."""

from __future__ import annotations

import uuid

from pydantic import AliasChoices, BaseModel, Field, field_validator

from app.schemas.common import ORMModel

#: The designations a registration may carry (Super Admin portal, Users).
DESIGNATIONS: tuple[str, ...] = ("Officer", "Operator", "Assistant")


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
    location: str = Field(min_length=2, max_length=255)
    address: str = Field(min_length=2, max_length=1000)
    contact_email: str = Field(min_length=3, max_length=255)
    accreditation_no: str | None = None
    is_active: bool = True

    @field_validator("location", "address", "contact_email")
    @classmethod
    def _required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("this field is required")
        return text

    @field_validator("contact_email")
    @classmethod
    def _contact_email(cls, value: str) -> str:
        text = value.strip()
        local, _, domain = text.partition("@")
        if not local or "." not in domain:
            raise ValueError("contact email must be a valid email address")
        return text


class RolePermissionUpdate(BaseModel):
    """The complete permission set a Super Admin wants a role to hold."""

    permissions: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("permissions")
    @classmethod
    def _deduplicate(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for code in value:
            text = code.strip()
            if text and text not in seen:
                seen.append(text)
        return seen


class PasswordSetRequest(BaseModel):
    """An administrator setting a new password directly (no current password)."""

    password: str = Field(min_length=8, max_length=256)

    @field_validator("password")
    @classmethod
    def _policy(cls, value: str) -> str:
        from app.security.passwords import validate_password_strength

        return validate_password_strength(value)


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=8, max_length=256)
    role_code: str
    laboratory_id: uuid.UUID | None = None
    designation: str
    phone: str | None = None
    is_active: bool = True

    @field_validator("role_code")
    @classmethod
    def _known_role(cls, value: str) -> str:
        from app.security.permissions import ROLE_DEFINITIONS

        if value not in ROLE_DEFINITIONS:
            raise ValueError(f"unknown role '{value}'")
        return value

    @field_validator("designation")
    @classmethod
    def _known_designation(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("designation is required")
        if text not in DESIGNATIONS:
            raise ValueError("designation must be one of: " + ", ".join(DESIGNATIONS))
        return text

    @field_validator("password")
    @classmethod
    def _password_policy(cls, value: str) -> str:
        from app.security.passwords import validate_password_strength

        return validate_password_strength(value)


class UserUpdate(BaseModel):
    """Fields the administration console may change on an existing account.

    The role is fixed at registration: a user's identifier and role describe
    the position they were registered for, so an edit cannot move an account
    between roles.
    """

    full_name: str | None = None
    designation: str | None = None
    phone: str | None = None
    laboratory_id: uuid.UUID | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)

    @field_validator("password")
    @classmethod
    def _password_policy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from app.security.passwords import validate_password_strength

        return validate_password_strength(value)


MeOut.model_rebuild()
