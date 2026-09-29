"""Supabase Auth as the production identity provider (project-audit item 4).

The audit's definition of done is the shape of this file:

* production sign-in uses Supabase Auth, and the local password path is refused
  there rather than merely hidden from the form;
* FastAPI rejects forged, expired, mis-issued and unsigned tokens;
* a role cannot be elevated from the frontend.

The last one is the interesting one. Every token below is signed with the
project's secret, so the signature is genuinely valid - and the tests still
require that the session it produces is exactly the MetrIQ user that identity is
linked to, permissions included.

The JWKS (asymmetric-key) route is not exercised here: it needs a real project
serving a real key set. The algorithm allow-list and the issuer check that guard
it are exercised through the HS256 path.
"""

from __future__ import annotations

import logging
import time
import uuid

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import _application_csp
from app.security import tokens
from app.security.permissions import ENGINEER, SUPER_ADMIN, role_permissions

API = "/api/v1"

SUPABASE_URL = "https://test-project.supabase.co"
SUPABASE_ANON_KEY = "anon-key-for-tests-only"
SUPABASE_SECRET = "supabase-jwt-secret-for-tests"
SUPABASE_ISSUER = SUPABASE_URL + "/auth/v1"

#: What every refused token gets back, whatever the internal reason was. The
#: caller is told the outcome; the reason is an operator diagnostic and stays in
#: the service log, so no deployment detail ever reaches a message a person may
#: read (the interface is formal and self-contained).
TOKEN_REFUSED = "The sign-in token could not be verified."

#: Every permission the engineer's role does not carry. None of these may appear
#: in a session created from a token that claims to be a super admin.
ADMIN_ONLY = sorted(role_permissions(SUPER_ADMIN) - role_permissions(ENGINEER))
assert ADMIN_ONLY, "the fixture is meaningless if both roles carry the same permissions"


def supabase_token(**overrides) -> str:
    """An HS256 token that this project would genuinely have issued."""
    now = int(time.time())
    payload = {
        "sub": str(uuid.uuid4()),
        "email": "someone@example.test",
        "role": "authenticated",
        "aud": "authenticated",
        "iss": SUPABASE_ISSUER,
        "iat": now,
        "exp": now + 3600,
        "session_id": str(uuid.uuid4()),
    }
    payload.update(overrides)
    return pyjwt.encode(payload, SUPABASE_SECRET, algorithm="HS256")


@pytest.fixture
def supabase(monkeypatch):
    """A deployment configured for Supabase Auth."""
    monkeypatch.setattr(settings, "AUTH_PROVIDER", "hybrid")
    monkeypatch.setattr(settings, "SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", SUPABASE_ANON_KEY)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", SUPABASE_SECRET)
    monkeypatch.setattr(settings, "SUPABASE_JWKS_URL", None)
    # The JWKS client is cached on the module; a leftover from another test would
    # be used for the next one.
    monkeypatch.setattr(tokens, "_jwks_client", None)
    return settings


def exchange(client: TestClient, token: str):
    return client.post(f"{API}/auth/session", headers={"Authorization": f"Bearer {token}"})


# ---------------------------------------------------------------- the happy path


def test_a_supabase_identity_is_exchanged_for_a_metriq_session(client, accounts, supabase):
    email = accounts["emails"][ENGINEER]
    subject = str(uuid.uuid4())

    response = exchange(client, supabase_token(sub=subject, email=email))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert body["user"]["email"] == email
    assert body["user"]["role_code"] == ENGINEER
    assert body["user"]["supabase_user_id"] == subject, "the link is recorded on first sign-in"
    assert body["user"]["auth_provider"] == "supabase"


def test_the_session_is_a_metriq_one_that_works_against_the_api(client, accounts, supabase):
    """The Supabase token is exchanged, not passed through: every later call
    carries a MetrIQ token, so the provider's lifetime never becomes ours."""
    email = accounts["emails"][ENGINEER]
    session = exchange(client, supabase_token(email=email)).json()

    me = client.get(
        f"{API}/me", headers={"Authorization": f"Bearer {session['access_token']}"}
    )

    assert me.status_code == 200, me.text
    assert me.json()["user"]["email"] == email
    assert me.json()["permissions"] == sorted(role_permissions(ENGINEER))


