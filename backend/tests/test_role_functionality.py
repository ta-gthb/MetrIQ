"""Per-role functional verification (PRD Appendix A / section 19).

Where the authorization tests answer "may this role call this endpoint?", this
module answers "does the work this role is accountable for actually complete?".
Each role is driven through the full set of actions it owns, so a regression in
any role's happy path fails the suite instead of surfacing in production.

Runs against the throwaway SQLite database built by ``conftest``.
"""

from __future__ import annotations

import uuid

from app.security.permissions import (
    ALL_PERMISSIONS,
    APPROVER,
    AUDITOR,
    ENGINEER,
    LAB_ADMIN,
    P,
    PERMISSION_CATALOGUE,
    REVIEWER,
    ROLE_DEFINITIONS,
    SUPER_ADMIN,
)

API = "/api/v1"

#: Every catalogue permission that mutates state. The Auditor must hold none.
WRITE_PERMISSIONS = frozenset(
    {
        P.USERS_MANAGE,
        P.USERS_MANAGE_SCOPED,
        P.LABS_MANAGE,
        P.MASTERS_MANAGE,
        P.EQUIPMENT_MANAGE,
        P.RULES_MANAGE,
        P.RULES_REVIEW,
        P.RULES_APPROVE,
        P.CASES_CREATE,
        P.CASES_ASSIGN,
        P.CASES_EDIT,
        P.CASES_SUBMIT,
        P.CASES_REVIEW,
        P.CASES_REQUEST_CORRECTION,
        P.CASES_APPROVE,
        P.CASES_FINALIZE,
        P.TESTS_EDIT,
        P.TESTS_EDIT_OWN,
        P.TESTS_VALIDATE,
        P.REPORTS_GENERATE,
        P.OVERRIDE_REQUEST,
        P.OVERRIDE_APPROVE,
        P.AI_USE,
        P.AI_MANAGE,
        P.SETTINGS_MANAGE,
    }
)

PINNED_ROLE_ORDER = [SUPER_ADMIN, LAB_ADMIN, ENGINEER, REVIEWER, APPROVER, AUDITOR]


def unique_email(prefix: str) -> str:
    return f"{prefix}.{uuid.uuid4().hex[:10]}@metriq.test"


def submit(client, tokens, case_id, role=ENGINEER):
    response = client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens[role])
    assert response.status_code == 200, response.text
    return response.json()


def verify(client, tokens, case_id, role=REVIEWER):
    response = client.post(f"{API}/cases/{case_id}/verify", json={}, headers=tokens[role])
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------- role matrix
def test_the_role_matrix_is_a_consistent_hierarchy():
    catalogue = {code for code, _description, _category in PERMISSION_CATALOGUE}

    for code, definition in ROLE_DEFINITIONS.items():
        assert definition.permissions <= catalogue, f"{code} grants unknown permissions"

    assert ROLE_DEFINITIONS[SUPER_ADMIN].permissions == catalogue == ALL_PERMISSIONS

    ranks = [ROLE_DEFINITIONS[code].rank for code in PINNED_ROLE_ORDER]
    assert ranks == sorted(ranks), "role ranks must order from most to least privileged"

    # The read-only role must be exactly that.
    assert not (ROLE_DEFINITIONS[AUDITOR].permissions & WRITE_PERMISSIONS)
    assert P.AUDIT_VIEW in ROLE_DEFINITIONS[AUDITOR].permissions

    # Separation of duties: the engineer cannot review or approve, the reviewer
    # cannot approve or finalize, and the approver cannot perform technical review.
    assert not (ROLE_DEFINITIONS[ENGINEER].permissions & {P.CASES_REVIEW, P.CASES_APPROVE, P.CASES_FINALIZE})
    assert P.CASES_REVIEW in ROLE_DEFINITIONS[REVIEWER].permissions
    assert not (ROLE_DEFINITIONS[REVIEWER].permissions & {P.CASES_APPROVE, P.CASES_FINALIZE})
    assert P.CASES_APPROVE in ROLE_DEFINITIONS[APPROVER].permissions
    assert P.CASES_FINALIZE in ROLE_DEFINITIONS[APPROVER].permissions
    assert not (ROLE_DEFINITIONS[APPROVER].permissions & {P.CASES_REVIEW, P.CASES_REQUEST_CORRECTION})


