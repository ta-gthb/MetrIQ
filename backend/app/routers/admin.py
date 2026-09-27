"""Administration: users, roles, laboratories and system settings (FR-02)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.dependencies.permissions import require_any_permission, require_permission
from app.models import Laboratory, SystemSetting, User
from app.routers._helpers import paginate
from app.schemas.common import Paginated
from app.schemas.identity import (
    LaboratoryCreate,
    LaboratoryOut,
    PermissionOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.security.passwords import generate_temporary_password, hash_password
from app.security.permissions import (
    P,
    PERMISSION_CATALOGUE,
    ROLE_DEFINITIONS,
    SUPER_ADMIN,
    role_name,
)
from app.services import audit_service

router = APIRouter(tags=["Administration"])


# ----------------------------------------------------------------- users
@router.get("/users", response_model=Paginated[UserOut], summary="List users")
def list_users(
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.USERS_MANAGE, P.USERS_MANAGE_SCOPED)),
    search: str | None = None,
    role_code: str | None = None,
    page: int = 1,
    page_size: int = Query(25, le=200),
) -> Paginated[UserOut]:
    statement = select(User).order_by(User.full_name)
    if user.role_code != SUPER_ADMIN:
        statement = statement.where(User.laboratory_id == user.laboratory_id)
    if search:
        pattern = f"%{search}%"
        statement = statement.where(User.full_name.ilike(pattern) | User.email.ilike(pattern))
    if role_code:
        statement = statement.where(User.role_code == role_code)
    rows, meta = paginate(db, statement, page=page, page_size=page_size)
    return Paginated[UserOut](items=rows, meta=meta)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED, summary="Create a user")
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.USERS_MANAGE, P.USERS_MANAGE_SCOPED)),
) -> User:
    if user.role_code != SUPER_ADMIN:
        if payload.laboratory_id not in (None, user.laboratory_id):
            raise HTTPException(status_code=403, detail="You may only create users in your own laboratory.")
        if payload.role_code in {SUPER_ADMIN, "LAB_ADMIN"}:
            raise HTTPException(
                status_code=403,
                detail="Laboratory administrators cannot create platform or laboratory administrator accounts.",
            )
    existing = db.execute(
        select(User).where(User.email == payload.email.strip().lower())
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="A user with this email address already exists.")

    record = User(
        email=payload.email.strip().lower(),
        full_name=payload.full_name,
        designation=payload.designation,
        phone=payload.phone,
        role_code=payload.role_code,
        laboratory_id=payload.laboratory_id or (None if user.role_code == SUPER_ADMIN else user.laboratory_id),
        is_active=payload.is_active,
        password_hash=hash_password(payload.password),
        auth_provider="local",
    )
    db.add(record)
    db.flush()
    audit_service.record(
        db, event_type="CREATE", entity_type="user", entity_id=record.id, actor=user,
        after={"email": record.email, "role": record.role_code, "laboratory_id": str(record.laboratory_id)},
    )
    db.commit()
    db.refresh(record)
    return record


@router.patch("/users/{user_id}", response_model=UserOut, summary="Update a user")
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.USERS_MANAGE, P.USERS_MANAGE_SCOPED)),
) -> User:
    record = db.get(User, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role_code != SUPER_ADMIN and record.laboratory_id != user.laboratory_id:
        raise HTTPException(status_code=404, detail="User not found")

    changes = payload.model_dump(exclude_unset=True)
    role_change = None
    if "role_code" in changes and changes["role_code"] and changes["role_code"] != record.role_code:
        if user.role_code != SUPER_ADMIN:
            raise HTTPException(status_code=403, detail="Only a Super Admin may change a user's role.")
        if changes["role_code"] not in ROLE_DEFINITIONS:
            raise HTTPException(status_code=422, detail="Unknown role code.")
        role_change = (record.role_code, changes["role_code"])
        record.role_code = changes["role_code"]

    if "password" in changes and changes["password"]:
        record.password_hash = hash_password(changes["password"])
    for field in ("full_name", "designation", "phone", "laboratory_id", "is_active"):
        if field in changes and changes[field] is not None:
            setattr(record, field, changes[field])

    if role_change:
        audit_service.record(
            db, event_type="ROLE_CHANGE", entity_type="user", entity_id=record.id, actor=user,
            field_changed="role_code", before={"role_code": role_change[0]},
            after={"role_code": role_change[1]},
        )
    audit_service.record(
        db, event_type="EDIT", entity_type="user", entity_id=record.id, actor=user,
        after={key: str(value) for key, value in changes.items() if key != "password"},
    )
    db.commit()
    db.refresh(record)
    return record


@router.post("/users/{user_id}/reset-password", summary="Issue a temporary password")
def reset_password(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.USERS_MANAGE)),
) -> dict:
    record = db.get(User, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="User not found")
    temporary = generate_temporary_password()
    record.password_hash = hash_password(temporary)
    audit_service.record(
        db, event_type="EDIT", entity_type="user", entity_id=record.id, actor=user,
        field_changed="password", reason="Administrative password reset",
    )
    db.commit()
    return {
        "user_id": record.id,
        "temporary_password": temporary,
        "must_change_on_next_login": True,
        "delivery": "Communicate this value through an approved out-of-band channel.",
    }


# ---------------------------------------------------------- laboratories
@router.get("/laboratories", response_model=Paginated[LaboratoryOut], summary="List laboratories")
def list_laboratories(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    page: int = 1,
    page_size: int = Query(25, le=200),
) -> Paginated[LaboratoryOut]:
    statement = select(Laboratory).order_by(Laboratory.name)
    if user.role_code != SUPER_ADMIN:
        statement = statement.where(Laboratory.id == user.laboratory_id)
    rows, meta = paginate(db, statement, page=page, page_size=page_size)
    return Paginated[LaboratoryOut](items=rows, meta=meta)


@router.post(
    "/laboratories",
    response_model=LaboratoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a laboratory",
)
def create_laboratory(
    payload: LaboratoryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.LABS_MANAGE)),
) -> Laboratory:
    existing = db.execute(
        select(Laboratory).where(Laboratory.code == payload.code)
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="A laboratory with this code already exists.")
    record = Laboratory(**payload.model_dump())
    db.add(record)
    db.flush()
    audit_service.record(
        db, event_type="CREATE", entity_type="laboratory", entity_id=record.id, actor=user,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(record)
    return record


@router.patch("/laboratories/{laboratory_id}", response_model=LaboratoryOut, summary="Update a laboratory")
def update_laboratory(
    laboratory_id: uuid.UUID,
    payload: LaboratoryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.LABS_MANAGE)),
) -> Laboratory:
    record = db.get(Laboratory, laboratory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Laboratory not found")
    if user.role_code != SUPER_ADMIN and record.id != user.laboratory_id:
        raise HTTPException(status_code=403, detail="You may only edit your own laboratory.")
    before = {key: getattr(record, key) for key in payload.model_dump()}
    for key, value in payload.model_dump().items():
        setattr(record, key, value)
    audit_service.record(
        db, event_type="EDIT", entity_type="laboratory", entity_id=record.id, actor=user,
        before=before, after=payload.model_dump(),
    )
    db.commit()
    db.refresh(record)
    return record


# ------------------------------------------------------ permissions / settings
@router.get("/admin/permissions", response_model=list[PermissionOut], summary="Permission catalogue")
def permission_catalogue(
    user: User = Depends(require_any_permission(P.USERS_MANAGE, P.USERS_MANAGE_SCOPED)),
) -> list[PermissionOut]:
    return [
        PermissionOut(code=code, description=description, category=category)
        for code, description, category in PERMISSION_CATALOGUE
    ]


@router.get("/admin/roles", summary="Role definitions and permission matrix")
def role_matrix(
    user: User = Depends(require_any_permission(P.USERS_MANAGE, P.USERS_MANAGE_SCOPED)),
) -> list[dict]:
    return [
        {
            "code": definition.code,
            "name": definition.name,
            "description": definition.description,
            "rank": definition.rank,
            "permission_count": len(definition.permissions),
            "permissions": sorted(definition.permissions),
        }
        for definition in sorted(ROLE_DEFINITIONS.values(), key=lambda item: item.rank)
    ]


@router.get("/settings", summary="List system settings")
def list_settings(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.SETTINGS_MANAGE)),
) -> list[dict]:
    rows = db.execute(select(SystemSetting).order_by(SystemSetting.key)).scalars().all()
    return [
        {
            "id": row.id,
            "key": row.key,
            "value": row.value,
            "category": row.category,
            "description": row.description,
        }
        for row in rows
    ]


@router.put("/settings/{key}", summary="Create or update a setting")
def put_setting(
    key: str,
    value: dict,
    category: str = "general",
    description: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.SETTINGS_MANAGE)),
) -> dict:
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalars().first()
    if row is None:
        row = SystemSetting(key=key, category=category, description=description)
        db.add(row)
        db.flush()
    before = row.value
    row.value = value
    row.category = category or row.category
    row.description = description or row.description
    row.updated_by = user.id
    audit_service.record(
        db, event_type="EDIT", entity_type="system_setting", entity_id=row.id, actor=user,
        field_changed=key, before=before, after=value,
    )
    db.commit()
    return {"key": row.key, "value": row.value, "category": row.category}