def test_the_exchange_records_the_sign_in_method(client, accounts, supabase):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import AuditLog

    email = accounts["emails"][ENGINEER]
    assert exchange(client, supabase_token(email=email)).status_code == 200

    db = SessionLocal()
    try:
        rows = db.execute(
            select(AuditLog).where(
                AuditLog.actor_id == uuid.UUID(accounts["user_ids"][ENGINEER]),
                AuditLog.event_type == "LOGIN",
            )
        ).scalars().all()
    finally:
        db.close()

    methods = {(row.extra or {}).get("method") for row in rows}
    # "was this account signed into through the identity provider?" is answerable
    # from the trail alone (audit items 4 and 10).
    assert "supabase" in methods, methods


# ------------------------------------------------------- forged and unusable tokens


def test_a_token_signed_with_the_wrong_key_is_refused(client, accounts, supabase):
    """The classic forgery: a well-formed token, signed with something else."""
    forged = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "email": accounts["emails"][ENGINEER],
            "role": "authenticated",
            "aud": "authenticated",
            "iss": SUPABASE_ISSUER,
            "exp": int(time.time()) + 3600,
        },
        "not-the-project-secret",
        algorithm="HS256",
    )

    response = exchange(client, forged)

    assert response.status_code == 401


def test_a_token_signed_with_metriqs_own_key_is_refused(client, accounts, supabase):
    """Guard against the fallback that used to exist: JWT_SECRET signs *our*
    tokens, so accepting it here would let a MetrIQ session masquerade as a
    Supabase identity."""
    forged = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "email": accounts["emails"][ENGINEER],
            "role": "authenticated",
            "aud": "authenticated",
            "iss": SUPABASE_ISSUER,
            "exp": int(time.time()) + 3600,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )

    assert exchange(client, forged).status_code == 401


def test_an_unsigned_token_is_refused(client, accounts, supabase, caplog):
    """``alg: none`` takes the algorithm from the token itself, so without an
    allow-list a token that was never signed is simply trusted."""
    unsigned = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "email": accounts["emails"][ENGINEER], "role": "authenticated"},
        key=None,
        algorithm="none",
    )

    with caplog.at_level(logging.WARNING, logger="metriq.auth"):
        response = exchange(client, unsigned)

    assert response.status_code == 401
    assert response.json()["detail"] == TOKEN_REFUSED
    assert "algorithm" in caplog.text


def test_an_expired_token_is_refused(client, accounts, supabase):
    expired = supabase_token(
        email=accounts["emails"][ENGINEER], exp=int(time.time()) - 60
    )

    assert exchange(client, expired).status_code == 401


def test_a_token_issued_by_another_project_is_refused(client, accounts, supabase):
    """Same signing secret, different project: a shared or rotated key must not
    let one project's token open another's API."""
    other = supabase_token(
        email=accounts["emails"][ENGINEER], iss="https://someone-else.supabase.co/auth/v1"
    )

    assert exchange(client, other).status_code == 401


def test_a_token_for_another_audience_is_refused(client, accounts, supabase, caplog):
    with caplog.at_level(logging.WARNING, logger="metriq.auth"):
        response = exchange(client, supabase_token(email=accounts["emails"][ENGINEER], aud="other"))

    assert response.status_code == 401
    assert response.json()["detail"] == TOKEN_REFUSED
    # Both mismatches are a one-line configuration fix, so the log the operator
    # reads says which value this deployment wanted rather than "invalid token".
    assert "audience mismatch" in caplog.text
    assert settings.JWT_AUDIENCE in caplog.text


def test_an_issuer_mismatch_names_the_expected_issuer(client, accounts, supabase, caplog):
    with caplog.at_level(logging.WARNING, logger="metriq.auth"):
        response = exchange(
            client,
            supabase_token(
                email=accounts["emails"][ENGINEER], iss="https://elsewhere.supabase.co/auth/v1"
            ),
        )

    assert response.status_code == 401
    assert response.json()["detail"] == TOKEN_REFUSED
    assert "issuer mismatch" in caplog.text
    assert SUPABASE_ISSUER in caplog.text


def test_the_expected_issuer_is_derived_from_the_project_url(supabase):
    assert settings.supabase_issuer == SUPABASE_ISSUER


