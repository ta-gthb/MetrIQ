"""Portal rules for the second batch of updates (role boundaries, releases).

These tests hold the boundaries added with the laboratory-assignment model:

* an evaluation case is opened only from the Laboratory Admin / Manager portal;
* test equipment is written by the owning laboratory and read by everyone else;
* the instrument register publishes only approved evaluations;
* a report is released only after the Approving Authority approves, and the
  released document is downloadable by the Laboratory Admin / Manager (its own
  laboratory) and the Super Admin alone;
* case personnel must come from the case's own laboratory, and the Super Admin
  assigns users to a laboratory in the first place;
* every case carries a discussion for its team, and every role has one support
  thread with the Super Admin.

Runs against the throwaway SQLite database built by ``conftest``.
"""

from __future__ import annotations

import uuid

from app.security.permissions import APPROVER, ENGINEER, LAB_ADMIN, REVIEWER, SUPER_ADMIN

from tests.conftest import INSTRUMENT_TEMPLATE

API = "/api/v1"
PASSWORD = "Str0ng!Pass"
UNKNOWN_ID = "00000000-0000-4000-8000-000000000001"


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _laboratory_payload() -> dict:
    tag = uuid.uuid4().hex[:8]
    return {
        "name": f"Batch Two Laboratory {tag}",
        "code": f"B2-{tag}".upper(),
        "location": "Karnataka",
        "address": "Plot 3, Metrology Park",
        "contact_email": f"lab.{tag}@metriq.test",
    }


def register_user(client, tokens, *, role_code: str, laboratory_id: str | None) -> dict:
    response = client.post(
        f"{API}/users",
        json={
            "email": f"{_unique('batch2')}@metriq.test",
            "full_name": f"Batch Two {_unique('User')}",
            "password": PASSWORD,
            "role_code": role_code,
            "designation": "Officer",
            "laboratory_id": laboratory_id,
        },
        headers=tokens[SUPER_ADMIN],
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_laboratory(client, tokens) -> dict:
    response = client.post(f"{API}/laboratories", json=_laboratory_payload(), headers=tokens[SUPER_ADMIN])
    assert response.status_code == 201, response.text
    return response.json()


def drive_to_approved(client, tokens, case_id: str) -> None:
    submitted = client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens[ENGINEER])
    assert submitted.status_code == 200, submitted.text
    verified = client.post(f"{API}/cases/{case_id}/verify", json={}, headers=tokens[REVIEWER])
    assert verified.status_code == 200, verified.text
    approved = client.post(
        f"{API}/cases/{case_id}/approve",
        json={"reason": "Conforming evaluation; release for the regression check."},
        headers=tokens[APPROVER],
    )
    assert approved.status_code == 200, approved.text


# --------------------------------------------------------------- case creation
def test_only_the_laboratory_admin_opens_evaluation_cases(client, tokens, accounts):
    payload = {
        "title": "Batch two case-creation gate",
        "purpose": "Role gate regression check",
        "instrument": {**INSTRUMENT_TEMPLATE, "serial_number": f"B2-{uuid.uuid4().hex[:8]}"},
        "engineer_id": accounts["user_ids"][ENGINEER],
    }
    for role in (ENGINEER, REVIEWER, APPROVER, SUPER_ADMIN):
        response = client.post(f"{API}/cases", json=payload, headers=tokens[role])
        assert response.status_code == 403, (role, response.text)

    created = client.post(f"{API}/cases", json=payload, headers=tokens[LAB_ADMIN])
    assert created.status_code == 201, created.text
    assert created.json()["laboratory_id"] == accounts["laboratory_id"]


# -------------------------------------------------------------------- equipment
def test_equipment_writes_belong_to_the_laboratory_admin(client, tokens):
    tag = uuid.uuid4().hex[:8]
    payload = {
        "code": f"B2EQ-{tag}",
        "name": f"Batch two equipment {tag}",
        "equipment_type": "weights",
        "manufacturer": "Batch Two Instruments",
        "model": f"MDL-{tag}",
        "serial_no": f"SN-{tag}",
        "unit": "g",
        "accuracy_class": "F1",
    }
    for role in (SUPER_ADMIN, ENGINEER, REVIEWER, APPROVER):
        response = client.post(f"{API}/equipment", json=payload, headers=tokens[role])
        assert response.status_code == 403, (role, response.text)

    created = client.post(f"{API}/equipment", json=payload, headers=tokens[LAB_ADMIN])
    assert created.status_code == 201, created.text
    equipment_id = created.json()["id"]

    for role in (SUPER_ADMIN, ENGINEER):
        response = client.delete(f"{API}/equipment/{equipment_id}", headers=tokens[role])
        assert response.status_code == 403, (role, response.text)

    removed = client.delete(f"{API}/equipment/{equipment_id}", headers=tokens[LAB_ADMIN])
    assert removed.status_code == 204, removed.text


