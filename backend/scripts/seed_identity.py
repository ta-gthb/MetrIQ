"""Seed the role and permission catalogue (PRD Appendix A, FR-02).

    python backend/scripts/seed_identity.py

Idempotent: re-running refreshes role names, ranks and permission grants
without touching user accounts. Run this before creating users, because
`users.role_code` is a foreign key into `roles`.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._bootstrap import banner, ok  # noqa: E402

from app.database import session_scope  # noqa: E402
from app.security.permissions import PERMISSION_CATALOGUE  # noqa: E402
from app.services.reference_data.identity import (  # noqa: E402
    seed_roles_and_permissions,
)

__all__ = ["seed_roles_and_permissions", "main"]


def main() -> int:
    banner("MetrIQ - seed roles and permissions")
    with session_scope() as db:
        roles, granted = seed_roles_and_permissions(db)
    ok(f"roles: {roles} definitions")
    ok(f"permissions: {len(PERMISSION_CATALOGUE)} codes, {granted} new grants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
