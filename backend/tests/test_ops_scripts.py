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

    first = subprocess.run(command, cwd=BACKEND_DIR, env=env, capture_output=True, text=True)
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
    second = subprocess.run(command, cwd=BACKEND_DIR, env=env, capture_output=True, text=True)
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
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "--yes" in result.stdout