# ---------------------------------------------------------- instrument register
def test_the_instrument_register_publishes_approved_evaluations_only(client, tokens, case_factory):
    serial_number = f"B2-{uuid.uuid4().hex[:10]}"
    case = case_factory(instrument={**INSTRUMENT_TEMPLATE, "serial_number": serial_number})
    instrument_id = case["instrument"]["id"]

    before = client.get(f"{API}/instruments/{instrument_id}", headers=tokens[ENGINEER])
    assert before.status_code == 404, before.text
    listing = client.get(
        f"{API}/instruments", params={"search": serial_number}, headers=tokens[REVIEWER]
    ).json()
    assert listing["items"] == []

    drive_to_approved(client, tokens, case["id"])

    after = client.get(f"{API}/instruments/{instrument_id}", headers=tokens[ENGINEER])
    assert after.status_code == 200, after.text
    assert after.json()["serial_number"] == serial_number
    listing = client.get(
        f"{API}/instruments", params={"search": serial_number}, headers=tokens[APPROVER]
    ).json()
    assert [row["id"] for row in listing["items"]] == [instrument_id]


# ----------------------------------------------------------- report release gate
def test_a_report_is_released_only_after_approval(client, tokens, case_factory):
    case_id = case_factory()["id"]
    submitted = client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens[ENGINEER])
    assert submitted.status_code == 200, submitted.text
    verified = client.post(f"{API}/cases/{case_id}/verify", json={}, headers=tokens[REVIEWER])
    assert verified.status_code == 200, verified.text

    pending = client.post(
        f"{API}/cases/{case_id}/reports/generate",
        json={"formats": ["pdf"]},
        headers=tokens[APPROVER],
    )
    assert pending.status_code == 409, pending.text

    approved = client.post(
        f"{API}/cases/{case_id}/approve",
        json={"reason": "Conforming evaluation; the report may be released."},
        headers=tokens[APPROVER],
    )
    assert approved.status_code == 200, approved.text

    released = client.post(
        f"{API}/cases/{case_id}/reports/generate",
        json={"formats": ["pdf"]},
        headers=tokens[APPROVER],
    )
    assert released.status_code == 201, released.text
    download = client.get(
        f"{API}/reports/{released.json()['id']}/download",
        params={"fmt": "pdf"},
        headers=tokens[LAB_ADMIN],
    )
    assert download.status_code == 200, download.text
    assert download.content.startswith(b"%PDF")


def test_approved_reports_download_only_for_super_admin_and_lab_manager(
    client, tokens, case_factory
):
    case_id = case_factory()["id"]
    drive_to_approved(client, tokens, case_id)
    generated = client.post(
        f"{API}/cases/{case_id}/reports/generate",
        json={"formats": ["pdf"]},
        headers=tokens[APPROVER],
    )
    assert generated.status_code == 201, generated.text
    report_id = generated.json()["id"]

    # The evaluation team keeps the snapshot and revision history but cannot
    # release the document itself.
    for role in (ENGINEER, REVIEWER, APPROVER):
        response = client.get(
            f"{API}/reports/{report_id}/download", params={"fmt": "pdf"}, headers=tokens[role]
        )
        assert response.status_code == 403, (role, response.text)
    assert (
        client.get(f"{API}/reports/{report_id}/pdf", headers=tokens[ENGINEER]).status_code
        == 403
    )

    for role in (SUPER_ADMIN, LAB_ADMIN):
        response = client.get(
            f"{API}/reports/{report_id}/download", params={"fmt": "pdf"}, headers=tokens[role]
        )
        assert response.status_code == 200, (role, response.text)
        assert response.content.startswith(b"%PDF")