def test_the_issuer_can_be_overridden_for_a_custom_domain(supabase, monkeypatch):
    """A project on a custom domain issues a different `iss`; without an
    override every one of its otherwise-valid tokens would be refused."""
    monkeypatch.setattr(settings, "SUPABASE_JWT_ISSUER", "https://auth.lab.example/")

    assert settings.supabase_issuer == "https://auth.lab.example"



def test_the_anon_key_is_not_a_user_session(client, accounts, supabase):
    """The publishable anon key *is* a valid HS256 JWT for this project - it
    carries role 'anon' and no subject - and it is handed to every browser."""
    anon = pyjwt.encode(
        {"iss": "supabase", "ref": "test-project", "role": "anon", "exp": int(time.time()) + 3600},
        SUPABASE_SECRET,
        algorithm="HS256",
    )

    response = exchange(client, anon)

    assert response.status_code == 401


def test_the_service_role_key_is_not_a_user_session(client, accounts, supabase, caplog):
    """It bypasses row-level security, so a leaked one must not become a session
    even though its signature is valid."""
    service = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "role": "service_role",
            "iss": SUPABASE_ISSUER,
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        SUPABASE_SECRET,
        algorithm="HS256",
    )

    with caplog.at_level(logging.WARNING, logger="metriq.auth"):
        response = exchange(client, service)

    assert response.status_code == 401
    assert response.json()["detail"] == TOKEN_REFUSED
    assert "service_role" in caplog.text


def test_an_hs256_token_without_the_project_secret_is_refused(client, accounts, monkeypatch, caplog):
    """A clear failure beats a silent fallback on to the wrong key."""
    monkeypatch.setattr(settings, "AUTH_PROVIDER", "hybrid")
    monkeypatch.setattr(settings, "SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", SUPABASE_ANON_KEY)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", None)

    with caplog.at_level(logging.WARNING, logger="metriq.auth"):
        response = exchange(client, supabase_token(email=accounts["emails"][ENGINEER]))

    assert response.status_code == 401
    assert response.json()["detail"] == TOKEN_REFUSED
    assert "SUPABASE_JWT_SECRET" in caplog.text


# ------------------------------------------------------------------- authorisation


def test_a_claim_in_the_token_cannot_elevate_the_role(client, accounts, supabase):
    """The audit's third requirement. The token is genuinely signed by the
    project, and every role-shaped claim in it says super admin."""
    email = accounts["emails"][ENGINEER]
    token = supabase_token(
        email=email,
        app_metadata={"provider": "email", "roles": [SUPER_ADMIN]},
        user_metadata={"role_code": SUPER_ADMIN, "role": SUPER_ADMIN},
    )

    session = exchange(client, token)
    assert session.status_code == 200, session.text
    assert session.json()["user"]["role_code"] == ENGINEER, "the token claimed otherwise"

    me = client.get(
        f"{API}/me", headers={"Authorization": f"Bearer {session.json()['access_token']}"}
    ).json()

    assert me["user"]["role_code"] == ENGINEER
    assert not set(me["permissions"]) & set(ADMIN_ONLY), (
        "a token claim granted permissions the user's own role does not have"
    )


def test_our_own_session_tokens_survive_auth_provider_supabase(client, accounts, monkeypatch):
    """The exchange mints a MetrIQ access token, so `AUTH_PROVIDER=supabase`
    must not switch our own token format off - it did, and every request after
    sign-in returned 401."""
    monkeypatch.setattr(settings, "AUTH_PROVIDER", "supabase")
    monkeypatch.setattr(settings, "SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", SUPABASE_ANON_KEY)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", SUPABASE_SECRET)

    session = exchange(client, supabase_token(email=accounts["emails"][ENGINEER]))
    assert session.status_code == 200, session.text

    me = client.get(
        f"{API}/me", headers={"Authorization": f"Bearer {session.json()['access_token']}"}
    )
    assert me.status_code == 200, me.text


