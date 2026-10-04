"""Drop, recreate and reseed the database from any machine (PRD 20.2).

Render's free plan has no shell and no pre-deploy command, so this is the
supported way to (re)initialise the deployed database from your own device:

    python backend/scripts/reinit_db.py

It can run with no arguments at all, which is the easiest way to do this from
another machine: it asks for the connection string, the schema and the new
Super Admin account, and makes you type REINITIALISE before anything happens.
The flags below remain for scripted use:

    python backend/scripts/reinit_db.py --yes
    python backend/scripts/reinit_db.py --url "postgresql://...:5432/postgres" --yes
    python backend/scripts/reinit_db.py --yes --admin-email me@lab.example

DESTRUCTIVE: every MetrIQ table in the target schema is dropped, so all cases,
users, audit history and generated report rows are deleted. Objects already in the storage bucket are
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

from scripts._bootstrap import (  # noqa: E402
    banner,
    confirm,
    interactive,
    mask_url,
    ok,
    prompt,
    prompt_secret,
    read_env_file,
    remember_env,
    warn,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reinit_db.py",
        description="Drop, recreate and reseed the MetrIQ database.",
    )
    parser.add_argument("--url", default=None, help="database to reinitialise; defaults to DATABASE_URL")
    parser.add_argument(
        "--schema", default=None,
        help="PostgreSQL schema for MetrIQ's tables (DB_SCHEMA) when the database is shared",
    )
    parser.add_argument("--yes", action="store_true", help="required: confirms the data loss")
    parser.add_argument("--force", action="store_true", help="also run when ENVIRONMENT=production")
    parser.add_argument("--admin-email", default="admin@metriq.local")
    parser.add_argument("--admin-name", default="Platform Administrator")
    parser.add_argument("--admin-password", default=None)
    parser.add_argument("--no-admin", action="store_true", help="do not recreate a Super Admin")
    return parser


def collect_inputs(args) -> bool:
    """Guided session: gather the target and the account. False = cancelled."""
    banner("MetrIQ - reinitialise database (DESTRUCTIVE)")

    stored = read_env_file()
    from app.config import DEPLOYMENT_DEFAULTS

    deployment_defaults = read_env_file(DEPLOYMENT_DEFAULTS)
    configured = (
        os.environ.get("DATABASE_URL")
        or stored.get("DATABASE_URL")
        or deployment_defaults.get("DATABASE_URL")
        or ""
    )

    # Once the target is remembered in backend/.env this is a single keystroke.
    if configured:
        print(f"  Target: {mask_url(configured)}")
        if confirm("Use this database?", default=True):
            args.url = configured
        else:
            args.url = prompt("Database URL", allow_blank=True) or configured
    else:
        print("  No database is configured yet. Text typed here does not reach your")
        print("  shell history, and the password is never echoed.")
        print()
        args.url = prompt("Database URL", allow_blank=True)
        if not args.url:
            warn("no DATABASE_URL is set and none was entered")
            return False

    print()
    print("  Use a dedicated schema such as 'metriq' when this database is shared with")
    print("  another application, so only MetrIQ's own tables are touched.")
    default_schema = os.environ.get("DB_SCHEMA") or stored.get("DB_SCHEMA") or "public"
    args.schema = prompt("Schema", default=default_schema)

    # Anything new is offered back to .env, so later runs need no input at all.
    if stored.get("DATABASE_URL") != args.url or (stored.get("DB_SCHEMA") or "") != args.schema:
        if confirm("Save these settings to backend/.env for next time?", default=True):
            ok(f"saved to {remember_env({'DATABASE_URL': args.url, 'DB_SCHEMA': args.schema})}")

    print()
    print("  " + "-" * 68)
    warn(f"every MetrIQ table in {mask_url(args.url or configured)} (schema {args.schema})")
    warn("will be dropped: all cases, users, audit history and generated reports.")
    print("  " + "-" * 68)
    if prompt("Type REINITIALISE to continue", allow_blank=True) != "REINITIALISE":
        return False
    args.yes = True

    print()
    if confirm("Recreate a Super Admin account as well?", default=True):
        args.admin_email = prompt("Admin email", default=args.admin_email)
        args.admin_name = prompt("Admin display name", default=args.admin_name)
        secret = prompt_secret("Admin password (blank to generate one)")
        if secret:
            args.admin_password = secret
    else:
        args.no_admin = True

    return True


def drop_rls_policies(connection, schema: str | None) -> int:
    """Remove MetrIQ's cross-table RLS policies before dropping their tables."""
    if connection.dialect.name != "postgresql":
        return 0

    from sqlalchemy import inspect

    from app.security.rls import POLICY_NAME, TENANT_TABLES

    inspector = inspect(connection)
    existing = set(inspector.get_table_names(schema=schema))
    preparer = connection.dialect.identifier_preparer
    policy = preparer.quote(POLICY_NAME)
    removed = 0
    for table in sorted(TENANT_TABLES):
        if table not in existing:
            continue
        qualified_table = preparer.quote(table)
        if schema:
            qualified_table = f"{preparer.quote_schema(schema)}.{qualified_table}"
        connection.exec_driver_sql(
            f"DROP POLICY IF EXISTS {policy} ON {qualified_table}"
        )
        removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]

    # No flags at all on a real terminal means "ask me everything".
    guided = not argv and interactive()
    if guided:
        args = parser.parse_args([])
        print()
        if not collect_inputs(args):
            print()
            warn("cancelled - nothing was changed")
            return 1
    else:
        args = parser.parse_args(argv)

    if args.url:
        os.environ["DATABASE_URL"] = args.url
    if args.schema:
        os.environ["DB_SCHEMA"] = args.schema

    from app.config import settings
    from app.database import engine, session_scope
    from app.models import Base
    from app.services.reference_data import bootstrap

    if not guided:
        banner("MetrIQ - reinitialise database (DESTRUCTIVE)")
    target = engine.url.render_as_string(hide_password=True)
    print(f"  target: {target}")
    if settings.DB_SCHEMA:
        print(f"  schema: {settings.DB_SCHEMA}")
    print()

    if not args.yes:
        warn("this drops every table; re-run with --yes to confirm")
        return 1
    if settings.ENVIRONMENT == "production" and not args.force:
        if guided and confirm(f"ENVIRONMENT is 'production'. Reinitialise {target} anyway?"):
            args.force = True
        else:
            warn(f"ENVIRONMENT is 'production'; add --force if {target} really is the intended target")
            return 2

    connected, detail = bootstrap.probe_connection()
    if not connected:
        warn(f"cannot reach {target}: {detail}")
        print("  Check DATABASE_URL. For Supabase use the session pooler on port 5432 and")
        print("  percent-encode reserved characters in the password (@ -> %40, : -> %3A, / -> %2F).")
        return 3

    # Only tables that already exist count here: a database that has never been
    # initialised is not a shared one.
    conflicts = bootstrap.schema_problems(include_missing=False)
    if conflicts:
        warn("these pre-existing tables do not match the MetrIQ schema and will be replaced:")
        for problem in conflicts[:4]:
            print(f"        - {problem}")
        if guided:
            # The operator already answered the prompts, so never drop tables
            # that belong to whatever else lives in this database.
            warn("stopping: this looks like a shared database")
            warn(f"re-run and enter the dedicated schema (for example 'metriq') "
                 f"instead of {settings.DB_SCHEMA or 'public'}")
            return 1
        warn("if this database is shared with another application, stop now and use")
        warn("DB_SCHEMA=<name> instead, so only MetrIQ's own tables are touched")

    warn(f"dropping all tables in {target}")
    with engine.begin() as connection:
        removed_policies = drop_rls_policies(connection, settings.DB_SCHEMA)
        if removed_policies:
            ok(f"removed {removed_policies} MetrIQ row-level security policies")
        Base.metadata.drop_all(bind=connection)
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
        print(f"  user ID: {result.user_id}")
        if result.generated:
            print()
            print(f"  Temporary password (shown once): {result.password}")

    print()
    ok("database reinitialised")
    print("  Next: python backend/scripts/manage_admin.py list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