# ------------------------------------------------- laboratory-scoped personnel
def test_case_personnel_come_from_the_case_laboratory(client, tokens, accounts, case_factory):
    other_laboratory = create_laboratory(client, tokens)
    other_engineer = register_user(
        client, tokens, role_code=ENGINEER, laboratory_id=other_laboratory["id"]
    )

    case = case_factory()
    rejected = client.post(
        f"{API}/cases/{case['id']}/assignments",
        json={
            "engineer_id": other_engineer["id"],
            "reason": "A case may only be staffed from its own laboratory.",
        },
        headers=tokens[LAB_ADMIN],
    )
    assert rejected.status_code == 422, rejected.text
    assert "case laboratory" in rejected.json()["detail"]

    wrong_role = client.post(
        f"{API}/cases/{case['id']}/assignments",
        json={
            "engineer_id": accounts["user_ids"][REVIEWER],
            "reason": "An engineer slot may only name a registered engineer.",
        },
        headers=tokens[LAB_ADMIN],
    )
    assert wrong_role.status_code == 422, wrong_role.text
    assert ENGINEER in wrong_role.json()["detail"]


def test_the_super_admin_assigns_users_to_a_laboratory(client, tokens, accounts):
    other_laboratory = create_laboratory(client, tokens)
    engineer = register_user(
        client, tokens, role_code=ENGINEER, laboratory_id=other_laboratory["id"]
    )
    assert engineer["laboratory_id"] == other_laboratory["id"]

    moved = client.patch(
        f"{API}/users/{engineer['id']}",
        json={"laboratory_id": accounts["laboratory_id"]},
        headers=tokens[SUPER_ADMIN],
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["laboratory_id"] == accounts["laboratory_id"]


# --------------------------------------------------------------- case discussion
def test_a_case_discussion_is_open_to_its_team(client, tokens, case_factory):
    case_id = case_factory()["id"]
    for role in (LAB_ADMIN, ENGINEER, REVIEWER, APPROVER):
        posted = client.post(
            f"{API}/cases/{case_id}/messages",
            json={"body": f"Case note from {role}"},
            headers=tokens[role],
        )
        assert posted.status_code == 201, (role, posted.text)

    thread = client.get(f"{API}/cases/{case_id}/messages", headers=tokens[ENGINEER])
    assert thread.status_code == 200, thread.text
    rows = thread.json()
    bodies = {row["body"] for row in rows}
    for role in (LAB_ADMIN, ENGINEER, REVIEWER, APPROVER):
        assert f"Case note from {role}" in bodies
    assert all(row["sender_id"] for row in rows)
    assert all(row["created_at"] for row in rows)

    empty = client.post(
        f"{API}/cases/{case_id}/messages", json={"body": "   "}, headers=tokens[ENGINEER]
    )
    assert empty.status_code == 422, empty.text


# ------------------------------------------------------- Super Admin support
def test_every_role_can_contact_the_super_admin(client, tokens, accounts):
    for role in (LAB_ADMIN, ENGINEER, REVIEWER, APPROVER):
        posted = client.post(
            f"{API}/support/messages",
            json={"body": f"Support question from {role}"},
            headers=tokens[role],
        )
        assert posted.status_code == 201, (role, posted.text)

        own_thread = client.get(f"{API}/support/messages", headers=tokens[role])
        assert own_thread.status_code == 200, own_thread.text
        assert any(row["body"] == f"Support question from {role}" for row in own_thread.json())

        # A conversation with the Super Admin is private to its owner.
        assert client.get(f"{API}/support/threads", headers=tokens[role]).status_code == 403
        assert (
            client.get(
                f"{API}/support/threads/{accounts['user_ids'][role]}", headers=tokens[role]
            ).status_code
            == 403
        )

    threads = client.get(f"{API}/support/threads", headers=tokens[SUPER_ADMIN])
    assert threads.status_code == 200, threads.text
    thread_ids = {row["user_id"] for row in threads.json()}
    for role in (LAB_ADMIN, ENGINEER, REVIEWER, APPROVER):
        assert accounts["user_ids"][role] in thread_ids

    unknown = client.get(f"{API}/support/threads/{UNKNOWN_ID}", headers=tokens[SUPER_ADMIN])
    assert unknown.status_code == 404, unknown.text

    reply = client.post(
        f"{API}/support/threads/{accounts['user_ids'][ENGINEER]}",
        json={"body": "Super Admin reply for the regression check"},
        headers=tokens[SUPER_ADMIN],
    )
    assert reply.status_code == 201, reply.text
    engineer_thread = client.get(f"{API}/support/messages", headers=tokens[ENGINEER]).json()
    assert any(row["body"] == "Super Admin reply for the regression check" for row in engineer_thread)
