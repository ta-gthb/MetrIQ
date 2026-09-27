"""The role and permission catalogue (PRD Appendix A, FR-02).

Idempotent: re-running refreshes role names, ranks and permission grants
without touching user accounts. It must run before users are created, because
`users.role_code` is a foreign key into `roles`. Shared by
backend/scripts/seed_identity.py and the start-up bootstrap.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import Permission, Role, RolePermission
from app.security.permissions import PERMISSION_CATALOGUE, ROLE_DEFINITIONS


def seed_roles_and_permissions(db) -> tuple[int, int]:
    """Upsert every permission, role and role-permission grant."""

    for code, description, category in PERMISSION_CATALOGUE:
        permission = db.get(Permission, code)
        if permission is None:
            db.add(Permission(code=code, description=description, category=category))
        else:
            permission.description = description
            permission.category = category
    db.flush()

    granted = 0
    for code, definition in ROLE_DEFINITIONS.items():
        role = db.get(Role, code)
        if role is None:
            role = Role(code=code, name=definition.name, description=definition.description,
                        rank=definition.rank)
            db.add(role)
        else:
            role.name = definition.name
            role.description = definition.description
            role.rank = definition.rank
        db.flush()

        existing = {
            item.permission_code
            for item in db.execute(
                select(RolePermission).where(RolePermission.role_code == code)
            ).scalars()
        }
        for permission_code in sorted(definition.permissions - existing):
            db.add(RolePermission(role_code=code, permission_code=permission_code))
            granted += 1
        for permission_code in sorted(existing - definition.permissions):
            stale = db.execute(
                select(RolePermission).where(
                    RolePermission.role_code == code,
                    RolePermission.permission_code == permission_code,
                )
            ).scalars().first()
            if stale is not None:
                db.delete(stale)
    db.flush()
    return len(ROLE_DEFINITIONS), granted