def test_every_role_reaches_its_dashboard_and_profile(client, tokens, accounts):
    seen_roles = set()
    for role in PINNED_ROLE_ORDER:
        email = accounts["emails"][role]
        profile = client.get(f"{API}/me", headers=tokens[role])
        assert profile.status_code == 200, profile.text
        body = profile.json()
        assert body["user"]["email"] == email
        assert body["user"]["role_code"] == role
        assert body["role_name"]

        permissions = client.get(f"{API}/me/permissions", headers=tokens[role])
        assert permissions.status_code == 200, permissions.text
        assert {row["code"] for row in permissions.json()} == set(body["permissions"])

        for path in ("/dashboard/summary", "/dashboard/pending", "/dashboard/ai-review",
                     "/notifications", "/laboratories", "/standards", "/test-definitions",
                     "/rulesets", "/report-templates", "/calculations/engine", "/ai/features"):
            response = client.get(f"{API}{path}", headers=tokens[role])
            assert response.status_code == 200, f"{role} {path}: {response.status_code} {response.text}"

        cases = client.get(f"{API}/cases", headers=tokens[role])
        assert cases.status_code == 200, cases.text
        assert "items" in cases.json()
        seen_roles.add(role)

    assert seen_roles == set(PINNED_ROLE_ORDER)


