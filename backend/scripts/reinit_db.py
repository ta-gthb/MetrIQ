"""Drop, recreate and reseed the database from any machine (PRD 20.2).

Render's free plan has no shell and no pre-deploy command, so this is the
supported way to (re)initialise the deployed database from your own device:

    python backend/scripts/reinit_db.py --yes
    python backend/scripts/reinit_db.py --url "postgresql://...:5432/postgres" --yes
    python backend/scripts/reinit_db.py --yes --admin-email me@lab.example

DESTRUCTIVE: every table is dropped, so all cases, users, audit history and
generated report rows are deleted. Objects already in the storage bucket are
left where they are; they simply become unreferenced. After a re-run the
instance is immediately usable again, because the reference catalogue and a
Super Admin account are recreated in the same pass.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._bootstrap import banner, ok, warn  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reinit_db.py",
        description="Drop, recreate and reseed the MetrIQ database.",
    )
    parser.add_argument("--url", default=None, help="database to reinitialise; defaults to DATABASE_URL")
    parser.add_argument("--yes", action="store_true", help="required: confirms the data loss")
    parser.add_argument("--force", action="store_true", help="also run when ENVIRONMENT=production")
    parser.add_argument("--admin-email", default="admin@metriq.local")
    parser.add_argument("--admin-name", default="Platform Administrator")
    parser.add_argument("--admin-password", default=None)
    parser.add_argument("--no-admin", action="store_true", help="do not recreate a Super Admin")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.url:
        os.environ["DATABASE_URL"] = args.url

    from app.config import settings
    from app.database import engine, session_scope
    from app.models import Base
    from app.services.reference_data import bootstrap

    banner("MetrIQ - reinitialise database (DESTRUCTIVE)")
    target = engine.url.render_as_string(hide_password=True)
    print(f"  target: {target}")
    print()

    if not args.yes:
        warn("this drops every table; re-run with --yes to confirm")
        return 1
    if settings.ENVIRONMENT == "production" and not args.force:
        warn(f"ENVIRONMENT is 'production'; add --force if {target} really is the intended target")
        return 2

    connected, detail = bootstrap.probe_connection()
    if not connected:
        warn(f"cannot reach {target}: {detail}")
        print("  Check DATABASE_URL. For Supabase use the session pooler on port 5432 and")
        print("  percent-encode reserved characters in the password (@ -> %40, : -> %3A, / -> %2F).")
        return 3

    warn(f"dropping all tables in {target}")
    Base.metadata.drop_all(bind=engine)
    tables = bootstrap.ensure_schema()
    ok(f"schema recreated with {tables} tables")

    with session_scope() as db:
        summary = bootstrap.seed_reference_data(db)
    ok(
        f"reference data seeded: {summary['roles']} roles, "
        f"{summary['test_definitions']} test definitions"
    )

    if args.no_admin:
        warn("no Super Admin created; run manage_admin.py create before signing in")
    else:
        from scripts.manage_admin import upsert_super_admin

        with session_scope() as db:
            result = upsert_super_admin(
                db,
                email=args.admin_email,
                full_name=args.admin_name,
                password=args.admin_password,
            )
        ok(f"Super Admin {'created' if result.created else 'updated'}: {result.user.email}")
        if result.generated:
            print()
            print(f"  Temporary password (shown once): {result.password}")

    print()
    ok("database reinitialised")
    print("  Next: python backend/scripts/manage_admin.py list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