def test_a_supabase_token_is_not_exchanged_when_the_provider_is_local(client, accounts, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_PROVIDER", "local")
    monkeypatch.setattr(settings, "SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", SUPABASE_ANON_KEY)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", SUPABASE_SECRET)

    assert exchange(client, supabase_token(email=accounts["emails"][ENGINEER])).status_code == 404


def test_an_unlinked_identity_is_refused_with_a_clear_message(client, supabase):
    """Signing up in Supabase does not by itself grant access to MetrIQ; an
    administrator still has to provision the account."""
    response = exchange(client, supabase_token(email="nobody-linked@example.test"))

    assert response.status_code == 401
    assert "No MetrIQ account is linked" in response.json()["detail"]


def test_an_inactive_account_cannot_exchange_a_token(client, accounts, supabase):
    from app.database import SessionLocal
    from app.models import User

    db = SessionLocal()
    try:
        user = db.get(User, uuid.UUID(accounts["user_ids"][ENGINEER]))
        user.is_active = False
        db.commit()
    finally:
        db.close()
    try:
        response = exchange(client, supabase_token(email=accounts["emails"][ENGINEER]))
        assert response.status_code == 403
    finally:
        db = SessionLocal()
        try:
            user = db.get(User, uuid.UUID(accounts["user_ids"][ENGINEER]))
            user.is_active = True
            db.commit()
        finally:
            db.close()


def test_a_metriq_token_is_not_accepted_by_the_exchange(client, tokens, supabase):
    """Only the identity provider's tokens are exchanged here; accepting our own
    would make the endpoint a way to renew a session indefinitely."""
    local = tokens[ENGINEER]["Authorization"].split(" ", 1)[1]

    response = exchange(client, local)

    assert response.status_code == 401
    assert "identity provider" in response.json()["detail"]


def test_the_exchange_needs_a_token_at_all(client, supabase):
    assert client.post(f"{API}/auth/session").status_code == 401


# ------------------------------------------------------------------ configuration


def test_the_exchange_is_absent_when_supabase_is_not_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", None)
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", None)

    assert exchange(client, supabase_token()).status_code == 404


def test_a_production_deployment_refuses_the_local_password(client, accounts, monkeypatch):
    """Hiding the form is not enough - a caller can post here directly."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "DEMO_MODE", False)

    response = client.post(
        f"{API}/auth/login",
        json={"email": accounts["emails"][ENGINEER], "password": accounts["password"]},
    )

    assert response.status_code == 403
    # Formal and self-contained: the deployment's own choice is stated, without
    # naming the vendor or the endpoint that performs the exchange.
    assert "not enabled" in response.json()["detail"]
    assert "Supabase" not in response.json()["detail"]


def test_a_demonstration_deployment_still_allows_the_local_password(client, accounts, monkeypatch):
    """The deployed demonstration runs with DEMO_MODE on; that is what makes its
    seeded accounts legitimate rather than a hole."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "DEMO_MODE", True)

    response = client.post(
        f"{API}/auth/login",
        json={"email": accounts["emails"][ENGINEER], "password": accounts["password"]},
    )

    assert response.status_code == 200, response.text


def test_development_allows_the_local_password(client, accounts):
    assert client.post(
        f"{API}/auth/login",
        json={"email": accounts["emails"][ENGINEER], "password": accounts["password"]},
    ).status_code == 200


# ------------------------------------------------------------------ /auth/config


def test_the_config_endpoint_publishes_only_public_values(client, supabase, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "service-role-must-not-leak")

    body = client.get(f"{API}/auth/config").json()

    assert body["provider"] == "hybrid"
    assert body["supabase"] == {
        "url": SUPABASE_URL,
        "auth_url": SUPABASE_ISSUER,
        "anon_key": SUPABASE_ANON_KEY,
    }
    assert body["password_reset"] == "supabase"
    # The whole payload, not just the field we remembered to check: the
    # service-role key bypasses row-level security and must never reach a browser.
    assert "service-role-must-not-leak" not in client.get(f"{API}/auth/config").text


def test_the_config_endpoint_says_when_supabase_is_not_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", None)
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", None)
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    body = client.get(f"{API}/auth/config").json()

    assert body["supabase"] is None
    assert body["password_reset"] is None
    assert body["local_login"] is False, "production without demo mode must not offer it"
    assert body["demo_mode"] is False


def test_the_content_security_policy_allows_the_supabase_origin(supabase):
    """Sign-in calls Supabase from the browser, so a policy of connect-src 'self'
    would fail it silently."""
    assert SUPABASE_URL in _application_csp()


def test_the_content_security_policy_is_unchanged_without_supabase(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", None)
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", None)
    monkeypatch.setattr(settings, "CSP_EXTRA_CONNECT_SRC", "")

    policy = _application_csp()

    assert policy.endswith("connect-src 'self'; manifest-src 'self'")