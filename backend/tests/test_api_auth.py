"""Authentication, permission and record-scope tests (FR-01/FR-02, PRD 16.3, 19.1)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.security.permissions import AUDITOR, ENGINEER, LAB_ADMIN, REVIEWER, SUPER_ADMIN

API = "/api/v1"


def login(client, accounts, role: str) -> dict:
    response = client.post(
        f"{API}/auth/login",
        json={"email": accounts["emails"][role], "password": accounts["password"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_health_is_public_and_states_the_ai_independence_contract(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["calculation_engine_independent_of_ai"] is True
    assert body["ai_provider"] == "stub"


def test_health_publishes_the_platform_clock_for_client_synchronisation(client):
    """The browser corrects a skewed workstation clock from this value."""
    body = client.get("/health").json()
    stamp = datetime.fromisoformat(body["server_time_utc"])
    assert stamp.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - stamp).total_seconds()) < 60


def test_openapi_schema_is_generated(client):
    """Every route annotation must be resolvable or /docs silently 500s."""
    response = client.get("/openapi.json")
    assert response.status_code == 200, response.text
    schema = response.json()
    assert schema["info"]["title"]
    assert len(schema["paths"]) > 50


def test_api_index_advertises_the_supported_standards(client):
    body = client.get(API).json()
    assert "OIML R 76-1:2006" in body["standards"]
    assert "OIML R 76-2:2007" in body["standards"]


def test_malformed_bearer_tokens_are_rejected_as_401_not_500(client):
    """A junk Authorization header is bad credentials, not a server fault."""
    for header in ["Bearer (none)", "Bearer abc.def.ghi", "Bearer not-a-jwt"]:
        response = client.get(f"{API}/calculations/engine", headers={"Authorization": header})
        assert response.status_code == 401, (header, response.status_code, response.text)


def test_login_returns_a_bearer_token_and_the_user_profile(client, accounts):
    body = login(client, accounts, ENGINEER)
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert body["user"]["role_code"] == ENGINEER


def test_login_is_case_insensitive_on_the_email(client, accounts):
    response = client.post(
        f"{API}/auth/login",
        json={"email": accounts["emails"][ENGINEER].upper(), "password": accounts["password"]},
    )
    assert response.status_code == 200


def test_login_rejects_a_wrong_password(client, accounts):
    response = client.post(
        f"{API}/auth/login",
        json={"email": accounts["emails"][ENGINEER], "password": "definitely-not-the-password"},
    )
    assert response.status_code == 401
    assert "Incorrect" in response.json()["detail"]


def test_login_rejects_an_unknown_account_with_the_same_message(client, accounts):
    response = client.post(
        f"{API}/auth/login",
        json={"email": "nobody@metriq.local", "password": accounts["password"]},
    )
    assert response.status_code == 401
    assert "Incorrect" in response.json()["detail"]


def test_protected_endpoints_require_authentication(client):
    assert client.get(f"{API}/me").status_code == 401
    assert client.get(f"{API}/cases").status_code == 401
    assert client.get(f"{API}/dashboard/summary").status_code == 401


def test_a_malformed_token_is_rejected(client):
    response = client.get(f"{API}/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_me_reports_the_effective_permission_set(client, tokens):
    body = client.get(f"{API}/me", headers=tokens[ENGINEER]).json()
    assert body["user"]["role_code"] == ENGINEER
    assert body["role_name"] == "Test Engineer / Metrologist"
    assert "cases.create" in body["permissions"]
    assert "cases.approve" not in body["permissions"]
    assert "users.manage" not in body["permissions"]


def test_me_lists_permission_descriptions(client, tokens):
    rows = client.get(f"{API}/me/permissions", headers=tokens[AUDITOR]).json()
    codes = {row["code"] for row in rows}
    assert "audit.view" in codes
    assert "cases.create" not in codes
    assert all(row["description"] for row in rows)


def test_refresh_issues_a_new_access_token(client, accounts):
    session = login(client, accounts, SUPER_ADMIN)
    refreshed = client.post(f"{API}/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert refreshed.status_code == 200
    access = refreshed.json()["access_token"]
    assert client.get(f"{API}/me", headers={"Authorization": f"Bearer {access}"}).status_code == 200


def test_an_access_token_cannot_be_used_as_a_refresh_token(client, accounts):
    session = login(client, accounts, SUPER_ADMIN)
    response = client.post(f"{API}/auth/refresh", json={"refresh_token": session["access_token"]})
    assert response.status_code == 401


def test_engineer_cannot_manage_users_or_laboratories(client, tokens):
    assert client.get(f"{API}/users", headers=tokens[ENGINEER]).status_code == 403
    # Engineers may read laboratory reference data (they need it to describe a
    # case) but must not be able to create or change it.
    assert client.get(f"{API}/laboratories", headers=tokens[ENGINEER]).status_code == 200
    response = client.post(
        f"{API}/laboratories",
        json={"name": "Rogue Laboratory", "code": "LAB-ROGUE"},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 403


def test_auditor_can_read_but_cannot_create_cases(client, tokens, new_case):
    case = new_case()
    assert client.get(f"{API}/cases", headers=tokens[AUDITOR]).status_code == 200
    response = client.post(
        f"{API}/cases",
        json={"title": "Auditor attempt", "instrument": {"model": "X", "instrument_class": "III",
                                                        "max_capacity": "1000",
                                                        "verification_scale_interval": "1"}},
        headers=tokens[AUDITOR],
    )
    assert response.status_code == 403
    assert case["id"]


def test_engineer_cannot_finalize_a_case(client, tokens, case_factory):
    response = client.post(
        f"{API}/cases/{case_factory()['id']}/finalize", json={}, headers=tokens[ENGINEER]
    )
    assert response.status_code == 403


def test_reviewer_cannot_approve_a_case(client, tokens, case_factory):
    response = client.post(
        f"{API}/cases/{case_factory()['id']}/approve", json={}, headers=tokens[REVIEWER]
    )
    assert response.status_code == 403


def test_only_the_assigned_reviewer_may_verify(client, tokens, case_factory):
    case_id = case_factory()["id"]
    submitted = client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens[ENGINEER])
    assert submitted.status_code == 200, submitted.text

    # The Laboratory Admin holds the review permission but is not the assigned
    # reviewer, so the record-scope check must still refuse the action.
    response = client.post(f"{API}/cases/{case_id}/verify", json={}, headers=tokens[LAB_ADMIN])
    assert response.status_code == 403
    assert "cases.review" in response.json()["detail"]


def test_cases_are_scoped_to_the_users_laboratory(client, tokens, new_case, accounts):
    case = new_case()

    laboratory = client.post(
        f"{API}/laboratories",
        json={"name": "Second Laboratory", "code": "LAB-002"},
        headers=tokens[SUPER_ADMIN],
    )
    assert laboratory.status_code == 201, laboratory.text

    created = client.post(
        f"{API}/users",
        json={
            "email": "second.engineer@metriq.local",
            "full_name": "Second Engineer",
            "password": "MetrIQ@2026",
            "role_code": ENGINEER,
            "laboratory_id": laboratory.json()["id"],
        },
        headers=tokens[SUPER_ADMIN],
    )
    assert created.status_code == 201, created.text

    session = login(client, {"emails": {ENGINEER: "second.engineer@metriq.local"},
                             "password": "MetrIQ@2026"}, ENGINEER)
    other = {"Authorization": f"Bearer {session['access_token']}"}

    listed = [item["id"] for item in client.get(f"{API}/cases", headers=other).json()["items"]]
    assert case["id"] not in listed
    # Cross-tenant probing returns 404 rather than 403 so nothing is disclosed.
    assert client.get(f"{API}/cases/{case['id']}", headers=other).status_code == 404


def test_dashboard_summary_is_available_to_every_role(client, tokens):
    for role, headers in tokens.items():
        response = client.get(f"{API}/dashboard/summary", headers=headers)
        assert response.status_code == 200, f"{role}: {response.text}"
        assert "kpis" in response.json() or "cards" in response.json()
