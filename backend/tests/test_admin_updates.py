"""Portal updates: registration rules, permissions, MPE, gating (this release).

These tests cover the administration changes end to end: user registration
policy and lifecycle, editable role permissions, laboratory and equipment
registration requirements, MPE lookup, report-section requirements, assignment
role separation, execution write gating and cancellation authority.

Runs against the throwaway SQLite database built by ``conftest``.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.database import SessionLocal
from app.security.permissions import (
    APPROVER,
    AUDITOR,
    ENGINEER,
    LAB_ADMIN,
    REVIEWER,
    SUPER_ADMIN,
)
from scripts.seed_identity import seed_roles_and_permissions

API = "/api/v1"
PASSWORD = "Str0ng!Pass"


def unique_text(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def register_user(
    client,
    tokens,
    accounts,
    *,
    role_code: str = ENGINEER,
    designation: str = "Officer",
    password: str = PASSWORD,
    prefix: str = "user",
) -> dict:
    payload = {
        "email": f"{unique_text(prefix)}@metriq.test",
        "full_name": f"Portal {prefix.title()} {uuid.uuid4().hex[:6]}",
        "password": password,
        "role_code": role_code,
        "designation": designation,
        "laboratory_id": accounts["laboratory_id"],
    }
    response = client.post(f"{API}/users", json=payload, headers=tokens[SUPER_ADMIN])
    assert response.status_code == 201, response.text
    return response.json()


def equipment_payload(prefix: str = "eq") -> dict:
    tag = uuid.uuid4().hex[:8]
    return {
        "code": f"{prefix}-{tag}",
        "name": f"Portal test equipment {tag}",
        "equipment_type": "weights",
        "manufacturer": "Portal Instruments",
        "model": f"MDL-{tag}",
        "serial_no": f"SN-{tag}",
        "unit": "g",
        "accuracy_class": "F1",
    }


def calibrated_equipment(client, tokens, prefix: str = "cal") -> dict:
    created = client.post(
        f"{API}/equipment", json=equipment_payload(prefix), headers=tokens[SUPER_ADMIN]
    )
    assert created.status_code == 201, created.text
    record = created.json()
    calibration = client.post(
        f"{API}/equipment/{record['id']}/calibrations",
        json={
            "certificate_no": "CERT-" + record["code"],
            "issued_by": "National Metrology Institute",
            "issue_date": (date.today() - timedelta(days=30)).isoformat(),
            "valid_until": (date.today() + timedelta(days=365)).isoformat(),
        },
        headers=tokens[SUPER_ADMIN],
    )
    assert calibration.status_code == 201, calibration.text
    return record


def find_user(client, tokens, user_id: str):
    page = client.get(
        f"{API}/users", params={"page_size": 200}, headers=tokens[SUPER_ADMIN]
    )
    assert page.status_code == 200, page.text
    return next((row for row in page.json()["items"] if row["id"] == user_id), None)


# ------------------------------------------------------------ user lifecycle
def test_user_registration_applies_password_and_designation_policy(client, tokens, accounts):
    base = {
        "email": f"{unique_text('policy')}@metriq.test",
        "full_name": "Policy Candidate",
        "role_code": ENGINEER,
        "designation": "Officer",
        "laboratory_id": accounts["laboratory_id"],
    }

    short = client.post(f"{API}/users", json={**base, "password": "Ab1!xyz"}, headers=tokens[SUPER_ADMIN])
    assert short.status_code == 422, short.text

    no_special = client.post(
        f"{API}/users", json={**base, "password": "Password123"}, headers=tokens[SUPER_ADMIN]
    )
    assert no_special.status_code == 422, no_special.text

    no_digit = client.post(
        f"{API}/users", json={**base, "password": "Password!xy"}, headers=tokens[SUPER_ADMIN]
    )
    assert no_digit.status_code == 422, no_digit.text

    bad_designation = client.post(
        f"{API}/users",
        json={**base, "password": PASSWORD, "designation": "Manager"},
        headers=tokens[SUPER_ADMIN],
    )
    assert bad_designation.status_code == 422, bad_designation.text

    missing_designation = client.post(
        f"{API}/users",
        json={key: value for key, value in base.items() if key != "designation"} | {"password": PASSWORD},
        headers=tokens[SUPER_ADMIN],
    )
    assert missing_designation.status_code == 422, missing_designation.text

    created = client.post(
        f"{API}/users", json={**base, "password": PASSWORD, "designation": "Operator"},
        headers=tokens[SUPER_ADMIN],
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["designation"] == "Operator"
    assert body["user_code"].startswith("temadm"), body["user_code"]

    duplicate = client.post(
        f"{API}/users", json={**base, "password": PASSWORD}, headers=tokens[SUPER_ADMIN]
    )
    assert duplicate.status_code == 409, duplicate.text


def test_user_edit_ignores_role_change_and_password_reset_needs_no_current_value(
    client, tokens, accounts
):
    created = register_user(client, tokens, accounts, prefix="edit")
    user_id = created["id"]

    # The edit payload may not move the account between roles.
    edited = client.patch(
        f"{API}/users/{user_id}",
        json={"full_name": "Edited Portal User", "role_code": REVIEWER, "designation": "Assistant"},
        headers=tokens[SUPER_ADMIN],
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["role_code"] == ENGINEER
    assert edited.json()["designation"] == "Assistant"

    reset = client.post(
        f"{API}/users/{user_id}/reset-password",
        json={"password": "N3w!Portal9"},
        headers=tokens[SUPER_ADMIN],
    )
    assert reset.status_code == 200, reset.text

    old_login = client.post(
        f"{API}/auth/login", json={"user_id": created["email"], "password": PASSWORD}
    )
    assert old_login.status_code == 401, old_login.text
    new_login = client.post(
        f"{API}/auth/login", json={"user_id": created["email"], "password": "N3w!Portal9"}
    )
    assert new_login.status_code == 200, new_login.text

    # Account Status: a disabled account keeps its record but cannot sign in.
    disabled = client.patch(
        f"{API}/users/{user_id}", json={"is_active": False}, headers=tokens[SUPER_ADMIN]
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["is_active"] is False
    blocked = client.post(
        f"{API}/auth/login", json={"user_id": created["email"], "password": "N3w!Portal9"}
    )
    assert blocked.status_code in {401, 403}, blocked.text


def test_user_deletion_is_permanent_but_governed_accounts_are_protected(
    client, tokens, accounts, new_case
):
    disposable = register_user(client, tokens, accounts, prefix="disposable")
    removed = client.delete(f"{API}/users/{disposable['id']}", headers=tokens[SUPER_ADMIN])
    assert removed.status_code == 204, removed.text
    assert find_user(client, tokens, disposable["id"]) is None

    governed = register_user(client, tokens, accounts, prefix="governed")
    case = new_case(title="Governed account deletion gate")
    assigned = client.post(
        f"{API}/cases/{case['id']}/assignments",
        json={
            "engineer_id": governed["id"],
            "reviewer_id": accounts["user_ids"][REVIEWER],
            "approver_id": accounts["user_ids"][APPROVER],
            "reason": "Deletion-protection regression check",
        },
        headers=tokens[SUPER_ADMIN],
    )
    assert assigned.status_code == 200, assigned.text
    refused = client.delete(f"{API}/users/{governed['id']}", headers=tokens[SUPER_ADMIN])
    assert refused.status_code == 409, refused.text

    # Nobody may delete their own account.
    self_delete = client.delete(
        f"{API}/users/{accounts['user_ids'][SUPER_ADMIN]}", headers=tokens[SUPER_ADMIN]
    )
    assert self_delete.status_code == 409, self_delete.text


def test_view_users_work_detail_reports_every_registered_user(client, tokens, accounts):
    created = register_user(client, tokens, accounts, prefix="worklog")
    response = client.get(f"{API}/admin/users/work-summary", headers=tokens[SUPER_ADMIN])
    assert response.status_code == 200, response.text
    rows = {row["user_id"]: row for row in response.json()}
    assert created["id"] in rows, "the newly registered user must appear in the work detail"
    row = rows[created["id"]]
    for field in (
        "cases_created",
        "cases_as_engineer",
        "cases_as_reviewer",
        "cases_as_approver",
        "open_cases",
        "tests_total",
        "tests_completed",
    ):
        assert field in row, field

    assert (
        client.get(f"{API}/admin/users/work-summary", headers=tokens[AUDITOR]).status_code
        == 403
    )


# --------------------------------------------------------- role permissions
def test_role_permissions_are_editable_and_apply_immediately(client, tokens):
    matrix = client.get(f"{API}/admin/roles", headers=tokens[SUPER_ADMIN])
    assert matrix.status_code == 200, matrix.text
    rows = {row["code"]: row for row in matrix.json()}
    assert rows[SUPER_ADMIN]["locked"] is True
    baseline = set(rows[REVIEWER]["permissions"])

    granted = client.put(
        f"{API}/admin/roles/{REVIEWER}/permissions",
        json={"permissions": sorted(baseline | {"cases.assign"})},
        headers=tokens[SUPER_ADMIN],
    )
    assert granted.status_code == 200, granted.text
    assert "cases.assign" in granted.json()["permissions"]

    live = client.get(f"{API}/me/permissions", headers=tokens[REVIEWER])
    assert live.status_code == 200, live.text
    codes = {row["code"] for row in live.json()}
    assert "cases.assign" in codes, "an edited permission must apply immediately"

    locked = client.put(
        f"{API}/admin/roles/{SUPER_ADMIN}/permissions",
        json={"permissions": sorted(baseline)},
        headers=tokens[SUPER_ADMIN],
    )
    assert locked.status_code == 403, locked.text

    unknown_role = client.put(
        f"{API}/admin/roles/NO_SUCH_ROLE/permissions",
        json={"permissions": []},
        headers=tokens[SUPER_ADMIN],
    )
    assert unknown_role.status_code == 404, unknown_role.text

    unknown_permission = client.put(
        f"{API}/admin/roles/{REVIEWER}/permissions",
        json={"permissions": ["permission.that.does.not.exist"]},
        headers=tokens[SUPER_ADMIN],
    )
    assert unknown_permission.status_code == 422, unknown_permission.text

    restored = client.put(
        f"{API}/admin/roles/{REVIEWER}/permissions",
        json={"permissions": sorted(baseline)},
        headers=tokens[SUPER_ADMIN],
    )
    assert restored.status_code == 200, restored.text
    live = client.get(f"{API}/me/permissions", headers=tokens[REVIEWER]).json()
    assert {row["code"] for row in live} == baseline


def test_a_customised_role_survives_reference_data_seeding(client, tokens):
    rows = {row["code"]: row for row in client.get(f"{API}/admin/roles", headers=tokens[SUPER_ADMIN]).json()}
    baseline = set(rows[AUDITOR]["permissions"])
    assert "ai.view" in baseline

    reduced = sorted(baseline - {"ai.view"})
    response = client.put(
        f"{API}/admin/roles/{AUDITOR}/permissions",
        json={"permissions": reduced},
        headers=tokens[SUPER_ADMIN],
    )
    assert response.status_code == 200, response.text

    db = SessionLocal()
    try:
        seed_roles_and_permissions(db)
        db.commit()
    finally:
        db.close()

    after = {row["code"]: row for row in client.get(f"{API}/admin/roles", headers=tokens[SUPER_ADMIN]).json()}
    assert after[AUDITOR]["customised"] is True
    assert set(after[AUDITOR]["permissions"]) == set(reduced), "the edit must survive a reseed"

    client.put(
        f"{API}/admin/roles/{AUDITOR}/permissions",
        json={"permissions": sorted(baseline)},
        headers=tokens[SUPER_ADMIN],
    )


# ------------------------------------------------ laboratories and equipment
def test_laboratory_registration_requires_address_location_and_contact(client, tokens):
    base = {"name": unique_text("Portal Laboratory"), "code": unique_text("PLAB").upper()[:16]}

    missing_address = client.post(
        f"{API}/laboratories",
        json={**base, "location": "Karnataka", "contact_email": "lab@metriq.test"},
        headers=tokens[SUPER_ADMIN],
    )
    assert missing_address.status_code == 422, missing_address.text

    missing_location = client.post(
        f"{API}/laboratories",
        json={**base, "address": "Plot 1, Metrology Park", "contact_email": "lab@metriq.test"},
        headers=tokens[SUPER_ADMIN],
    )
    assert missing_location.status_code == 422, missing_location.text

    missing_contact = client.post(
        f"{API}/laboratories",
        json={**base, "address": "Plot 1, Metrology Park", "location": "Karnataka"},
        headers=tokens[SUPER_ADMIN],
    )
    assert missing_contact.status_code == 422, missing_contact.text

    created = client.post(
        f"{API}/laboratories",
        json={
            **base,
            "location": "Karnataka",
            "address": "Plot 1, Metrology Park",
            "contact_email": "lab@metriq.test",
        },
        headers=tokens[SUPER_ADMIN],
    )
    assert created.status_code == 201, created.text


def test_equipment_registration_requires_every_field_and_supports_deletion(
    client, tokens, new_case
):
    incomplete = equipment_payload("incomplete")
    incomplete.pop("manufacturer")
    response = client.post(f"{API}/equipment", json=incomplete, headers=tokens[SUPER_ADMIN])
    assert response.status_code == 422, response.text

    wrong_type = equipment_payload("badtype")
    wrong_type["equipment_type"] = "banana"
    response = client.post(f"{API}/equipment", json=wrong_type, headers=tokens[SUPER_ADMIN])
    assert response.status_code == 422, response.text

    created = client.post(
        f"{API}/equipment", json=equipment_payload("disposable"), headers=tokens[SUPER_ADMIN]
    )
    assert created.status_code == 201, created.text
    equipment_id = created.json()["id"]

    removed = client.delete(f"{API}/equipment/{equipment_id}", headers=tokens[SUPER_ADMIN])
    assert removed.status_code == 204, removed.text
    gone = client.delete(f"{API}/equipment/{equipment_id}", headers=tokens[SUPER_ADMIN])
    assert gone.status_code == 404, gone.text

    in_use = calibrated_equipment(client, tokens, "inuse")
    case = new_case(title="Equipment deletion gate")
    attached = client.post(
        f"{API}/cases/{case['id']}/equipment",
        json={"equipment_id": in_use["id"], "role": "reference standard"},
        headers=tokens[ENGINEER],
    )
    assert attached.status_code == 201, attached.text
    refused = client.delete(f"{API}/equipment/{in_use['id']}", headers=tokens[SUPER_ADMIN])
    assert refused.status_code == 409, refused.text


# --------------------------------------------------------------- MPE lookup
def test_mpe_lookup_resolves_class_iii_and_falls_back_to_the_active_ruleset(client, tokens):
    response = client.post(
        f"{API}/calculations/mpe",
        json={"instrument_class": "III", "load": "10000", "e": "10", "stage": "verification"},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["band_label"], body
    assert body["mpe_value"] is not None, body

    # A case may name a standard version whose ruleset is not the active one;
    # the lookup must still resolve instead of reporting "no bands configured".
    stale = client.post(
        f"{API}/calculations/mpe",
        json={
            "instrument_class": "III",
            "load": "10000",
            "e": "10",
            "stage": "verification",
            "standard_version_id": str(uuid.uuid4()),
        },
        headers=tokens[ENGINEER],
    )
    assert stale.status_code == 200, stale.text
    assert stale.json()["band_label"], stale.text


# ---------------------------------------------------------- report template
def test_report_sections_can_be_declared_required_or_optional(client, tokens):
    templates = client.get(f"{API}/report-templates", headers=tokens[SUPER_ADMIN])
    assert templates.status_code == 200, templates.text
    template = next(item for item in templates.json() if item["versions"])
    version = template["versions"][-1]
    sections = version["section_map"].get("sections") or []
    assert sections, "the seeded template must carry editable sections"

    original = [{"number": str(s["number"]), "required": bool(s.get("required"))} for s in sections]
    flipped = [dict(item, required=not item["required"]) for item in original[:1]]
    edited = client.put(
        f"{API}/report-templates/{template['id']}/sections",
        json={"sections": flipped},
        headers=tokens[SUPER_ADMIN],
    )
    assert edited.status_code == 200, edited.text
    after = {row["number"]: row["required"] for row in edited.json()["sections"]}
    assert after[flipped[0]["number"]] is flipped[0]["required"]

    forbidden = client.put(
        f"{API}/report-templates/{template['id']}/sections",
        json={"sections": original},
        headers=tokens[LAB_ADMIN],
    )
    assert forbidden.status_code == 403, forbidden.text

    unknown = client.put(
        f"{API}/report-templates/{template['id']}/sections",
        json={"sections": [{"number": "99999", "required": True}]},
        headers=tokens[SUPER_ADMIN],
    )
    assert unknown.status_code == 422, unknown.text

    restored = client.put(
        f"{API}/report-templates/{template['id']}/sections",
        json={"sections": original},
        headers=tokens[SUPER_ADMIN],
    )
    assert restored.status_code == 200, restored.text


# ------------------------------------------------------ assignments and gates
def test_assignments_only_accept_the_matching_role(client, tokens, accounts, new_case):
    case = new_case(title="Assignment role separation")

    wrong_engineer = client.post(
        f"{API}/cases/{case['id']}/assignments",
        json={
            "engineer_id": accounts["user_ids"][REVIEWER],
            "reviewer_id": accounts["user_ids"][REVIEWER],
            "approver_id": accounts["user_ids"][APPROVER],
        },
        headers=tokens[SUPER_ADMIN],
    )
    assert wrong_engineer.status_code == 422, wrong_engineer.text

    wrong_reviewer = client.post(
        f"{API}/cases/{case['id']}/assignments",
        json={
            "engineer_id": accounts["user_ids"][ENGINEER],
            "reviewer_id": accounts["user_ids"][ENGINEER],
            "approver_id": accounts["user_ids"][APPROVER],
        },
        headers=tokens[SUPER_ADMIN],
    )
    assert wrong_reviewer.status_code == 422, wrong_reviewer.text

    valid = client.post(
        f"{API}/cases/{case['id']}/assignments",
        json={
            "engineer_id": accounts["user_ids"][ENGINEER],
            "reviewer_id": accounts["user_ids"][REVIEWER],
            "approver_id": accounts["user_ids"][APPROVER],
            "reason": "Role separation regression check",
        },
        headers=tokens[SUPER_ADMIN],
    )
    assert valid.status_code == 200, valid.text


def test_conditions_and_case_equipment_are_engineer_only(client, tokens, accounts, new_case):
    case = new_case(title="Conditions write gate")
    condition = {
        "label": "Portal gate",
        "temperature_c": "21",
        "max_temperature_c": "22",
        "end_temperature_c": "21.5",
    }
    for role in (LAB_ADMIN, REVIEWER, APPROVER):
        blocked = client.post(
            f"{API}/cases/{case['id']}/conditions", json=condition, headers=tokens[role]
        )
        assert blocked.status_code == 403, f"{role}: {blocked.status_code} {blocked.text}"

    allowed = client.post(
        f"{API}/cases/{case['id']}/conditions", json=condition, headers=tokens[ENGINEER]
    )
    assert allowed.status_code == 201, allowed.text

    equipment = calibrated_equipment(client, tokens, "gate")
    blocked = client.post(
        f"{API}/cases/{case['id']}/equipment",
        json={"equipment_id": equipment["id"], "role": "reference standard"},
        headers=tokens[LAB_ADMIN],
    )
    assert blocked.status_code == 403, blocked.text
    attached = client.post(
        f"{API}/cases/{case['id']}/equipment",
        json={"equipment_id": equipment["id"], "role": "reference standard"},
        headers=tokens[ENGINEER],
    )
    assert attached.status_code == 201, attached.text
    usage_id = attached.json()["id"]
    blocked = client.delete(
        f"{API}/cases/{case['id']}/equipment/{usage_id}", headers=tokens[LAB_ADMIN]
    )
    assert blocked.status_code == 403, blocked.text


def test_execution_writes_are_engineer_only(client, tokens, accounts, new_case):
    case = new_case(title="Execution write gate")
    assigned = client.post(
        f"{API}/cases/{case['id']}/assignments",
        json={
            "engineer_id": accounts["user_ids"][ENGINEER],
            "reviewer_id": accounts["user_ids"][REVIEWER],
            "approver_id": accounts["user_ids"][APPROVER],
            "reason": "Execution gate regression check",
        },
        headers=tokens[SUPER_ADMIN],
    )
    assert assigned.status_code == 200, assigned.text
    plan = client.get(f"{API}/cases/{case['id']}/tests", headers=tokens[ENGINEER])
    assert plan.status_code == 200, plan.text
    test_id = plan.json()[0]["id"]

    blocked_calls = [
        ("PUT", f"/tests/{test_id}/observations", {"observations": [], "replace": True}, LAB_ADMIN),
        ("PATCH", f"/tests/{test_id}", {"mark_complete": True}, LAB_ADMIN),
        ("POST", f"/tests/{test_id}/calculate", {}, LAB_ADMIN),
        ("POST", f"/tests/{test_id}/calculate", {}, APPROVER),
    ]
    for method, path, payload, role in blocked_calls:
        response = client.request(method, f"{API}{path}", json=payload, headers=tokens[role])
        assert response.status_code == 403, f"{role} {method} {path}: {response.status_code} {response.text}"

    # The assigned engineer still owns the execution write path.
    saved = client.put(
        f"{API}/tests/{test_id}/observations",
        json={"observations": [{"observation_no": 1, "position_label": "start", "load": "0", "indication": "0"}], "replace": True},
        headers=tokens[ENGINEER],
    )
    assert saved.status_code == 200, saved.text

    # Read-only validation stays available to the reviewing roles.
    validation = client.post(f"{API}/tests/{test_id}/validate", headers=tokens[REVIEWER])
    assert validation.status_code == 200, validation.text


def test_cancellation_and_submission_follow_the_authority_rules(
    client, tokens, accounts, new_case, case_factory
):
    case = new_case(title="Cancellation gate")
    for role in (ENGINEER, REVIEWER):
        blocked = client.post(
            f"{API}/cases/{case['id']}/cancel",
            json={"reason": "Cancellation authority regression check"},
            headers=tokens[role],
        )
        assert blocked.status_code == 403, f"{role}: {blocked.status_code} {blocked.text}"
    cancelled = client.post(
        f"{API}/cases/{case['id']}/cancel",
        json={"reason": "Cancellation authority regression check"},
        headers=tokens[LAB_ADMIN],
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"

    ready = case_factory(title="Submission authority gate")
    for role in (REVIEWER, APPROVER):
        blocked = client.post(
            f"{API}/cases/{ready['id']}/submit", json={}, headers=tokens[role]
        )
        assert blocked.status_code == 403, f"{role}: {blocked.status_code} {blocked.text}"
    submitted = client.post(
        f"{API}/cases/{ready['id']}/submit", json={}, headers=tokens[LAB_ADMIN]
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "TESTING_COMPLETED"
