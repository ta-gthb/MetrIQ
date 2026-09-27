"""Create or update the Super Admin account (PRD 20.2).

    python backend/scripts/create_admin.py --email admin@metriq.local \
        --name "Platform Administrator" --password "ChangeMe123!"

Kept for convenience; manage_admin.py is the fuller tool (list, rotate
password, rename, enable/disable, delete). When --password is omitted a strong
temporary password is generated and printed once, and passwords must be at
least 8 characters. Nothing is ever stored in clear text.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._bootstrap import banner, ok, warn  # noqa: E402


def main() -> int:
    banner("MetrIQ - create Super Admin")
    parser = argparse.ArgumentParser(description="Create or update the MetrIQ Super Admin account.")
    parser.add_argument("--email", default="admin@metriq.local")
    parser.add_argument("--name", default="Platform Administrator")
    parser.add_argument("--password", default=None)
    parser.add_argument("--laboratory-code", default=None)
    args = parser.parse_args()

    from app.database import session_scope
    from scripts.manage_admin import upsert_super_admin

    with session_scope() as db:
        result = upsert_super_admin(
            db,
            email=args.email,
            full_name=args.name,
            password=args.password,
            laboratory_code=args.laboratory_code,
        )
        ok(f"Super Admin {'created' if result.created else 'updated'}: {result.user.email}")

    if result.previous_role and result.previous_role != "SUPER_ADMIN":
        warn(f"that account was a {result.previous_role} and has been promoted to Super Admin")

    if result.generated:
        print()
        print(f"  Temporary password (shown once): {result.password}")
        print("  Change it immediately after the first sign-in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
