"""The user IDs the platform issues, and the one sign-in that uses them.

Every account signs in the same way - the identifier the platform gave it and
its password - and the role is read from the account that identifier names,
never chosen by the caller. These tests hold the identifier to the format the
specification fixes, one prefix per role, and check that a Super Admin is
issued only through the operator script.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import User
from app.security.permissions import (
    APPROVER,
    AUDITOR,
    ENGINEER,
    LAB_ADMIN,
    REVIEWER,
    ROLE_DEFINITIONS,
    SUPER_ADMIN,
)
from app.services.identity import user_codes
from app.services.identity.user_codes import CODE_PATTERN

API = "/api/v1"
BACKEND_DIR = Path(__file__).resolve().parents[1]

#: The format the specification fixes, role by role.
EXPECTED_PREFIXES = {
    SUPER_ADMIN: "stmadm",
    LAB_ADMIN: "labadm",
    ENGINEER: "temadm",
    REVIEWER: "trvadm",
    APPROVER: "apradm",
    AUDITOR: "audadm",
}


@pytest.fixture
def db(accounts):
    """A session on the suite database, which `accounts` has already seeded."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def taken(db, code: str) -> bool:
    return db.execute(select(User.id).where(User.user_code == code)).scalars().first() is not None


# ------------------------------------------------------------- the format


def test_every_role_has_a_format_and_no_other_role_does():
    assert user_codes.PREFIXES == EXPECTED_PREFIXES
    assert set(user_codes.PREFIXES) == set(ROLE_DEFINITIONS), (
        "a role without a prefix cannot be issued an identifier"
    )


def test_an_identifier_is_the_prefix_the_year_and_three_digits():
    for role, prefix in EXPECTED_PREFIXES.items():
        issued = user_codes.compose(role, 7)

        assert re.fullmatch(rf"{prefix}[0-9]{{4}}[0-9]{{3}}", issued), issued
        assert issued.startswith(f"{prefix}{user_codes.current_year()}")
        assert issued.endswith("007")


def test_the_year_the_identifier_carries_is_given_in_full():
    assert user_codes.compose(ENGINEER, 1, year=2031) == "temadm2031001"


def test_a_role_the_platform_has_no_format_for_is_refused():
    with pytest.raises(user_codes.UnknownRole):
        user_codes.compose("STUDENT", 1)
    with pytest.raises(user_codes.UnknownRole):
        user_codes.prefix_for(None)


def test_the_pattern_accepts_only_identifiers_this_platform_issues():
    assert user_codes.is_user_code("temadm2026042")
    assert user_codes.is_user_code("  STMADM2026001  "), "case and spacing are tidied up"
    assert not user_codes.is_user_code("zzzzzz2026001"), "an unknown prefix is not an identifier"
    assert not user_codes.is_user_code("temadm20260"), "the serial is three digits"
    assert not user_codes.is_user_code(None)
    assert not user_codes.is_user_code("engineer@metriq.local")


def test_an_identifier_in_use_is_drawn_again(db, accounts, monkeypatch):
    held = accounts["codes"][ENGINEER]
    year = int(held[6:10])
    held_serial = int(held[9:])
    free = next(
        serial
        for serial in range(user_codes.SERIAL_LIMIT)
        if not taken(db, f"temadm{year}{serial:03d}")
    )
    draws = iter([held_serial, free])
    monkeypatch.setattr(user_codes, "_draw", lambda: next(draws))

    issued = user_codes.generate_user_code(db, ENGINEER, year=year)

    assert issued == f"temadm{year}{free:03d}"


def test_an_identifier_in_use_is_never_handed_out(
    db, accounts, monkeypatch
):
    """Two accounts holding one identifier would make the sign-in name
    ambiguous, so the draw is refused rather than repeated."""
    held = accounts["codes"][ENGINEER]
    monkeypatch.setattr(user_codes, "_draw", lambda: int(held[9:]))

    with pytest.raises(RuntimeError, match="no free temadm identifier"):
        user_codes.generate_user_code(db, ENGINEER, year=int(held[6:10]))


def test_an_account_keeps_the_identifier_it_already_holds(db, accounts):
    user = db.execute(
        select(User).where(User.email == accounts["emails"][REVIEWER])
    ).scalars().first()

    assert user_codes.ensure_user_code(db, user) == accounts["codes"][REVIEWER]


def test_an_account_that_predates_the_identifiers_is_given_one(db, accounts):
    """So an account created before the platform issued them is still usable."""
    user = User(
        email=f"legacy-{uuid.uuid4().hex[:10]}@lab.example",
        full_name="Legacy Account",
        role_code=ENGINEER,
        laboratory_id=uuid.UUID(accounts["laboratory_id"]),
        is_active=True,
        is_email_verified=True,
        auth_provider="local",
        password_hash="not-a-real-hash",
    )
    assert user.user_code is None
    db.add(user)
    try:
        issued = user_codes.ensure_user_code(db, user)
        db.commit()

        assert re.fullmatch(r"temadm[0-9]{4}[0-9]{3}", issued)
        assert db.get(User, user.id).user_code == issued
    finally:
        db.rollback()
        stored = db.get(User, user.id)
        if stored is not None:
            db.delete(stored)
            db.commit()


