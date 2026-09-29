"""Release-hardening checks from the project audit: items 2, 3, 5 and 12.

Each test names the item it belongs to, so a failure points at the finding it
reopens rather than at an anonymous assertion.

This file quotes credential-shaped strings on purpose, to prove the gate catches
them; `check_release.SELF_REFERENTIAL` is the list of files the gate skips for
that reason.
"""

from __future__ import annotations

import io
import pathlib
import zipfile

import pytest

from scripts import check_release
from scripts.check_release import REPO_ROOT, scan_archive, scan_git_history, scan_text, scan_tree


# --------------------------------------------------------- item 2: secrets ---


def test_the_shipped_tree_carries_no_secret():
    """The gate CI runs: no .env, no key material, no credential-like string."""
    assert scan_tree(REPO_ROOT) == []


def test_an_ignored_file_is_skipped_unless_the_build_context_is_being_checked(tmp_path, monkeypatch):
    """A gitignored .env cannot be committed, but it can still reach an image."""
    monkeypatch.setattr(check_release, "ignored_paths", lambda root: ({"backend/.env"}, set()))
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env").write_text(
        "DATABASE_URL=postgresql://u:fixture-not-a-secret-01@h:5432/d\n", encoding="utf-8"
    )

    assert scan_tree(tmp_path) == []
    assert scan_tree(tmp_path, include_ignored=True)


def test_a_release_archive_is_checked_member_by_member(tmp_path):
    clean = tmp_path / "clean.zip"
    with zipfile.ZipFile(clean, "w") as bundle:
        bundle.writestr("backend/app/main.py", "print('hello')\n")
    assert scan_archive(clean) == []

    dirty = tmp_path / "dirty.zip"
    with zipfile.ZipFile(dirty, "w") as bundle:
        bundle.writestr("backend/.env", "SUPABASE_SERVICE_ROLE_KEY=whatever\n")
        bundle.writestr("keys/server.pem", "-----BEGIN RSA PRIVATE KEY-----\n")
    messages = {finding.message for finding in scan_archive(dirty)}
    assert any(".env" in message for message in messages)
    assert any(".pem" in message for message in messages)


def test_a_database_url_with_a_real_looking_password_is_caught():
    line = 'DATABASE_URL = "postgresql://postgres.abc:fixture-not-a-secret-01@aws-0.pooler.supabase.com:5432/postgres"'
    assert scan_text("backend/settings.py", line)


@pytest.mark.parametrize(
    "line",
    [
        'DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres',
        'DATABASE_URL=postgresql://postgres.ref:pw@host:5432/postgres',
        'JWT_SECRET=change-me-in-production',
    ],
)
def test_placeholders_are_not_mistaken_for_secrets(line):
    assert scan_text("docs/deployment/README.md", line) == []


def test_the_docker_build_context_cannot_admit_a_secret():
    """`COPY backend backend` would otherwise bake in a developer's .env."""
    text = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    for pattern in (".env", "**/.env", "*.pem", "*.key", "*.db"):
        assert pattern in text, f".dockerignore must exclude {pattern}"
    assert "!backend/deployment.env" in text, "the committed non-secret defaults still have to ship"


def test_git_history_carries_no_credential():
    """A secret removed from the tip is still in the objects until history is rewritten."""
    assert scan_git_history() == []


# ------------------------------------------- copy: formal, no implementation ---


def test_sign_in_copy_does_not_explain_the_identity_provider():
    """Sign-in is described in the product's own terms.

    Which service verifies a password, and how a session is minted afterwards,
    is an implementation detail. Printed on the sign-in page it reads as a note
    to the developer rather than to the person signing in - and it names a
    vendor the product's copy does not otherwise mention.
    """
    for asset, phrase in (
        ("frontend/login.html", "verified by"),
        ("frontend/login.html", "provider-note"),
        ("frontend/js/login.js", "Passwords are verified by"),
        ("frontend/js/login.js", "issues its own short-lived session"),
        ("frontend/js/api.js", "Supabase sign-in service"),
        ("frontend/js/api.js", "Supabase service"),
        ("frontend/js/api.js", "Supabase message"),
    ):
        text = (REPO_ROOT / asset).read_text(encoding="utf-8")
        assert phrase not in text, f"{asset} still carries {phrase!r}"


def test_a_refused_token_is_reported_without_a_deployment_diagnostic():
    """The reply names the outcome; the reason belongs in the service log."""
    from app.routers import auth

    source = (REPO_ROOT / "backend" / "app" / "routers" / "auth.py").read_text(encoding="utf-8")
    assert "The sign-in token could not be verified." in source
    assert "logger.warning(" in source, "the rejected reason has to be logged somewhere"
    assert auth.logger.name == "metriq.auth"