# --------------------------------------------------------------- super admin
def test_super_admin_administers_every_platform_function(client, tokens, accounts):
    profile = client.get(f"{API}/me", headers=tokens[SUPER_ADMIN]).json()
    assert set(profile["permissions"]) == set(ALL_PERMISSIONS)

    catalogue = client.get(f"{API}/admin/permissions", headers=tokens[SUPER_ADMIN])
    assert catalogue.status_code == 200
    assert len(catalogue.json()) == len(ALL_PERMISSIONS)

    roles = client.get(f"{API}/admin/roles", headers=tokens[SUPER_ADMIN]).json()
    assert [row["code"] for row in roles] == PINNED_ROLE_ORDER
    assert roles[0]["permission_count"] == len(ALL_PERMISSIONS)

    # User lifecycle: create, edit, reset the password and sign in with it.
    email = unique_email("provisioned.engineer")
    created = client.post(
        f"{API}/users",
        json={
            "email": email,
            "full_name": "Provisioned Engineer",
            "password": "Provisional@2026",
            "role_code": ENGINEER,
            "laboratory_id": accounts["laboratory_id"],
            "designation": "Metrologist",
        },
        headers=tokens[SUPER_ADMIN],
    )
    assert created.status_code == 201, created.text
    new_user_id = created.json()["id"]

    updated = client.patch(
        f"{API}/users/{new_user_id}", json={"designation": "Senior Metrologist"}, headers=tokens[SUPER_ADMIN]
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["designation"] == "Senior Metrologist"

    searched = client.get(f"{API}/users", params={"search": "Provisioned"}, headers=tokens[SUPER_ADMIN])
    assert searched.status_code == 200
    assert any(row["id"] == new_user_id for row in searched.json()["items"])

    rotated = client.post(f"{API}/users/{new_user_id}/reset-password", headers=tokens[SUPER_ADMIN])
    assert rotated.status_code == 200, rotated.text
    temporary = rotated.json()["temporary_password"]
    assert temporary

    login = client.post(f"{API}/auth/login", json={"email": email, "password": temporary})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["role_code"] == ENGINEER

    # Laboratory lifecycle.
    lab_code = f"SLAB-{uuid.uuid4().hex[:6].upper()}"
    lab = client.post(
        f"{API}/laboratories", json={"name": "Supervision Lab", "code": lab_code}, headers=tokens[SUPER_ADMIN]
    )
    assert lab.status_code == 201, lab.text
    patched = client.patch(
        f"{API}/laboratories/{lab.json()['id']}",
        json={"name": "Supervision Laboratory", "code": lab_code},
        headers=tokens[SUPER_ADMIN],
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Supervision Laboratory"

    # System settings.
    settings = client.get(f"{API}/settings", headers=tokens[SUPER_ADMIN])
    assert settings.status_code == 200
    put = client.put(
        f"{API}/settings/role-verification.key",
        params={"category": "general", "description": "Written by the role test"},
        json={"enabled": True},
        headers=tokens[SUPER_ADMIN],
    )
    assert put.status_code == 200, put.text
    assert put.json()["value"] == {"enabled": True}

    # Ruleset governance (audit items 6 and 10). The seeded set is active only
    # provisionally, because the bootstrap may not speak for a metrologist; an
    # unreviewed set cannot be activated through the ordinary path, and the
    # governed path - review every rule, submit, approve, activate - succeeds.
    rulesets = client.get(f"{API}/rulesets", headers=tokens[SUPER_ADMIN]).json()
    assert rulesets, "the reference ruleset must be seeded"
    # More than one standard version is seeded; the ruleset under test is the
    # one that actually carries rules (the shipped OIML R 76-1 catalogue).
    version_id = max(rulesets, key=lambda row: row["rule_count"])["standard_version_id"]

    refused = client.post(f"{API}/rulesets/{version_id}/activate", headers=tokens[SUPER_ADMIN])
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["unreviewed_rules"], refused.text

    package = client.get(
        f"{API}/rulesets/{version_id}/review-package", headers=tokens[SUPER_ADMIN]
    )
    assert package.status_code == 200, package.text
    assert package.json()["reviewed_rule_count"] == 0, package.text

    detail = client.get(f"{API}/rulesets/{version_id}", headers=tokens[SUPER_ADMIN]).json()
    assert detail["rules"]
    for rule in detail["rules"]:
        reviewed = client.post(
            f"{API}/rulesets/{version_id}/rules/{rule['rule_version_id']}/review",
            json={
                "decision": "approved",
                "reviewer_name": "Dr A. Metrologist",
                "reviewer_credentials": "Legal metrology reviewer, R 76 scope",
                "source_revision": "OIML R 76-1:2006 (E)",                "change_note": "Bands, tolerances and clause references checked.",
                "boundary_cases_passed": True,
                "boundary_case_reference": "tests/test_rule_boundaries.py",
            },
            headers=tokens[REVIEWER],
        )
        assert reviewed.status_code == 200, reviewed.text

    submitted = client.post(
        f"{API}/rulesets/{version_id}/submit-review",
        json={"note": "Ready for technical review"},
        headers=tokens[SUPER_ADMIN],
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["lifecycle_state"] == "under_review"

    approved = client.post(
        f"{API}/rulesets/{version_id}/approve",
        json={
            "reviewer_name": "Dr A. Metrologist",
            "reviewer_credentials": "Legal metrology reviewer, R 76 scope",
            "source_revision": "OIML R 76-1:2006 (E)",
            "change_note": "Whole rule set approved for verification use.",
        },
        headers=tokens[APPROVER],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["can_activate"] is True, approved.text

    activated = client.post(
        f"{API}/rulesets/{version_id}/activate",
        json={"reason": "Domain review complete"},
        headers=tokens[APPROVER],
    )
    assert activated.status_code == 200, activated.text
    body = activated.json()
    assert body["is_active"] is True
    assert body["activation_basis"] == "domain_review", body
    assert body["approved_by_name"] == "Dr A. Metrologist", body

    # Master data.
    manufacturer = client.post(
        f"{API}/manufacturers",
        json={"name": "Super Admin Balances", "code": f"MFR-{uuid.uuid4().hex[:6]}"},
        headers=tokens[SUPER_ADMIN],
    )
    assert manufacturer.status_code == 201, manufacturer.text


# ---------------------------------------------------------------- lab admin
def test_lab_admin_is_limited_to_its_own_laboratory(client, tokens, accounts):
    listed = client.get(f"{API}/users", params={"page_size": 200}, headers=tokens[LAB_ADMIN])
    assert listed.status_code == 200, listed.text
    rows = listed.json()["items"]
    assert rows
    assert {row["laboratory_id"] for row in rows} == {accounts["laboratory_id"]}

    # Cannot create platform administrators.
    forbidden_role = client.post(
        f"{API}/users",
        json={
            "email": unique_email("escalation.attempt"),
            "full_name": "Escalation Attempt",
            "password": "Provisional@2026",
            "role_code": SUPER_ADMIN,
        },
        headers=tokens[LAB_ADMIN],
    )
    assert forbidden_role.status_code == 403, forbidden_role.text

    # Cannot create users in another laboratory.
    foreign_lab = client.post(
        f"{API}/users",
        json={
            "email": unique_email("foreign.lab"),
            "full_name": "Foreign Lab User",
            "password": "Provisional@2026",
            "role_code": ENGINEER,
            "laboratory_id": str(uuid.uuid4()),
        },
        headers=tokens[LAB_ADMIN],
    )
    assert foreign_lab.status_code == 403, foreign_lab.text

    # Can create within its own laboratory.
    created = client.post(
        f"{API}/users",
        json={
            "email": unique_email("lab.staff"),
            "full_name": "Lab Staff",
            "password": "Provisional@2026",
            "role_code": ENGINEER,
        },
        headers=tokens[LAB_ADMIN],
    )
    assert created.status_code == 201, created.text
    assert created.json()["laboratory_id"] == accounts["laboratory_id"]

    # Scoped administration stops at the platform settings and rule activation.
    assert client.get(f"{API}/settings", headers=tokens[LAB_ADMIN]).status_code == 403
    rulesets = client.get(f"{API}/rulesets", headers=tokens[LAB_ADMIN]).json()
    assert (
        client.post(
            f"{API}/rulesets/{rulesets[0]['standard_version_id']}/activate", headers=tokens[LAB_ADMIN]
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{API}/users",
            json={"email": unique_email("x"), "full_name": "Xavier", "password": "Provisional@2026",
                  "role_code": LAB_ADMIN},
            headers=tokens[LAB_ADMIN],
        ).status_code
        == 403
    )
    # PRD Appendix A grants "Manage laboratories: Y" to the Laboratory Admin,
    # but the update path is deliberately limited to the administrator's own lab.
    sibling = client.post(
        f"{API}/laboratories",
        json={"name": "Sibling Lab", "code": f"SLAB-{uuid.uuid4().hex[:6].upper()}"},
        headers=tokens[SUPER_ADMIN],
    )
    assert sibling.status_code == 201, sibling.text
    assert (
        client.patch(
            f"{API}/laboratories/{sibling.json()['id']}",
            json={"name": "Taken Over", "code": sibling.json()["code"]},
            headers=tokens[LAB_ADMIN],
        ).status_code
        == 403
    )


def test_lab_admin_manages_masters_and_can_review(client, tokens, accounts, new_case, fill_case, attach_evidence):
    suffix = uuid.uuid4().hex[:6]

    manufacturer = client.post(
        f"{API}/manufacturers", json={"name": "Lab Admin Balances", "code": f"MFR-{suffix}"},
        headers=tokens[LAB_ADMIN],
    )
    assert manufacturer.status_code == 201, manufacturer.text
    manufacturer_id = manufacturer.json()["id"]

    updated = client.patch(
        f"{API}/manufacturers/{manufacturer_id}", json={"name": "Lab Admin Balances Ltd", "code": f"MFR-{suffix}"},
        headers=tokens[LAB_ADMIN],
    )
    assert updated.status_code == 200, updated.text

    applicant = client.post(
        f"{API}/applicants", json={"name": f"Applicant {suffix}"}, headers=tokens[LAB_ADMIN]
    )
    assert applicant.status_code == 201, applicant.text

    instrument = client.post(
        f"{API}/instruments",
        json={
            "manufacturer_id": manufacturer_id,
            "model": f"LAB-{suffix}",
            "instrument_class": "III",
            "max_capacity": "30000",
            "verification_scale_interval": "10",
            "unit": "g",
        },
        headers=tokens[LAB_ADMIN],
    )
    assert instrument.status_code == 201, instrument.text
    instrument_id = instrument.json()["id"]
    assert client.get(f"{API}/instruments/{instrument_id}", headers=tokens[LAB_ADMIN]).status_code == 200

    equipment = client.post(
        f"{API}/equipment",
        json={"code": f"EQ-{suffix}", "name": "Class F1 weight set", "equipment_type": "weights"},
        headers=tokens[LAB_ADMIN],
    )
    assert equipment.status_code == 201, equipment.text
    calibration = client.post(
        f"{API}/equipment/{equipment.json()['id']}/calibrations",
        json={"certificate_no": f"CAL-{suffix}", "issued_by": "National Laboratory"},
        headers=tokens[LAB_ADMIN],
    )
    assert calibration.status_code == 201, calibration.text

    # Case creation with the master records, then assignment.
    case = new_case(instrument_id=instrument_id)
    case_id = case["id"]
    assigned = client.post(
        f"{API}/cases/{case_id}/assignments",
        json={
            "engineer_id": accounts["user_ids"][ENGINEER],
            "reviewer_id": accounts["user_ids"][LAB_ADMIN],
            "approver_id": accounts["user_ids"][APPROVER],
            "reason": "Laboratory manager assignment",
        },
        headers=tokens[LAB_ADMIN],
    )
    assert assigned.status_code == 200, assigned.text

    fill_case(case_id)
    attach_evidence(case_id)
    submit(client, tokens, case_id, role=ENGINEER)

    # The laboratory manager is the assigned reviewer and can verify.
    verified = verify(client, tokens, case_id, role=LAB_ADMIN)
    assert verified["status"] == "VERIFIED"


# ----------------------------------------------------------------- engineer
def test_engineer_runs_the_complete_evaluation_cycle(client, tokens, new_case, observations, png_factory):
    case = new_case(title="Engineer full cycle")
    case_id = case["id"]
    assert case["status"] == "DRAFT"
    assert case["engineer_id"] == accounts_id(client, tokens, ENGINEER)

    plan = client.get(f"{API}/cases/{case_id}/tests", headers=tokens[ENGINEER])
    assert plan.status_code == 200, plan.text
    tests = plan.json()
    assert tests
    assert all(test["applicability_status"] in {"APPLICABLE", "NOT_APPLICABLE"} for test in tests)

    detail = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()
    assert detail["test_plan"]

    for test in detail["tests"]:
        code = test["test_code"]
        rows = observations.get(code)
        assert rows is not None, f"no reference observations for {code}"

        saved = client.put(
            f"{API}/tests/{test['id']}/observations",
            json={"observations": rows, "replace": True},
            headers=tokens[ENGINEER],
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"] == "IN_PROGRESS"

        validated = client.post(f"{API}/tests/{test['id']}/validate", headers=tokens[ENGINEER])
        assert validated.status_code == 200, validated.text
        assert validated.json()["is_valid"], validated.json()["errors"]

        calculated = client.post(f"{API}/tests/{test['id']}/calculate", headers=tokens[ENGINEER])
        assert calculated.status_code == 200, calculated.text
        assert calculated.json()["status"] == "PASS", calculated.json()

        completed = client.patch(
            f"{API}/tests/{test['id']}", json={"mark_complete": True}, headers=tokens[ENGINEER]
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "COMPLETED"

    # Two clear photographs are mandatory before submission.
    blocked = client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens[ENGINEER])
    assert blocked.status_code == 422, blocked.text
    assert set(blocked.json()["detail"]["missing_evidence"]) == {
        "nameplate_photograph",
        "test_setup_photograph",
    }

    for category in ("nameplate_photograph", "test_setup_photograph"):
        uploaded = client.post(
            f"{API}/cases/{case_id}/attachments",
            files={"file": (f"{category}.png", png_factory(), "image/png")},
            data={"category": category, "caption": category, "auto_classify": "false"},
            headers=tokens[ENGINEER],
        )
        assert uploaded.status_code == 201, uploaded.text

    requirements = client.get(f"{API}/cases/{case_id}/evidence-requirements", headers=tokens[ENGINEER])
    assert requirements.status_code == 200, requirements.text
    assert requirements.json()["satisfied"] is True, requirements.json()

    attachments = client.get(f"{API}/cases/{case_id}/attachments", headers=tokens[ENGINEER])
    assert attachments.status_code == 200
    assert len(attachments.json()) == 2

    submitted = submit(client, tokens, case_id)
    assert submitted["status"] == "TESTING_COMPLETED"
    assert submitted["overall_result"] == "PASS"

    # The engineer's authority stops at submission.
    for path, payload in (
        (f"/cases/{case_id}/assignments", {"engineer_id": accounts_id(client, tokens, ENGINEER)}),
        (f"/cases/{case_id}/verify", {}),
        (f"/cases/{case_id}/approve", {}),
        (f"/cases/{case_id}/finalize", {}),
        ("/users", {}),
    ):
        response = client.post(f"{API}{path}", json=payload, headers=tokens[ENGINEER])
        assert response.status_code == 403, f"engineer POST {path}: {response.status_code} {response.text}"


def accounts_id(client, tokens, role):
    return client.get(f"{API}/me", headers=tokens[role]).json()["user"]["id"]


# ----------------------------------------------------------------- reviewer
def test_reviewer_verifies_and_requests_corrections(client, tokens, case_factory, accounts):
    case = case_factory()
    case_id = case["id"]
    submit(client, tokens, case_id)

    verified = verify(client, tokens, case_id)
    assert verified["status"] == "VERIFIED"
    assert verified["verified_at"]

    notes = client.get(f"{API}/notifications", headers=tokens[APPROVER])
    assert notes.status_code == 200
    assert any(case["application_no"] in (row["title"] or "") for row in notes.json())

    # A verification notification goes to the approver, not to the reviewer.
    trail = client.get(f"{API}/cases/{case_id}/workflow-actions", headers=tokens[REVIEWER])
    assert trail.status_code == 200
    assert [row["action"] for row in trail.json()] == ["CREATE", "ASSIGN", "SUBMIT", "VERIFY"]
    assert trail.json()[-1]["actor_role"] == REVIEWER

    # Corrections return the case to the engineer.
    second = case_factory()
    second_id = second["id"]
    submit(client, tokens, second_id)
    corrected = client.post(
        f"{API}/cases/{second_id}/request-correction",
        json={"reason": "Uncertainty budget omits the eccentricity contribution."},
        headers=tokens[REVIEWER],
    )
    assert corrected.status_code == 200, corrected.text
    body = corrected.json()
    assert body["status"] == "CORRECTION_REQUIRED"
    assert "eccentricity" in body["last_correction_reason"]

    engineer_notes = client.get(f"{API}/notifications", headers=tokens[ENGINEER]).json()
    assert any(second["application_no"] in (row["title"] or "") for row in engineer_notes)

    # Short reasons are rejected.
    assert (
        client.post(
            f"{API}/cases/{second_id}/request-correction",
            json={"reason": "nope"},
            headers=tokens[REVIEWER],
        ).status_code
        in {409, 422}
    )

    # The engineer fixes it and resubmits.
    resubmitted = submit(client, tokens, second_id)
    assert resubmitted["status"] == "TESTING_COMPLETED"

    # The reviewer cannot approve or finalize.
    for path in ("approve", "reject", "finalize"):
        response = client.post(f"{API}/cases/{case_id}/{path}", json={"reason": "Not my job"}, headers=tokens[REVIEWER])
        assert response.status_code == 403, f"reviewer {path}: {response.status_code} {response.text}"

    # Nor edit the engineer's observations.
    test_id = case["tests"][0]["id"] if "tests" in case else None
    if test_id is None:
        test_id = client.get(f"{API}/cases/{case_id}", headers=tokens[REVIEWER]).json()["tests"][0]["id"]
    assert (
        client.put(f"{API}/tests/{test_id}/observations", json={"observations": []},
                   headers=tokens[REVIEWER]).status_code
        == 403
    )


# ----------------------------------------------------------------- approver
def test_approver_approves_finalizes_and_downloads_the_report(client, tokens, case_factory):
    case = case_factory()
    case_id = case["id"]
    submit(client, tokens, case_id)
    verify(client, tokens, case_id)

    generated = client.post(
        f"{API}/cases/{case_id}/reports/generate",
        json={"formats": ["pdf", "docx"], "lock": False},
        headers=tokens[APPROVER],
    )
    assert generated.status_code == 201, generated.text
    report_id = generated.json()["id"]

    approved = client.post(
        f"{API}/cases/{case_id}/approve",
        json={"reason": "Design and metrological characteristics conform."},
        headers=tokens[APPROVER],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"

    finalized = client.post(f"{API}/cases/{case_id}/finalize", json={}, headers=tokens[APPROVER])
    assert finalized.status_code == 200, finalized.text
    body = finalized.json()
    assert body["status"] == "FINALIZED"
    assert body["finalized_at"]
    report = body["report"]
    assert report["report_no"]
    assert report["verification_code"]
    assert set(report["formats"]) == {"pdf", "docx"}

    # Repository search and revision history.
    search = client.get(f"{API}/reports", params={"search": body["application_no"]}, headers=tokens[APPROVER])
    assert search.status_code == 200, search.text
    assert any(row["id"] == report_id for row in search.json()["items"])

    revisions = client.get(f"{API}/reports/{report_id}/revisions", headers=tokens[APPROVER])
    assert revisions.status_code == 200
    assert {row["format"] for row in revisions.json()} == {"pdf", "docx"}

    snapshot = client.get(f"{API}/reports/{report_id}/snapshot", headers=tokens[APPROVER])
    assert snapshot.status_code == 200
    assert snapshot.json()

    pdf = client.get(f"{API}/reports/{report_id}/download", params={"fmt": "pdf"}, headers=tokens[APPROVER])
    assert pdf.status_code == 200, pdf.text
    assert pdf.content.startswith(b"%PDF")
    assert pdf.headers["X-Content-SHA256"]

    docx = client.get(f"{API}/reports/{report_id}/pdf", headers=tokens[APPROVER])
    assert docx.status_code == 200

    # A finalized case is sealed.
    assert (
        client.patch(f"{API}/cases/{case_id}", json={"title": "Tampered"}, headers=tokens[APPROVER]).status_code
        in {403, 404, 409}
    )

    # Rejection requires a substantive reason and reopens nothing.
    second = case_factory()
    second_id = second["id"]
    submit(client, tokens, second_id)
    verify(client, tokens, second_id)
    assert (
        client.post(f"{API}/cases/{second_id}/reject", json={"reason": "no"}, headers=tokens[APPROVER]).status_code
        == 422
    )
    rejected = client.post(
        f"{API}/cases/{second_id}/reject",
        json={"reason": "Eccentricity results exceed the permitted deviation."},
        headers=tokens[APPROVER],
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"

    # The approver cannot perform technical review.
    assert (
        client.post(f"{API}/cases/{second_id}/verify", json={}, headers=tokens[APPROVER]).status_code
        in {403, 409}
    )


# ------------------------------------------------------------------ auditor
def test_auditor_reads_everything_and_writes_nothing(client, tokens, case_factory, accounts):
    case = case_factory()
    case_id = case["id"]
    submit(client, tokens, case_id)
    verify(client, tokens, case_id)

    readable = (
        "/cases",
        f"/cases/{case_id}",
        f"/cases/{case_id}/tests",
        f"/cases/{case_id}/audit-logs",
        f"/cases/{case_id}/workflow-actions",
        f"/cases/{case_id}/attachments",
        f"/cases/{case_id}/evidence-requirements",
        f"/cases/{case_id}/reports",
        f"/cases/{case_id}/test-plan",
        f"/cases/{case_id}/conditions",
        "/audit-logs",
        "/reports",
        "/reports?only_final=false",
        "/laboratories",
        "/manufacturers",
        "/applicants",
        "/instruments",
        "/equipment",
        "/standards",
        "/rules",
        "/rulesets",
        "/test-definitions",
        "/report-templates",
        "/dashboard/summary",
        "/dashboard/pending",
        "/dashboard/ai-review",
        "/notifications",
        "/ai/features",
    )
    for path in readable:
        response = client.get(f"{API}{path}", headers=tokens[AUDITOR])
        assert response.status_code == 200, f"auditor GET {path}: {response.status_code} {response.text}"

    # User administration is an administrative function, not an auditor read.
    assert client.get(f"{API}/users", headers=tokens[AUDITOR]).status_code == 403

    # The auditor holds the platform-wide audit view.
    audit = client.get(f"{API}/audit-logs", headers=tokens[AUDITOR])
    assert audit.json()["meta"]["total"] > 0

    test_id = client.get(f"{API}/cases/{case_id}", headers=tokens[AUDITOR]).json()["tests"][0]["id"]
    blocked = (
        ("POST", "/cases", {"instrument": {"model": "X", "instrument_class": "III", "max_capacity": "1000",
                                           "verification_scale_interval": "1"}}),
        ("POST", "/users", {"email": unique_email("auditor"), "full_name": "Auditor Attempt",
                            "password": "Provisional@2026", "role_code": ENGINEER}),
        ("POST", "/laboratories", {"name": "Audit Lab", "code": "AUDIT-1"}),
        ("POST", "/manufacturers", {"name": "Audit Manufacturer"}),
        ("PUT", "/settings/auditor.key", {"value": 1}),
        ("POST", f"/cases/{case_id}/submit", {}),
        ("POST", f"/cases/{case_id}/review", {"decision": "verify"}),
        ("POST", f"/cases/{case_id}/verify", {}),
        ("POST", f"/cases/{case_id}/approve", {}),
        ("POST", f"/cases/{case_id}/request-correction", {"reason": "Auditor tampering attempt"}),
        ("POST", f"/cases/{case_id}/finalize", {}),
        ("POST", f"/cases/{case_id}/cancel", {"reason": "Auditor tampering attempt"}),
        ("POST", f"/cases/{case_id}/assignments", {"engineer_id": accounts["user_ids"][ENGINEER]}),
        ("POST", f"/cases/{case_id}/test-plan", {}),
        ("POST", f"/cases/{case_id}/conditions", {}),
        ("PATCH", f"/cases/{case_id}", {"title": "Auditor tampering attempt"}),
        ("PATCH", f"/tests/{test_id}", {"mark_complete": True}),
        ("PUT", f"/tests/{test_id}/observations", {"observations": []}),
        ("POST", f"/tests/{test_id}/calculate", {}),
        ("POST", f"/tests/{test_id}/validate", {}),
        ("POST", f"/tests/{test_id}/anomaly-check", {}),
        ("POST", f"/tests/{test_id}/override", {"reason": "Auditor tampering attempt"}),
        ("POST", f"/cases/{case_id}/reports/generate", {"formats": ["pdf"]}),
        ("POST", "/rulesets/00000000-0000-4000-8000-000000000001/activate", {}),
        ("PATCH", "/ai/features/nameplate_extraction", {"enabled": True}),
    )
    for method, path, payload in blocked:
        response = client.request(method, f"{API}{path}", json=payload, headers=tokens[AUDITOR])
        assert response.status_code == 403, f"auditor {method} {path}: {response.status_code} {response.text}"

    # Photograph upload requires a write permission.
    upload = client.post(
        f"{API}/cases/{case_id}/attachments",
        files={"file": ("audit.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"category": "nameplate_photograph"},
        headers=tokens[AUDITOR],
    )
    assert upload.status_code == 403, upload.text