"""Effective role permissions, resolved from the database (FR-02).

The catalogue in ``app.security.permissions`` is the shipped default and stays
the reference for a fresh installation. Once a System Administrator edits a role in the
administration console, the database rows for that role become the source of
truth: the reference-data seeding sees the customisation marker and leaves the
role alone, so an edit survives restarts and redeploys. A short-lived cache
keeps the per-request check cheap; every write invalidates it immediately, so
a change takes effect across the deployment as soon as it is saved.
"""

from __future__ import annotations

import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RolePermission, SystemSetting
from app.security.permissions import (
    CUSTOMISED_SETTING_PREFIX,
    SUPER_ADMIN,
    role_permissions as static_role_permissions,
)

CACHE_TTL_SECONDS = 5.0

_lock = threading.Lock()
_cache: dict[str, tuple[float, frozenset[str]]] = {}


def _cached(role_code: str) -> frozenset[str] | None:
    with _lock:
        entry = _cache.get(role_code)
        if entry and (time.monotonic() - entry[0]) < CACHE_TTL_SECONDS:
            return entry[1]
    return None


def _store(role_code: str, value: frozenset[str]) -> frozenset[str]:
    with _lock:
        _cache[role_code] = (time.monotonic(), value)
    return value


def invalidate(role_code: str | None = None) -> None:
    with _lock:
        if role_code is None:
            _cache.clear()
        else:
            _cache.pop(role_code, None)


def is_customised(db: Session, role_code: str) -> bool:
    """True when a System Administrator has edited this role's permissions."""
    if role_code == SUPER_ADMIN:
        return False
    marker = db.execute(
        select(SystemSetting).where(
            SystemSetting.key == CUSTOMISED_SETTING_PREFIX + role_code
        )
    ).scalars().first()
    return marker is not None


def effective_permissions(db: Session, role_code: str) -> frozenset[str]:
    """The permissions a role holds right now, customised values included."""
    if role_code == SUPER_ADMIN:
        # Pre-set by the software developer and deliberately not editable.
        return static_role_permissions(role_code)
    cached = _cached(role_code)
    if cached is not None:
        return cached
    try:
        rows = frozenset(
            db.execute(
                select(RolePermission.permission_code).where(
                    RolePermission.role_code == role_code
                )
            ).scalars().all()
        )
    except Exception:  # pragma: no cover - schema not ready yet
        return static_role_permissions(role_code)
    if rows:
        return _store(role_code, rows)
    if is_customised(db, role_code):
        # An intentional empty set must not silently fall back to the default.
        return _store(role_code, frozenset())
    return _store(role_code, static_role_permissions(role_code))


def set_role_permissions(
    db: Session, role_code: str, permissions: list[str], *, actor=None
) -> frozenset[str]:
    """Replace the role's grants with ``permissions`` and mark the role customised."""
    wanted = frozenset(permissions)
    existing = db.execute(
        select(RolePermission).where(RolePermission.role_code == role_code)
    ).scalars().all()
    for row in existing:
        db.delete(row)
    db.flush()
    for code in sorted(wanted):
        db.add(RolePermission(role_code=role_code, permission_code=code))
    marker = db.execute(
        select(SystemSetting).where(
            SystemSetting.key == CUSTOMISED_SETTING_PREFIX + role_code
        )
    ).scalars().first()
    if marker is None:
        marker = SystemSetting(
            key=CUSTOMISED_SETTING_PREFIX + role_code,
            category="administration",
            description=(
                "Set when a System Administrator edits this role's permissions; the "
                "reference-data seeding then leaves the role untouched."
            ),
        )
        db.add(marker)
    marker.value = {
        "customised": True,
        "updated_by": str(getattr(actor, "id", "") or ""),
    }
    db.flush()
    invalidate(role_code)
    return wanted
