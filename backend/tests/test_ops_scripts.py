"""Start-up bootstrap and the operator scripts (PRD 20.2).

Render's free plan has no shell and no pre-deploy command, so the schema and
reference catalogue are provisioned by the application itself and the Super
Admin is managed from a local machine. These tests cover both paths, plus the
destructive remote re-initialisation in an isolated database.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Base, User
from app.security.passwords import verify_password
from app.security.permissions import ENGINEER, SUPER_ADMIN
from app.services.reference_data import bootstrap
from scripts.manage_admin import (
    active_super_admins,
    command_set_password,
    ensure_not_last_active,
    find_super_admin,
    upsert_super_admin,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
API = "/api/v1"
FIXTURE_PASSWORD = "FixturePass1!"


@pytest.fixture
def temporary_super_admin():
    """A Super Admin that is deleted again, leaving the shared data untouched."""
    email = f"ops-{uuid.uuid4().hex[:10]}@lab.example"
    with SessionLocal() as db:
        result = upsert_super_admin(
            db, email=email, full_name="Ops Fixture", password=FIXTURE_PASSWORD
        )
        db.commit()
    yield result
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == email)).scalars().first()
        if user is not None:
            db.delete(user)
            db.commit()


def test_health_reports_database_readiness(client):
    body = client.get("/health").json()
    assert body["database"] == "ok"
    assert body["storage_backend"] == "local"
    assert body["auth_provider"] == "local"
    # Exposed so a deploy can confirm DB_SCHEMA took effect without a shell.
    assert body["schema"] == "public"
    assert body["schema_problems"] == 0


def test_startup_bootstrap_skips_seeding_when_reference_data_is_present(accounts):
    with SessionLocal() as db:
        assert bootstrap.reference_data_present(db) is True

    report = bootstrap.initialise_database()
    assert report["connected"] is True
    assert report["tables"] == len(Base.metadata.tables)
    assert report["reference_data"] == "present"
    assert report["seeded"] == {}


def test_bootstrap_seeding_is_idempotent(accounts):
    """The routine the start-up hook runs can be replayed without side effects."""
    with SessionLocal() as db:
        assert bootstrap.reference_data_present(db) is True
        counts = bootstrap.seed_reference_data(db)
        assert bootstrap.reference_data_present(db) is True
    assert counts["roles"] == 6
    assert counts["test_definitions"] >= 11


def test_manage_admin_create_produces_working_credentials(client, temporary_super_admin):
    result = temporary_super_admin
    assert result.created is True
    assert result.generated is False
    assert verify_password(FIXTURE_PASSWORD, result.user.password_hash)

    response = client.post(
        f"{API}/auth/login",
        json={"email": result.user.email, "password": FIXTURE_PASSWORD},
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["role_code"] == SUPER_ADMIN


def test_manage_admin_set_password_rotates_the_credential(temporary_super_admin):
    email = temporary_super_admin.user.email
    assert command_set_password(SimpleNamespace(email=email, password="RotatedPass2!")) == 0

    with SessionLocal() as db:
        user = find_super_admin(db, email)
        assert verify_password("RotatedPass2!", user.password_hash)
        assert not verify_password(FIXTURE_PASSWORD, user.password_hash)


def test_manage_admin_only_touches_super_admins(accounts):
    with SessionLocal() as db:
        with pytest.raises(PermissionError):
            find_super_admin(db, accounts["emails"][ENGINEER])
        with pytest.raises(LookupError):
            find_super_admin(db, "nobody@lab.example")


def test_manage_admin_refuses_to_remove_the_last_active_super_admin(accounts, temporary_super_admin):
    seeded_email = accounts["emails"][SUPER_ADMIN]
    with SessionLocal() as db:
        extra = find_super_admin(db, temporary_super_admin.user.email)
        assert len(active_super_admins(db)) >= 2
        # Removing one of two is allowed...
        ensure_not_last_active(db, extra, "disable")
        db.delete(extra)
        db.commit()
        # ...but the account that is now the only way in is protected.
        seeded = find_super_admin(db, seeded_email)
        assert seeded.is_active
        with pytest.raises(RuntimeError):
            ensure_not_last_active(db, seeded, "disable")
        with pytest.raises(RuntimeError):
            ensure_not_last_active(db, seeded, "delete")


def test_reinit_db_rebuilds_a_deployment_ready_database(tmp_path):
    """The remote re-initialisation used on hosts without a shell."""
    database = tmp_path / "reinit.db"
    env = {
        **os.environ,
        "ENVIRONMENT": "development",
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "AUTH_PROVIDER": "local",
        "AI_PROVIDER": "stub",
        "JWT_SECRET": "reinit-test-secret-value",
    }
    command = [
        sys.executable,
        "scripts/reinit_db.py",
        "--yes",
        "--admin-email",
        "ops@lab.example",
        "--admin-password",
        "RebuildPass1!",
    ]

    first = subprocess.run(
        command, cwd=BACKEND_DIR, env=env, capture_output=True, text=True, input="",
    )
    assert first.returncode == 0, first.stdout + first.stderr
    assert "database reinitialised" in first.stdout

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"users", "roles", "role_permissions", "test_definitions", "evaluation_cases"} <= tables
        assert connection.execute("SELECT COUNT(*) FROM roles").fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM test_definitions").fetchone()[0] >= 11
        rows = connection.execute("SELECT email, role_code, is_active FROM users").fetchall()
    assert rows == [("ops@lab.example", SUPER_ADMIN, 1)]

    # Re-running is safe: the drop/recreate/seed cycle does not duplicate data.
    second = subprocess.run(
        command, cwd=BACKEND_DIR, env=env, capture_output=True, text=True, input="",
    )
    assert second.returncode == 0, second.stdout + second.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM roles").fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_reinit_db_refuses_without_confirmation(tmp_path):
    database = tmp_path / "unconfirmed.db"
    env = {
        **os.environ,
        "ENVIRONMENT": "development",
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "JWT_SECRET": "reinit-test-secret-value",
    }
    result = subprocess.run(
        [sys.executable, "scripts/reinit_db.py"], cwd=BACKEND_DIR, env=env,
        capture_output=True, text=True, input="",
    )
    assert result.returncode == 1
    assert "--yes" in result.stdout
    assert "REINITIALISE" not in result.stdout.replace("--yes", "")


def test_seeded_reference_rows_fit_their_column_widths(accounts):
    """PostgreSQL enforces VARCHAR(n); SQLite silently accepts longer values.

    A seeded value wider than its declared column makes the ruleset fail to load
    on Supabase with StringDataRightTruncation - and no local SQLite run can
    catch it, so the contract is asserted here instead.
    """
    from app.models import (
        ReportTemplate,
        ReportTemplateVersion,
        Rule,
        RuleVersion,
        Standard,
        StandardVersion,
        TestDefinition,
    )

    models = [
        Standard,
        StandardVersion,
        Rule,
        RuleVersion,
        ReportTemplate,
        ReportTemplateVersion,
        TestDefinition,
    ]
    inspected = 0
    oversized: list[str] = []
    with SessionLocal() as db:
        for model in models:
            for row in db.execute(select(model)).scalars().all():
                inspected += 1
                for column in model.__table__.columns:
                    limit = getattr(column.type, "length", None)
                    value = getattr(row, column.name, None)
                    if limit and isinstance(value, str) and len(value) > limit:
                        oversized.append(
                            f"{model.__name__}.{column.name}: {len(value)} > {limit}"
                        )

    assert inspected > 0, "reference data was not seeded"
    assert oversized == [], "seeded values exceed their column width: " + "; ".join(oversized)


def test_db_schema_setting_is_validated():
    """DB_SCHEMA is interpolated into DDL, so only a plain identifier is allowed."""
    from pydantic import ValidationError

    from app.config import Settings

    assert Settings(DB_SCHEMA="metriq").DB_SCHEMA == "metriq"
    assert Settings(DB_SCHEMA="").DB_SCHEMA is None
    assert Settings(DB_SCHEMA=None).DB_SCHEMA is None
    with pytest.raises(ValidationError):
        Settings(DB_SCHEMA="metriq; DROP TABLE users")
    with pytest.raises(ValidationError):
        Settings(DB_SCHEMA="not a schema")


def test_tables_are_not_schema_qualified_by_default():
    """Schema qualification is opt-in: the suite runs on plain SQLite."""
    assert Base.metadata.schema is None
    assert "users" in Base.metadata.tables


def test_bootstrap_reports_a_mismatch_without_seeding_or_crashing(accounts, monkeypatch):
    """An incompatible table must not abort start-up: report it and carry on."""
    monkeypatch.setattr(bootstrap, "_LAST_REPORT", dict(bootstrap.last_report()))
    monkeypatch.setattr(
        bootstrap,
        "schema_problems",
        lambda bind=None: ["table 'users' is missing column(s): role_code"],
    )

    report = bootstrap.initialise_database()

    assert report["connected"] is True
    assert report["schema_problems"] == ["table 'users' is missing column(s): role_code"]
    assert report["seeded"] == {}
    assert report["reference_data"] == "skipped: schema mismatch"
    assert bootstrap.health_summary()["schema_problems"] == 1


def test_bootstrap_survives_a_seeding_failure(accounts, monkeypatch):
    """A failed seed leaves the API serving so /health can show the problem."""
    from sqlalchemy.exc import SQLAlchemyError

    monkeypatch.setattr(bootstrap, "_LAST_REPORT", dict(bootstrap.last_report()))
    monkeypatch.setattr(bootstrap, "schema_problems", lambda bind=None: [])
    monkeypatch.setattr(bootstrap, "reference_data_present", lambda db: False)

    def explode(db):
        raise SQLAlchemyError("value too long for type character varying(60)")

    monkeypatch.setattr(bootstrap, "seed_reference_data", explode)

    report = bootstrap.initialise_database()

    assert report["reference_data"] == "failed"
    assert "character varying(60)" in report["error"]


# ---------------------------------------------------------------------------
# Guided terminal input
#
# The scripts prompt only when the values were not passed as flags, and only on
# a real terminal, so the flags stay authoritative for scripted use. These tests
# drive the prompts directly instead of pretending to be a terminal.
# ---------------------------------------------------------------------------


def test_mask_url_hides_only_the_password():
    from scripts._bootstrap import mask_url

    assert mask_url("postgresql://postgres.abc:secret@host:5432/postgres") == (
        "postgresql://postgres.abc:***@host:5432/postgres"
    )
    # Nothing to hide, and no password to lose.
    assert mask_url("sqlite:///./metriq.db") == "sqlite:///./metriq.db"
    assert mask_url("") == ""


def test_prompt_keeps_a_default_on_an_empty_answer(monkeypatch):
    from scripts import _bootstrap

    monkeypatch.setattr("builtins.input", lambda _: "   ")
    assert _bootstrap.prompt("Schema", default="metriq") == "metriq"

    monkeypatch.setattr("builtins.input", lambda _: "  public  ")
    assert _bootstrap.prompt("Schema", default="metriq") == "public"

    # A blank answer is only acceptable where the caller allows it.
    monkeypatch.setattr("builtins.input", lambda _: "   ")
    assert _bootstrap.prompt("Laboratory code", allow_blank=True) == ""


def test_prompt_retries_until_a_required_value_is_typed(monkeypatch, capsys):
    from scripts import _bootstrap

    answers = iter(["", "", "engineer@lab.example"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    assert _bootstrap.prompt("Super Admin email") == "engineer@lab.example"
    assert "a value is required" in capsys.readouterr().out


def test_confirm_and_choice_accept_words_or_numbers(monkeypatch, capsys):
    from scripts import _bootstrap

    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert _bootstrap.confirm("Delete?") is True
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _bootstrap.confirm("Delete?") is False  # empty keeps the default
    assert _bootstrap.confirm("Delete?", default=True) is True

    monkeypatch.setattr("builtins.input", lambda _: "2")
    assert _bootstrap.prompt_choice("Choice", (("create", "a"), ("list", "b"))) == "list"
    monkeypatch.setattr("builtins.input", lambda _: "list")
    assert _bootstrap.prompt_choice("Choice", (("create", "a"), ("list", "b"))) == "list"
    assert "1." in capsys.readouterr().out


def test_manage_admin_guided_flow_builds_the_create_command(monkeypatch):
    from scripts import manage_admin

    monkeypatch.setattr(manage_admin, "prompt_choice", lambda label, choices, **kw: "create")
    answers = iter(["guided@lab.example", "Ops Lead", ""])
    monkeypatch.setattr(manage_admin, "prompt", lambda label, **kw: next(answers))
    monkeypatch.setattr(manage_admin, "prompt_secret", lambda label, **kw: "GuidedPass1!")

    argv = manage_admin.guided_argv()

    assert argv == [
        "create",
        "--email", "guided@lab.example",
        "--name", "Ops Lead",
        "--password", "GuidedPass1!",
    ]
    # The assembled argv must parse back into the command it describes.
    parsed = manage_admin.build_parser().parse_args(argv)
    assert parsed.command == "create"
    assert parsed.email == "guided@lab.example"
    assert parsed.password == "GuidedPass1!"


def test_manage_admin_guided_flow_can_be_declined(monkeypatch):
    from scripts import manage_admin

    monkeypatch.setattr(manage_admin, "prompt_choice", lambda label, choices, **kw: "delete")
    monkeypatch.setattr(manage_admin, "prompt", lambda label, **kw: "admin@lab.example")
    monkeypatch.setattr(manage_admin, "confirm", lambda label, **kw: False)

    assert manage_admin.guided_argv() == []


def test_manage_admin_asks_for_a_missing_value_instead_of_failing_early(monkeypatch):
    from scripts import manage_admin

    monkeypatch.setattr(manage_admin, "interactive", lambda: True)
    monkeypatch.setattr(manage_admin, "prompt", lambda label, **kw: "typed@lab.example")
    monkeypatch.setattr(manage_admin, "prompt_secret", lambda label, **kw: "")

    parser = manage_admin.build_parser()
    args = parser.parse_args(["set-password"])

    manage_admin.resolve_inputs(args, parser)

    assert args.email == "typed@lab.example"
    # A blank password keeps the generate-and-print-once behaviour.
    assert args.password is None


def test_manage_admin_still_errors_without_a_terminal(monkeypatch, capsys):
    from scripts import manage_admin

    monkeypatch.setattr(manage_admin, "interactive", lambda: False)
    parser = manage_admin.build_parser()
    args = parser.parse_args(["set-password"])

    with pytest.raises(SystemExit) as caught:
        manage_admin.resolve_inputs(args, parser)

    assert caught.value.code == 2
    assert "--email" in capsys.readouterr().err


def test_manage_admin_flags_are_never_asked_for_twice(monkeypatch):
    from scripts import manage_admin

    def refuse(label, **kw):  # pragma: no cover - must not be reached
        raise AssertionError(f"prompted for {label!r} despite the flag being set")

    monkeypatch.setattr(manage_admin, "interactive", lambda: True)
    monkeypatch.setattr(manage_admin, "prompt", refuse)
    monkeypatch.setattr(manage_admin, "prompt_secret", refuse)

    parser = manage_admin.build_parser()
    args = parser.parse_args(
        ["create", "--email", "given@lab.example", "--password", "GivenPass1!"]
    )
    manage_admin.resolve_inputs(args, parser)

    assert args.email == "given@lab.example"
    assert args.password == "GivenPass1!"


def test_reinit_db_guided_flow_requires_the_confirmation_word(monkeypatch):
    from scripts import reinit_db

    parser = reinit_db.build_parser()
    args = parser.parse_args([])
    monkeypatch.setattr(reinit_db, "prompt", lambda label, **kw: "reinitialise")

    assert reinit_db.collect_inputs(args) is False
    assert args.yes is False, "a declined run must not look confirmed"


def test_reinit_db_guided_flow_collects_the_target_and_the_account(monkeypatch):
    from scripts import reinit_db

    answers = iter(
        [
            "postgresql://postgres.ref:pw@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres",
            "metriq",
            "REINITIALISE",
            "ops@lab.example",
            "Ops Lead",
        ]
    )
    monkeypatch.setattr(reinit_db, "prompt", lambda label, **kw: next(answers))
    monkeypatch.setattr(reinit_db, "prompt_secret", lambda label, **kw: "GuidedPass1!")
    monkeypatch.setattr(reinit_db, "confirm", lambda label, **kw: True)

    args = reinit_db.build_parser().parse_args([])
    assert reinit_db.collect_inputs(args) is True

    assert args.url.endswith("/postgres")
    assert args.schema == "metriq"
    assert args.yes is True
    assert args.admin_email == "ops@lab.example"
    assert args.admin_name == "Ops Lead"
    assert args.admin_password == "GuidedPass1!"
    assert args.no_admin is False


def test_reinit_db_guided_flow_can_skip_the_admin(monkeypatch):
    from scripts import reinit_db

    answers = iter(["", "metriq", "REINITIALISE"])
    monkeypatch.setattr(reinit_db, "prompt", lambda label, **kw: next(answers))
    monkeypatch.setattr(reinit_db, "confirm", lambda label, **kw: False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres.ref:pw@host:5432/postgres")

    args = reinit_db.build_parser().parse_args([])
    assert reinit_db.collect_inputs(args) is True
    assert args.no_admin is True


def test_schema_problems_can_ignore_tables_that_do_not_exist_yet(tmp_path):
    """A never-initialised database must not look like a shared one."""
    from sqlalchemy import create_engine

    fresh = create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")

    assert any(p.startswith("missing table") for p in bootstrap.schema_problems(fresh))
    assert bootstrap.schema_problems(fresh, include_missing=False) == []


def test_schema_conflicts_name_a_foreign_table_that_would_be_taken_over(tmp_path):
    from sqlalchemy import create_engine, text

    shared = create_engine(f"sqlite:///{(tmp_path / 'shared.db').as_posix()}")
    with shared.begin() as connection:
        connection.execute(text("create table users (id integer primary key, email text)"))

    conflicts = bootstrap.schema_problems(shared, include_missing=False)

    assert len(conflicts) == 1
    assert conflicts[0].startswith("table 'users' is missing column(s):")
    assert "role_code" in conflicts[0]
