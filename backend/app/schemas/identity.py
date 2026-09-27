"""Authentication, user, role and laboratory schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class UserOut(ORMModel):
    id: uuid.UUID
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
