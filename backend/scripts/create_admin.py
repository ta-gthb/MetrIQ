"""Create or update the Super Admin account (PRD 20.2).

    python backend/scripts/create_admin.py --email admin@metriq.local \
        --name "Platform Administrator" --password "ChangeMe123!"

When --password is omitted a strong temporary password is generated and printed
once. Passwords are never written to the database in clear text.
"""

from __future__ import annotations

import argparse

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._bootstrap import banner, ok  # noqa: E402
from scripts.seed_identity import seed_roles_and_permissions  # noqa: E402

from app.database import session_scope  # noqa: E402
from app.models import Laboratory, User  # noqa: E402
from app.security.passwords import generate_temporary_password, hash_password  # noqa: E402
from app.security.permissions import SUPER_ADMIN  # noqa: E402


def main() -> int:
    banner("MetrIQ - create Super Admin")
    parser = argparse.ArgumentParser(description="Create or update the MetrIQ Super Admin account.")
    parser.add_argument("--email", default="admin@metriq.local")
    parser.add_argument("--name", default="Platform Administrator")
    parser.add_argument("--password", default=None)
    parser.add_argument("--laboratory-code", default=None)
    args = parser.parse_args()

    password = args.password or generate_temporary_password()
    generated = args.password is None

    from sqlalchemy import select

    with session_scope() as db:
        seed_roles_and_permissions(db)
        laboratory = None
        if args.laboratory_code:
            laboratory = db.execute(
                select(Laboratory).where(Laboratory.code == args.laboratory_code)
            ).scalars().first()
            if laboratory is None:
                print(f"  [!!] laboratory '{args.laboratory_code}' was not found; continuing without one")

        user = db.execute(select(User).where(User.email == args.email.lower())).scalars().first()
        if user is None:
            user = User(
                email=args.email.lower(),
                full_name=args.name,
                role_code=SUPER_ADMIN,
                is_active=True,
                is_email_verified=True,
                auth_provider="local",
                laboratory_id=laboratory.id if laboratory else None,
            )
            db.add(user)
            action = "created"
        else:
            user.role_code = SUPER_ADMIN
            user.is_active = True
            action = "updated"
        user.full_name = args.name
        user.password_hash = hash_password(password)
        db.flush()
        ok(f"Super Admin {action}: {user.email}")

    if generated:
        print()
        print(f"  Temporary password (shown once): {password}")
        print("  Change it immediately after the first sign-in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
