"""Administration: users, roles, laboratories and system settings (FR-02)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.dependencies.permissions import require_any_permission, require_permission
from app.models import (
    AuditLog,
    CaseAssignment,
    EnvironmentalCondition,
    EvaluationCase,
    Laboratory,
    Notification,
    RefreshToken,
    SystemSetting,
    TestInstance,
    User,
)
from app.routers._helpers import paginate
from app.schemas.common import Paginated
from app.schemas.identity import (
    LaboratoryCreate,
    LaboratoryOut,
    PasswordSetRequest,
    PermissionOut,
    RolePermissionUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.security.passwords import hash_password
from app.security.permissions import (
    P,
    PERMISSION_CATALOGUE,
    ROLE_DEFINITIONS,
    SUPER_ADMIN,
    role_name,
)
from app.services import audit_service
from app.services.identity import generate_user_code
from app.services.permission_service import (
    effective_permissions,
    is_customised,
    set_role_permissions,
)

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
        statement = statement.where(
            User.full_name.ilike(pattern)
            | User.email.ilike(pattern)
            | User.user_code.ilike(pattern)
        )
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
    if payload.laboratory_id is not None and db.get(Laboratory, payload.laboratory_id) is None:
        raise HTTPException(status_code=422, detail="laboratory_id must reference a registered laboratory.")
    existing = db.execute(
        select(User).where(User.email == payload.email.strip().lower())
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="A user with this email address already exists.")

    record = User(
        # The platform issues the identifier, so the caller never supplies one.
        user_code=generate_user_code(db, payload.role_code),
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
        after={
            "user_id": record.user_code,
            "email": record.email,
            "role": record.role_code,
            "laboratory_id": str(record.laboratory_id),
        },
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
    if "laboratory_id" in changes and user.role_code != SUPER_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Only the System Administrator may assign a user to a laboratory.",
        )
    if "laboratory_id" in changes and changes["laboratory_id"] is not None:
        if db.get(Laboratory, changes["laboratory_id"]) is None:
            raise HTTPException(status_code=422, detail="laboratory_id must reference a registered laboratory.")
    if "password" in changes and changes["password"]:
        record.password_hash = hash_password(changes["password"])
    for field in ("full_name", "designation", "phone", "laboratory_id", "is_active"):
        if field in changes and changes[field] is not None:
            setattr(record, field, changes[field])

    audit_service.record(
        db, event_type="EDIT", entity_type="user", entity_id=record.id, actor=user,
        after={key: str(value) for key, value in changes.items() if key != "password"},
    )
    db.commit()
    db.refresh(record)
    return record


@router.post("/users/{user_id}/reset-password", summary="Set a new password for a user")
def reset_password(
    user_id: uuid.UUID,
    payload: PasswordSetRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.USERS_MANAGE, P.USERS_MANAGE_SCOPED)),
) -> dict:
    """Set the account password directly. The previous value is not required."""
    record = db.get(User, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role_code != SUPER_ADMIN and record.laboratory_id != user.laboratory_id:
        raise HTTPException(status_code=404, detail="User not found")
    record.password_hash = hash_password(payload.password)
    audit_service.record(
        db, event_type="EDIT", entity_type="user", entity_id=record.id, actor=user,
        field_changed="password", reason="Administrative password set",
    )
    db.commit()
    return {
        "user_id": record.id,
        "updated": True,
        "message": "The new password is active immediately.",
    }


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Delete a user permanently",
)
def delete_user(
    user_id: uuid.UUID,
    reason: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.USERS_MANAGE, P.USERS_MANAGE_SCOPED)),
) -> None:
    """Remove the account from the system.

    An account that is part of a governed record - assigned to an evaluation
    case or having recorded observations or conditions - is refused and must be
    disabled instead, because deleting it would break the traceability the
    audit trail promises. A freshly registered account with no history is
    removed outright, together with its sessions and notifications.
    """
    record = db.get(User, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="User not found")
    if record.id == user.id:
        raise HTTPException(status_code=409, detail="You cannot delete your own account.")
    if user.role_code != SUPER_ADMIN:
        if record.laboratory_id != user.laboratory_id:
            raise HTTPException(status_code=404, detail="User not found")
        if record.role_code in {SUPER_ADMIN, "LAB_ADMIN"}:
            raise HTTPException(
                status_code=403,
                detail="Laboratory administrators cannot delete platform or laboratory administrator accounts.",
            )
    if record.role_code == SUPER_ADMIN:
        remaining = db.execute(
            select(func.count()).select_from(User).where(
                User.role_code == SUPER_ADMIN, User.id != record.id
            )
        ).scalar_one()
        if not remaining:
            raise HTTPException(
                status_code=409,
                detail="The last System Administrator account cannot be deleted.",
            )

    assigned = db.execute(
        select(func.count()).select_from(EvaluationCase).where(
            or_(
                EvaluationCase.engineer_id == record.id,
                EvaluationCase.reviewer_id == record.id,
                EvaluationCase.approver_id == record.id,
            )
        )
    ).scalar_one()
    recorded_conditions = db.execute(
        select(func.count()).select_from(EnvironmentalCondition).where(
            EnvironmentalCondition.recorded_by == record.id
        )
    ).scalar_one()
    completed_tests = db.execute(
        select(func.count()).select_from(TestInstance).where(
            TestInstance.completed_by == record.id
        )
    ).scalar_one()
    if assigned or recorded_conditions or completed_tests:
        raise HTTPException(
            status_code=409,
            detail=(
                "This account is part of a governed evaluation record and cannot be "
                "deleted. Disable the account instead; the audit trail keeps its name."
            ),
        )

    audit_service.record(
        db, event_type="DELETE", entity_type="user", entity_id=record.id, actor=user,
        before={
            "user_id": record.user_code,
            "email": record.email,
            "role": record.role_code,
            "laboratory_id": str(record.laboratory_id),
        },
        reason=reason or "Administrative deletion",
    )
    for table in (CaseAssignment, Notification, RefreshToken):
        db.execute(sa_delete(table).where(table.user_id == record.id))
    db.delete(record)
    db.commit()


@router.get(
    "/admin/users/work-summary",
    summary="Work detail and progress of every registered user",
)
def users_work_summary(
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.USERS_MANAGE, P.USERS_MANAGE_SCOPED)),
) -> list[dict]:
    """Per-user workload: cases held in each assignment, tests done, last activity."""
    statement = select(User).order_by(User.full_name)
    if user.role_code != SUPER_ADMIN:
        statement = statement.where(User.laboratory_id == user.laboratory_id)
    users = db.execute(statement).scalars().all()

    case_statement = select(EvaluationCase)
    if user.role_code != SUPER_ADMIN:
        case_statement = case_statement.where(
            EvaluationCase.laboratory_id == user.laboratory_id
        )
    cases = db.execute(case_statement).scalars().all()

    test_rows = db.execute(
        select(EvaluationCase.engineer_id, TestInstance.status, TestInstance.applicability_status)
        .join(TestInstance, TestInstance.case_id == EvaluationCase.id)
    ).all()

    activity = dict(
        db.execute(
            select(AuditLog.actor_id, func.max(AuditLog.occurred_at))
            .where(AuditLog.actor_id.is_not(None))
            .group_by(AuditLog.actor_id)
        ).all()
    )

    summary: dict[uuid.UUID, dict] = {
        row.id: {
            "user_id": str(row.id),
            "user_code": row.user_code,
            "full_name": row.full_name,
            "email": row.email,
            "designation": row.designation,
            "role_code": row.role_code,
            "laboratory_name": row.laboratory.name if row.laboratory else None,
            "is_active": row.is_active,
            "cases_created": 0,
            "cases_as_engineer": 0,
            "cases_as_reviewer": 0,
            "cases_as_approver": 0,
            "open_cases": 0,
            "finalized_cases": 0,
            "tests_total": 0,
            "tests_completed": 0,
            "last_activity_at": activity.get(row.id),
        }
        for row in users
    }

    closed = {"FINALIZED", "CANCELLED"}
    for case in cases:
        for field, key in (
            ("engineer_id", "cases_as_engineer"),
            ("reviewer_id", "cases_as_reviewer"),
            ("approver_id", "cases_as_approver"),
        ):
            owner = getattr(case, field)
            if owner in summary:
                summary[owner][key] += 1
                if case.status not in closed:
                    summary[owner]["open_cases"] += 1
                if case.status == "FINALIZED":
                    summary[owner]["finalized_cases"] += 1
        if case.created_by in summary:
            summary[case.created_by]["cases_created"] += 1

    for engineer_id, status, applicability in test_rows:
        if engineer_id not in summary:
            continue
        summary[engineer_id]["tests_total"] += 1
        if status == "COMPLETED" or applicability == "NOT_APPLICABLE":
            summary[engineer_id]["tests_completed"] += 1

    return list(summary.values())


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
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.USERS_MANAGE, P.USERS_MANAGE_SCOPED)),
) -> list[dict]:
    rows: list[dict] = []
    for definition in sorted(ROLE_DEFINITIONS.values(), key=lambda item: item.rank):
        granted = sorted(effective_permissions(db, definition.code))
        rows.append(
            {
                "code": definition.code,
                "name": definition.name,
                "description": definition.description,
                "rank": definition.rank,
                "locked": definition.code == SUPER_ADMIN,
                "customised": is_customised(db, definition.code),
                "permission_count": len(granted),
                "permissions": granted,
            }
        )
    return rows


@router.put(
    "/admin/roles/{role_code}/permissions",
    summary="Edit the permissions held by a role",
)
def update_role_permissions(
    role_code: str,
    payload: RolePermissionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.USERS_MANAGE)),
) -> dict:
    definition = ROLE_DEFINITIONS.get(role_code)
    if definition is None:
        raise HTTPException(status_code=404, detail="Unknown role code.")
    if role_code == SUPER_ADMIN:
        raise HTTPException(
            status_code=403,
            detail=(
                "System Administrator permissions are pre-set by the software developer and "
                "cannot be edited."
            ),
        )
    unknown = sorted(
        set(payload.permissions) - {code for code, _, _ in PERMISSION_CATALOGUE}
    )
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="Unknown permission code(s): " + ", ".join(unknown),
        )
    before = sorted(effective_permissions(db, role_code))
    after = sorted(set_role_permissions(db, role_code, payload.permissions, actor=user))
    audit_service.record(
        db,
        event_type="EDIT",
        entity_type="role",
        entity_id=role_code,
        actor=user,
        field_changed="permissions",
        before={"permissions": before},
        after={"permissions": after},
    )
    db.commit()
    return {
        "code": definition.code,
        "name": definition.name,
        "description": definition.description,
        "rank": definition.rank,
        "locked": False,
        "customised": True,
        "permission_count": len(after),
        "permissions": after,
    }


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