# ------------------------------------------------- item 3: demo credentials ---


def test_no_browser_asset_carries_the_demonstration_password(tmp_path):
    from app.config import settings

    asset = "frontend/js/login.js"
    assert scan_text(asset, f"password.value = '{settings.DEMO_PASSWORD}';", demo=settings.DEMO_PASSWORD)
    assert scan_text(asset, "payload = await api.get('/auth/demo-accounts');", demo=settings.DEMO_PASSWORD) == []


def test_the_frontend_sources_never_mention_the_demo_password():
    from app.config import settings

    frontend = REPO_ROOT / "frontend"
    for path in frontend.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".png", ".svg", ".ico", ".woff2"}:
            continue
        text = io.open(path, encoding="utf-8", newline="").read()
        assert settings.DEMO_PASSWORD not in text, path


def test_the_demo_panel_is_served_only_while_demo_mode_is_on(client, monkeypatch):
    """A deployment that has not opted in exposes no demonstration credential."""
    from app.config import Settings, settings

    monkeypatch.setattr(settings, "DEMO_MODE", False)
    assert client.get("/api/v1/auth/demo-accounts").status_code == 404

    monkeypatch.setattr(settings, "DEMO_MODE", True)
    body = client.get("/api/v1/auth/demo-accounts").json()

    assert body["demo_mode"] is True
    assert body["password"] == Settings().DEMO_PASSWORD
    emails = {account["email"] for account in body["accounts"]}
    assert "engineer@metriq.local" in emails
    assert all(account["role_name"] for account in body["accounts"]), "the panel labels each role"


# -------------------------------------------------- item 5: signature claims ---


def test_a_signed_report_claim_is_rejected():
    for line in (
        "<h1>from observation to signed report</h1>",
        "It produces a digitally signed document.",
        "Download the signed PDF.",
    ):
        assert scan_text("frontend/index.html", line), line
        assert scan_text("README.md", line), line


def test_the_approved_wording_is_accepted():
    for line in (
        "from observation to approved, hash-verifiable report",
        "an approved and hash-verifiable PDF/DOCX report",
        "Time-limited signed download URLs for evidence files.",
    ):
        assert scan_text("README.md", line) == [], line


# --------------------------------------------- item 12: CORS and boundaries ---


def test_a_wildcard_origin_pattern_is_ignored_and_reported():
    """A stale host variable must not be able to stop the service booting.

    The value used to raise, which is why a leftover
    `CORS_ALLOW_ORIGIN_REGEX` on Render produced a crash loop whose own cause
    `/health` could never report. It is now ignored, and said so.
    """
    from app.config import Settings

    wildcard = Settings(CORS_ALLOW_ORIGIN_REGEX=r"https://.*\.vercel\.app")
    assert wildcard.cors_allow_origin_regex is None
    assert [item.split(":")[0] for item in wildcard.ignored_settings] == [
        "CORS_ALLOW_ORIGIN_REGEX"
    ]

    assert Settings(CORS_ALLOW_ORIGIN_REGEX=None).cors_allow_origin_regex is None
    assert Settings(CORS_ALLOW_ORIGIN_REGEX="").ignored_settings == []
    assert Settings(CORS_ALLOW_ORIGIN_REGEX=None).ignored_settings == []


def test_an_exact_origin_pattern_still_passes_through():
    from app.config import Settings

    exact = Settings(CORS_ALLOW_ORIGIN_REGEX=r"https://metriq(-[a-z0-9]+)?\.vercel\.app")
    assert exact.cors_allow_origin_regex == r"https://metriq(-[a-z0-9]+)?\.vercel\.app"
    assert exact.ignored_settings == []


def test_production_drops_the_development_origins():
    from app.config import Settings

    localhost = ["http://localhost:5173", "http://127.0.0.1:5500", "https://metriq.vercel.app"]
    assert Settings(ENVIRONMENT="production", CORS_ORIGINS=localhost).cors_origins == [
        "https://metriq.vercel.app"
    ]
    assert Settings(ENVIRONMENT="development", CORS_ORIGINS=localhost).cors_origins == localhost


def test_an_unapproved_origin_gets_no_cors_approval(client):
    approved = client.options(
        "/api/v1/me",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert approved.headers.get("access-control-allow-origin") == "http://localhost:5173"

    rejected = client.options(
        "/api/v1/me",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in rejected.headers