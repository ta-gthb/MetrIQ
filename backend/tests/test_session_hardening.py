"""Session and token handling (project-audit item 13).

Short-lived access tokens, single-use refresh tokens with reuse detection,
sign-out that revokes server-side, and the security headers the browser needs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings

API = "/api/v1"
REPO_ROOT = Path(__file__).resolve().parents[2]


def fresh_client():
    """A client with no cookies, so a body token is used instead of the cookie."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    client.cookies.clear()
    return client


def login(client, email, password):
    response = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response


# ------------------------------------------------------- short-lived tokens ---


def test_the_access_token_is_short_lived():
    """A stolen access token should be worth an hour, not a working day."""
    assert Settings().ACCESS_TOKEN_TTL_MINUTES <= 60
    assert Settings().REFRESH_TOKEN_TTL_DAYS <= 30


def test_login_reports_the_shortened_lifetime(client, accounts):
    response = login(client, accounts["emails"]["ENGINEER"], accounts["password"])
    assert response.json()["expires_in"] == Settings().ACCESS_TOKEN_TTL_MINUTES * 60


# ------------------------------------------------------- cookie / rotation ---


def test_the_refresh_token_is_set_as_a_hardened_cookie(client, accounts):
    response = login(client, accounts["emails"]["ENGINEER"], accounts["password"])
    header = response.headers["set-cookie"]

    assert Settings().REFRESH_COOKIE_NAME in header
    assert "HttpOnly" in header, "page JavaScript must not be able to read it"
    assert "SameSite=lax" in header
    assert "Path=/" in header


def test_a_refresh_token_can_only_be_used_once(client, accounts):
    """Rotation: each use mints a successor, and the old token dies."""
    session = login(client, accounts["emails"]["ENGINEER"], accounts["password"]).json()

    rotated = fresh_client()
    first = rotated.post(f"{API}/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert first.status_code == 200, first.text
    assert first.json()["refresh_token"] != session["refresh_token"]

    again = fresh_client()
    replay = again.post(f"{API}/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert replay.status_code == 401
    assert "already been used" in replay.json()["detail"]


def test_reusing_a_token_revokes_the_whole_family(client, accounts):
    """Theft detection signs out the thief *and* the legitimate holder."""
    session = login(client, accounts["emails"]["ENGINEER"], accounts["password"]).json()

    rotate = fresh_client()
    successor = rotate.post(
        f"{API}/auth/refresh", json={"refresh_token": session["refresh_token"]}
    ).json()["refresh_token"]

    replay = fresh_client()
    assert replay.post(
        f"{API}/auth/refresh", json={"refresh_token": session["refresh_token"]}
    ).status_code == 401

    after = fresh_client()
    response = after.post(f"{API}/auth/refresh", json={"refresh_token": successor})
    assert response.status_code == 401
    assert "revoked" in response.json()["detail"]


def test_reuse_is_recorded_in_the_audit_trail(client, accounts):
    """A revoked family is a security event, so it belongs in the trail."""
    session = login(client, accounts["emails"]["ENGINEER"], accounts["password"]).json()
    rotate = fresh_client()
    rotate.post(f"{API}/auth/refresh", json={"refresh_token": session["refresh_token"]})
    replay = fresh_client()
    replay.post(f"{API}/auth/refresh", json={"refresh_token": session["refresh_token"]})

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import AuditLog

    with SessionLocal() as db:
        rows = db.execute(
            select(AuditLog).where(AuditLog.event_type == "TOKEN_REUSE")
        ).scalars().all()
    assert rows, "the revoked session must be visible to an auditor"


def test_the_refresh_flow_works_through_the_cookie_alone(client, accounts):
    """A browser presents no body at all: everything travels in the cookie."""
    login(client, accounts["emails"]["ENGINEER"], accounts["password"])
    before = client.cookies[Settings().REFRESH_COOKIE_NAME]

    response = client.post(f"{API}/auth/refresh")

    assert response.status_code == 200, response.text
    assert client.cookies[Settings().REFRESH_COOKIE_NAME] != before, "the cookie rotates too"


def test_signing_out_revokes_the_session_server_side(client, accounts):
    session = login(client, accounts["emails"]["ENGINEER"], accounts["password"]).json()

    assert client.post(f"{API}/auth/logout").json()["revoked"] is True

    after = fresh_client()
    response = after.post(f"{API}/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert response.status_code == 401, "a captured token must stop working on sign-out"


def test_logout_is_recorded_in_the_audit_trail(client, accounts):
    login(client, accounts["emails"]["ENGINEER"], accounts["password"])
    client.post(f"{API}/auth/logout")

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import AuditLog

    with SessionLocal() as db:
        rows = db.execute(select(AuditLog).where(AuditLog.event_type == "LOGOUT")).scalars().all()
    assert rows


def test_an_access_token_is_still_rejected_as_a_refresh_token(client, accounts):
    session = login(client, accounts["emails"]["ENGINEER"], accounts["password"]).json()

    response = fresh_client().post(
        f"{API}/auth/refresh", json={"refresh_token": session["access_token"]}
    )
    assert response.status_code == 401


def test_no_refresh_token_is_a_401_not_a_500(client):
    assert fresh_client().post(f"{API}/auth/refresh").status_code == 401


# ---------------------------------------------------------- security headers ---


def test_security_headers_are_present_on_api_responses(client):
    headers = client.get("/health").headers

    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert "camera=()" in headers["permissions-policy"]
    assert "default-src 'self'" in headers["content-security-policy"]


def test_the_policy_forbids_inline_scripts():
    """Every page's inline bootstrap was moved to js/theme-boot.js for this."""
    policy = Settings().CSP_POLICY
    script_src = next(part for part in policy.split(";") if "script-src" in part)
    assert "'unsafe-inline'" not in script_src
    assert "'self'" in script_src

    for page in (REPO_ROOT / "frontend").glob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert "<script>" not in text, f"{page.name} still carries an inline script"
        assert "<script>\n" not in text
        # Inline event handlers would need the same exception.
        for attribute in ("onclick=", "onchange=", "onsubmit=", "onload="):
            assert attribute not in text, f"{page.name} uses {attribute}"


def test_the_static_host_declares_the_same_headers():
    """Vercel serves the frontend, so the API's headers do not cover it."""
    config = json.loads((REPO_ROOT / "vercel.json").read_text(encoding="utf-8"))
    declared = {
        header["key"]: header["value"]
        for rule in config["headers"]
        if rule["source"] == "/(.*)"
        for header in rule["headers"]
    }
    assert "Content-Security-Policy" in declared
    assert "script-src 'self'" in declared["Content-Security-Policy"]
    assert declared["X-Content-Type-Options"] == "nosniff"
    assert declared["X-Frame-Options"] == "DENY"
    assert "max-age=" in declared["Strict-Transport-Security"]


def test_the_browser_never_stores_the_refresh_token():
    """It belongs in the HttpOnly cookie, not in localStorage."""
    source = (REPO_ROOT / "frontend" / "js" / "api.js").read_text(encoding="utf-8")
    assert "withoutRefreshToken" in source, "the login response must be stripped before it is stored"
    assert "credentials: 'include'" in source, "the cookie has to travel with each call"
    assert "refreshInFlight" in source, "concurrent 401s must rotate once, not six times"