# ------------------------------------------------------- one sign-in


@pytest.mark.parametrize("role", sorted(EXPECTED_PREFIXES))
def test_the_user_id_signs_in_and_the_role_comes_from_the_account(
    client, accounts, role
):
    response = client.post(
        f"{API}/auth/login",
        json={"user_id": accounts["codes"][role], "password": accounts["password"]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["role_code"] == role, "the caller never names the role"
    assert body["user"]["user_code"] == accounts["codes"][role]
    assert body["access_token"]


def test_the_address_on_the_account_still_signs_in(client, accounts):
    response = client.post(
        f"{API}/auth/login",
        json={"email": accounts["emails"][APPROVER], "password": accounts["password"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["user"]["role_code"] == APPROVER


def test_an_unknown_user_id_is_refused_without_saying_which_half_was_wrong(client, accounts):
    response = client.post(
        f"{API}/auth/login",
        json={"user_id": "temadm2000001", "password": accounts["password"]},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect user ID or password."


def test_a_wrong_password_on_a_real_user_id_is_refused_the_same_way(client, accounts):
    response = client.post(
        f"{API}/auth/login",
        json={"user_id": accounts["codes"][LAB_ADMIN], "password": "not-the-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect user ID or password."


def test_every_seeded_account_holds_an_identifier(accounts):
    assert set(accounts["codes"]) == set(EXPECTED_PREFIXES)
    for role, code in accounts["codes"].items():
        assert user_codes.is_user_code(code), code
        assert code.startswith(EXPECTED_PREFIXES[role])


def test_a_demonstration_deployment_lists_each_account_by_its_identifier(
    client, accounts, monkeypatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "DEMO_MODE", True)

    body = client.get(f"{API}/auth/demo-accounts").json()

    listed = {account["role_code"]: account["user_id"] for account in body["accounts"]}
    for role, code in accounts["codes"].items():
        assert listed[role] == code


# ------------------------------------------------- how the IDs are issued


def test_the_operator_script_issues_a_super_admin_identifier(db):
    from scripts.manage_admin import upsert_super_admin

    email = f"codes-{uuid.uuid4().hex[:10]}@lab.example"
    try:
        result = upsert_super_admin(
            db, email=email, full_name="Identifier Fixture", password="FixturePass1!"
        )
        db.commit()

        assert result.created is True
        assert result.user_id == result.user.user_code
        assert re.fullmatch(
            rf"{EXPECTED_PREFIXES[SUPER_ADMIN]}{user_codes.current_year()}[0-9]{{3}}",
            result.user_id,
        )
    finally:
        db.rollback()
        stored = db.execute(select(User).where(User.email == email)).scalars().first()
        if stored is not None:
            db.delete(stored)
            db.commit()


def test_an_administrator_creating_an_account_has_the_id_issued_for_it(
    client, accounts, tokens, db
):
    for role in (LAB_ADMIN, ENGINEER, REVIEWER, APPROVER, AUDITOR):
        email = f"issued-{role.lower()}-{uuid.uuid4().hex[:8]}@lab.example"
        response = client.post(
            f"{API}/users",
            headers=tokens[SUPER_ADMIN],
            json={
                "email": email,
                "full_name": f"Issued {role}",
                "password": "FixturePass1!",
                "role_code": role,
                "designation": "Officer",
                "laboratory_id": accounts["laboratory_id"],
            },
        )
        try:
            assert response.status_code == 201, response.text
            issued = response.json()["user_code"]
            assert re.fullmatch(
                rf"{EXPECTED_PREFIXES[role]}{user_codes.current_year()}[0-9]{{3}}", issued
            ), issued
        finally:
            stored = db.execute(select(User).where(User.email == email)).scalars().first()
            if stored is not None:
                db.delete(stored)
                db.commit()


def test_the_migration_tree_froze_the_same_prefixes():
    """The backfill in revision 0008 names the prefixes itself, so a change to
    the module must be accompanied by a change there."""
    path = next(
        (BACKEND_DIR / "alembic" / "versions").glob("0008_*.py")
    )
    spec = importlib.util.spec_from_file_location("revision_0008", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert dict(module.PREFIXES) == user_codes.PREFIXES


def test_the_pattern_and_the_composer_agree():
    for role in EXPECTED_PREFIXES:
        issued = user_codes.compose(role, 123)
        match = CODE_PATTERN.match(issued)
        assert match is not None
        assert match.group("prefix") == EXPECTED_PREFIXES[role]
        assert int(match.group("serial")) == 123
