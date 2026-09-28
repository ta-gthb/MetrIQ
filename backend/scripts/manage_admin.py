"""Create and manage Super Admin credentials (PRD 20.2).

Only accounts whose role is SUPER_ADMIN are touched. The script runs against
whatever DATABASE_URL points at, so the same command manages a local SQLite
file or the deployed database - the supported way to administer a Render
instance, whose free plan offers no shell:

    python backend/scripts/manage_admin.py create --email me@lab.example
    python backend/scripts/manage_admin.py list
    python backend/scripts/manage_admin.py set-password --email me@lab.example
    python backend/scripts/manage_admin.py set-email --email me@lab.example --new-email boss@lab.example
    python backend/scripts/manage_admin.py disable --email me@lab.example
    python backend/scripts/manage_admin.py delete --email me@lab.example --yes

Pass --url "<connection string>" to target a database without exporting
DATABASE_URL first, and --schema <name> to keep MetrIQ's tables in a dedicated
PostgreSQL schema when the database is shared with another application.
Passwords are stored hashed and must be at least 8 characters; when omitted a
strong password is generated and printed once.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._bootstrap import banner, ok, warn  # noqa: E402

MIN_PASSWORD_LENGTH = 8

_CONTEXT: SimpleNamespace | None = None


def context() -> SimpleNamespace:
    """Application imports, resolved only after --url has reached the environment."""
    global _CONTEXT
    if _CONTEXT is None:
        from sqlalchemy import select

        from app.config import settings
        from app.database import engine, session_scope
        from app.models import Laboratory, User
        from app.security.passwords import generate_temporary_password, hash_password
        from app.security.permissions import SUPER_ADMIN
        from app.services.reference_data.identity import seed_roles_and_permissions

        _CONTEXT = SimpleNamespace(
            select=select,
            settings=settings,
            engine=engine,
            session_scope=session_scope,
            Laboratory=Laboratory,
            User=User,
            generate_temporary_password=generate_temporary_password,
            hash_password=hash_password,
            SUPER_ADMIN=SUPER_ADMIN,
            seed_roles_and_permissions=seed_roles_and_permissions,
        )
    return _CONTEXT


@dataclass
class AdminResult:
    """Outcome of :func:`upsert_super_admin`."""

    user: Any
    created: bool
    password: str
    generated: bool
    previous_role: str | None = None


def normalise_email(value: str | None) -> str:
    return (value or "").strip().lower()


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def find_super_admin(db, email: str):
    """The account for ``email``, which must exist and already be a Super Admin."""
    ctx = context()
    address = normalise_email(email)
    user = db.execute(ctx.select(ctx.User).where(ctx.User.email == address)).scalars().first()
    if user is None:
        raise LookupError(f"no account found for {address!r}")
    if user.role_code != ctx.SUPER_ADMIN:
        raise PermissionError(
            f"{user.email} is a {user.role_code} account; manage_admin.py only touches SUPER_ADMIN"
        )
    return user


def active_super_admins(db) -> list:
    ctx = context()
    return list(
        db.execute(
            ctx.select(ctx.User).where(
                ctx.User.role_code == ctx.SUPER_ADMIN, ctx.User.is_active.is_(True)
            )
        ).scalars().all()
    )


def ensure_not_last_active(db, user, action: str) -> None:
    """Refuse an action that would remove the last usable Super Admin login."""
    if user.is_active and len(active_super_admins(db)) <= 1:
        raise RuntimeError(
            f"refusing to {action} the last active Super Admin - it would lock everyone out"
        )


def upsert_super_admin(
    db,
    *,
    email: str,
    full_name: str = "Platform Administrator",
    password: str | None = None,
    laboratory_code: str | None = None,
) -> AdminResult:
    """Create or refresh a Super Admin account. Shared with reinit_db.py."""
    ctx = context()
    address = normalise_email(email)
    if not address or "@" not in address:
        raise ValueError(f"{address!r} is not a valid email address")

    generated = password is None
    password = password or ctx.generate_temporary_password()
    validate_password(password)

    # users.role_code is a foreign key into roles, so the catalogue comes first.
    ctx.seed_roles_and_permissions(db)

    laboratory = None
    if laboratory_code:
        laboratory = db.execute(
            ctx.select(ctx.Laboratory).where(ctx.Laboratory.code == laboratory_code)
        ).scalars().first()
        if laboratory is None:
            raise LookupError(f"laboratory {laboratory_code!r} was not found")

    user = db.execute(ctx.select(ctx.User).where(ctx.User.email == address)).scalars().first()
    created = user is None
    previous_role = None
    if created:
        user = ctx.User(email=address, auth_provider="local")
        db.add(user)
    else:
        previous_role = user.role_code

    user.full_name = full_name
    user.role_code = ctx.SUPER_ADMIN
    user.is_active = True
    user.is_email_verified = True
    if laboratory is not None:
        user.laboratory_id = laboratory.id
    user.password_hash = ctx.hash_password(password)
    db.flush()
    return AdminResult(
        user=user, created=created, password=password, generated=generated,
        previous_role=previous_role,
    )


def command_create(args) -> int:
    ctx = context()
    with ctx.session_scope() as db:
        result = upsert_super_admin(
            db,
            email=args.email,
            full_name=args.name,
            password=args.password,
            laboratory_code=args.laboratory_code,
        )
        ok(f"Super Admin {'created' if result.created else 'updated'}: {result.user.email}")
    if result.previous_role and result.previous_role != ctx.SUPER_ADMIN:
        warn(f"this account was a {result.previous_role} and has been promoted to Super Admin")
    if result.generated:
        print()
        print(f"  Temporary password (shown once): {result.password}")
        print("  Store it in a password manager, then change it after the first sign-in.")
    return 0


def command_list(args) -> int:
    ctx = context()
    with ctx.session_scope() as db:
        users = db.execute(
            ctx.select(ctx.User).where(ctx.User.role_code == ctx.SUPER_ADMIN).order_by(ctx.User.email)
        ).scalars().all()
        rows = [
            (
                user.email,
                user.full_name,
                "active" if user.is_active else "disabled",
                user.last_login_at.strftime("%Y-%m-%d %H:%M") if user.last_login_at else "never",
            )
            for user in users
        ]
    if not rows:
        warn("no Super Admin accounts exist yet")
        print("  Create one with: manage_admin.py create --email you@lab.example")
        return 0
    print(f"  {'EMAIL':<32} {'NAME':<26} {'STATE':<9} LAST LOGIN")
    for email, name, state, last_login in rows:
        print(f"  {email:<32} {name[:25]:<26} {state:<9} {last_login}")
    ok(f"{len(rows)} Super Admin account(s)")
    return 0


def command_set_password(args) -> int:
    ctx = context()
    password = args.password or ctx.generate_temporary_password()
    validate_password(password)
    with ctx.session_scope() as db:
        user = find_super_admin(db, args.email)
        user.password_hash = ctx.hash_password(password)
        ok(f"password updated for {user.email}")
    if args.password is None:
        print()
        print(f"  New password (shown once): {password}")
    return 0


def command_set_email(args) -> int:
    ctx = context()
    address = normalise_email(args.new_email)
    if not address or "@" not in address:
        raise ValueError(f"{address!r} is not a valid email address")
    with ctx.session_scope() as db:
        user = find_super_admin(db, args.email)
        if user.email == address:
            ok(f"{address} is already the login address")
            return 0
        clash = db.execute(ctx.select(ctx.User).where(ctx.User.email == address)).scalars().first()
        if clash is not None:
            raise ValueError(f"{address} already belongs to another {clash.role_code} account")
        previous, user.email = user.email, address
        ok(f"login email changed: {previous} -> {address}")
    return 0


def command_set_active(args, active: bool) -> int:
    ctx = context()
    with ctx.session_scope() as db:
        user = find_super_admin(db, args.email)
        if not active:
            ensure_not_last_active(db, user, "disable")
        user.is_active = active
        ok(f"{user.email} is now {'active' if active else 'disabled'}")
    return 0


def command_delete(args) -> int:
    ctx = context()
    with ctx.session_scope() as db:
        user = find_super_admin(db, args.email)
        if not args.yes:
            warn(f"this deletes {user.email}; re-run with --yes to confirm")
            return 1
        ensure_not_last_active(db, user, "delete")
        db.delete(user)
        ok(f"deleted Super Admin {user.email}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    # SUPPRESS keeps the main parser's --url from being clobbered by the
    # subparser's default when the flag is given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--url", default=argparse.SUPPRESS,
        help="database to manage; defaults to DATABASE_URL (or .env)",
    )
    common.add_argument(
        "--schema", default=argparse.SUPPRESS,
        help="PostgreSQL schema for MetrIQ's tables (DB_SCHEMA) when the database is shared",
    )

    parser = argparse.ArgumentParser(
        prog="manage_admin.py",
        description="Create and manage MetrIQ Super Admin credentials.",
        parents=[common],
    )
    subcommands = parser.add_subparsers(dest="command")

    create = subcommands.add_parser("create", parents=[common], help="create or update the Super Admin")
    create.add_argument("--email", required=True)
    create.add_argument("--name", default="Platform Administrator")
    create.add_argument("--password", default=None)
    create.add_argument("--laboratory-code", default=None)
    create.set_defaults(func=command_create)

    listing = subcommands.add_parser("list", parents=[common], help="list Super Admin accounts")
    listing.set_defaults(func=command_list)

    rotate = subcommands.add_parser("set-password", parents=[common], help="rotate the password")
    rotate.add_argument("--email", required=True)
    rotate.add_argument("--password", default=None)
    rotate.set_defaults(func=command_set_password)

    rename = subcommands.add_parser("set-email", parents=[common], help="change the login email")
    rename.add_argument("--email", required=True)
    rename.add_argument("--new-email", required=True)
    rename.set_defaults(func=command_set_email)

    enable = subcommands.add_parser("enable", parents=[common], help="re-activate the account")
    enable.add_argument("--email", required=True)
    enable.set_defaults(func=lambda args: command_set_active(args, True))

    disable = subcommands.add_parser("disable", parents=[common], help="block sign-in for the account")
    disable.add_argument("--email", required=True)
    disable.set_defaults(func=lambda args: command_set_active(args, False))

    delete = subcommands.add_parser("delete", parents=[common], help="remove the account")
    delete.add_argument("--email", required=True)
    delete.add_argument("--yes", action="store_true", help="required: confirms the deletion")
    delete.set_defaults(func=command_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1

    url = getattr(args, "url", None)
    if url:
        os.environ["DATABASE_URL"] = url
    schema = getattr(args, "schema", None)
    if schema:
        os.environ["DB_SCHEMA"] = schema

    banner("MetrIQ - Super Admin management")
    ctx = context()
    print(f"  target: {ctx.engine.url.render_as_string(hide_password=True)}")
    if ctx.settings.DB_SCHEMA:
        print(f"  schema: {ctx.settings.DB_SCHEMA}")
    print()

    try:
        # Provision the schema first, so a fresh or shared database works
        # without a separate init step, and report incompatible tables clearly
        # instead of failing later with a raw column error.
        from app.services.reference_data import bootstrap

        bootstrap.ensure_schema()
        bootstrap.require_schema()
        return args.func(args) or 0
    except (LookupError, PermissionError, ValueError, RuntimeError) as exc:
        print(f"  [!!] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
