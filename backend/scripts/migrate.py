"""Schema migration CLI (project-audit item 11).

Alembic owns the schema. This is the operator entry point to it:

    python backend/scripts/migrate.py                 # asks what to do
    python backend/scripts/migrate.py current
    python backend/scripts/migrate.py upgrade [revision]
    python backend/scripts/migrate.py stamp [revision]
    python backend/scripts/migrate.py revision -m "add x"
    python backend/scripts/migrate.py check
    python backend/scripts/migrate.py history
    python backend/scripts/migrate.py downgrade <revision>

Pass --url "<connection string>" to target a database without exporting
DATABASE_URL, and --schema <name> when MetrIQ shares a PostgreSQL database with
another application. Anything passed as a flag is never asked for again, so the
same script works unattended in a pipeline.

`check` is the CI gate: it exits non-zero when the database is not at head, so a
release cannot ship a schema the code does not expect. The deployed service runs
the same upgrade itself at boot (see app/services/reference_data/bootstrap.py),
because Render's free plan offers no pre-deploy command.
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
    ok,
    prompt,
    prompt_choice,
    warn,
)

COMMANDS = (
    ("current", "show the applied revision and the head"),
    ("upgrade", "apply every migration up to head"),
    ("stamp", "record a revision without running its migrations"),
    ("revision", "autogenerate a new migration from the models"),
    ("check", "exit non-zero unless the database is at head"),
    ("history", "list the migration tree"),
    ("downgrade", "revert to an earlier revision (destructive)"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MetrIQ schema migrations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", help="DATABASE_URL to use instead of the configured one")
    parser.add_argument("--schema", help="DB_SCHEMA to use instead of the configured one")
    parser.add_argument("command", nargs="?", choices=[name for name, _ in COMMANDS])
    parser.add_argument("revision", nargs="?", help="target revision, when the command takes one")
    parser.add_argument("-m", "--message", help="message for `revision`")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompts")
    return parser


def apply_overrides(args: argparse.Namespace) -> None:
    if args.url:
        os.environ["DATABASE_URL"] = args.url
    if args.schema:
        os.environ["DB_SCHEMA"] = args.schema


def collect_inputs(args: argparse.Namespace) -> bool:
    """Ask for anything a flag did not already supply. False cancels."""
    if not args.command:
        print()
        args.command = prompt_choice("What do you want to do?", COMMANDS, default="current")
        if args.command == "downgrade":
            args.revision = prompt("Target revision")
            if not args.revision:
                return False
        elif args.command == "revision":
            args.message = prompt("Migration message")
            if not args.message:
                return False
    if args.command == "revision" and not args.message:
        args.message = prompt("Migration message")
    if args.command == "downgrade" and not args.revision:
        args.revision = prompt("Target revision")
        if not args.revision:
            return False
    return True


def describe(url: str, schema: str | None) -> None:
    print(f"  target: {url}")
    print(f"  schema: {schema or '<database default>'}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    guided = argv is None and interactive()
    if guided:
        args = parser.parse_args([])
        if not collect_inputs(args):
            print()
            warn("cancelled - nothing was changed")
            return 1
    else:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    apply_overrides(args)

    from app.database import engine
    from app.migrations import (
        alembic_config,
        current_revision,
        database_revision_summary,
        downgrade,
        head_revision,
        stamp,
        upgrade,
    )

    if not guided:
        banner("MetrIQ - schema migrations")
    describe(engine.url.render_as_string(hide_password=True), os.environ.get("DB_SCHEMA"))

    command = args.command or "current"

    if command == "history":
        from alembic import command as alembic_command

        alembic_command.history(alembic_config(), verbose=True)
        return 0

    if command == "revision":
        from alembic import command as alembic_command

        if not args.message:
            warn("a migration needs a message: pass -m \"what changed\"")
            return 2
        alembic_command.revision(alembic_config(), message=args.message, autogenerate=True)
        ok("migration generated - review it before committing")
        return 0

    if command == "current":
        print(f"  applied: {current_revision() or 'unmanaged'}")
        print(f"  head:    {head_revision() or 'unknown'}")
        print()
        for key, value in database_revision_summary().items():
            print(f"  {key}: {value}")
        return 0

    if command == "check":
        summary = database_revision_summary()
        if summary.get("schema_up_to_date"):
            ok(f"database is at {summary['schema_revision']}")
            return 0
        warn(
            f"database is at {summary.get('schema_revision')} but head is "
            f"{summary.get('schema_revision_head')} - run `migrate.py upgrade`"
        )
        return 1

    if command == "upgrade":
        target = args.revision or "head"
        upgrade(target)
        ok(f"upgraded to {current_revision() or target}")
        return 0

    if command == "stamp":
        target = args.revision or prompt("Revision to record", default="0001")
        if target != "0001" and not args.yes:
            warn("stamping records a revision without running its migrations; the schema must already match")
            if guided and not confirm(f"Record revision {target} without running it?"):
                return 1
        stamp(target)
        ok(f"recorded revision {target}")
        return 0

    if command == "downgrade":
        if not args.revision:
            warn("downgrade needs a target revision (use 'base' to drop everything)")
            return 2
        if not args.yes:
            warn(f"downgrading to {args.revision} drops the tables added since then")
            if guided and not confirm(f"Downgrade to {args.revision}?"):
                return 1
        downgrade(args.revision)
        ok(f"downgraded to {current_revision() or args.revision}")
        return 0

    parser.error(f"unknown command {command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())