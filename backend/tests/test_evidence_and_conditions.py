"""Evidence linked to tests, and conditions captured start/max/end (item 12).

Two promises are held here:

* a photograph or record that supports a specific test is *linked to that test*,
  so the report can show which document belongs to which procedure, and a
  procedure that declares mandatory evidence cannot be completed without one;
* environmental conditions are recorded across the observation period - the
  reading at the start, the maximum seen, and the reading at the end - and the
  record refuses a set of readings that cannot all be true.
"""

from __future__ import annotations

import uuid

import pytest

from app.database import SessionLocal
from app.models import TestDefinition
from app.services.report_engine.snapshot import build_report_snapshot
from tests.conftest import png_bytes

API = "/api/v1"
ENGINEER = "ENGINEER"


@pytest.fixture
def db(database):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _test_by_code(client, tokens, case_id: str, code: str) -> dict:
    detail = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()
    return next(item for item in detail["tests"] if item["test_code"] == code)


def _upload(
    client,
    tokens,
    case_id: str,
    category: str = "test_setup_photograph",
    *,
    test_id: str | None = None,
) -> dict:
    data = {"category": category, "caption": f"{category} for item 12", "auto_classify": "false"}
    if test_id is not None:
        data["test_instance_id"] = test_id
    response = client.post(
        f"{API}/cases/{case_id}/attachments",
        files={"file": (f"{category}.png", png_bytes(), "image/png")},
        data=data,
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 201, response.text
    return response.json()


def _link(client, tokens, attachment_id: str, test_id: str):
    return client.post(
        f"{API}/attachments/{attachment_id}/links",
        json={"test_instance_id": test_id},
        headers=tokens[ENGINEER],
    )


def _require_evidence(db, test_code: str, *, required: bool):
    """Flip a catalogue entry's evidence requirement, and return the entry."""
    definition = (
        db.query(TestDefinition).filter(TestDefinition.test_code == test_code).one_or_none()
    )
    assert definition is not None
    definition.evidence_requirements = {
        **(definition.evidence_requirements or {}),
        "required": required,
    }
    db.commit()
    return definition


# ----------------------------------------------------------------- conditions ---

def test_conditions_are_recorded_at_the_start_the_maximum_and_the_end(
    client, tokens, new_case
):
    case_id = new_case()["id"]
    test = _test_by_code(client, tokens, case_id, "T-TEMP-NL")
    response = client.post(
        f"{API}/cases/{case_id}/conditions",
        json={
            "label": "Ambient (test bench)",
            "test_instance_id": test["id"],
            "temperature_c": "20.5",
            "max_temperature_c": "22.0",
            "end_temperature_c": "21.0",
            "relative_humidity_pct": "45.0",
            "max_relative_humidity_pct": "52.0",
            "end_relative_humidity_pct": "48.0",
            "started_at": "2026-09-30T09:00:00Z",
            "ended_at": "2026-09-30T15:00:00Z",
            "notes": "Chamber held for the whole observation period.",
        },
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["max_temperature_c"] == "22.000"
    assert body["end_temperature_c"] == "21.000"
    assert body["test_instance_id"] == test["id"]

    listed = client.get(f"{API}/cases/{case_id}/conditions", headers=tokens[ENGINEER]).json()
    assert len(listed) == 1
    assert listed[0]["max_relative_humidity_pct"] == "52.000"


def test_a_reading_above_the_recorded_maximum_is_refused(client, tokens, new_case):
    case_id = new_case()["id"]
    above = client.post(
        f"{API}/cases/{case_id}/conditions",
        json={"temperature_c": "24", "max_temperature_c": "22"},
        headers=tokens[ENGINEER],
    )
    assert above.status_code == 422
    assert "cannot exceed the maximum" in above.json()["detail"]

    closing = client.post(
        f"{API}/cases/{case_id}/conditions",
        json={"end_temperature_c": "25", "max_temperature_c": "22"},
        headers=tokens[ENGINEER],
    )
    assert closing.status_code == 422

    backwards = client.post(
        f"{API}/cases/{case_id}/conditions",
        json={"started_at": "2026-09-30T15:00:00Z", "ended_at": "2026-09-30T09:00:00Z"},
        headers=tokens[ENGINEER],
    )
    assert backwards.status_code == 422
    assert "cannot end before it starts" in backwards.json()["detail"]


def test_conditions_cannot_name_a_test_from_another_case(client, tokens, new_case):
    first = new_case()["id"]
    second = new_case()["id"]
    foreign = _test_by_code(client, tokens, second, "T-WP")
    response = client.post(
        f"{API}/cases/{first}/conditions",
        json={"test_instance_id": foreign["id"]},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 422


# ------------------------------------------------------------------- evidence ---

def test_evidence_is_linked_to_the_test_it_supports(client, tokens, new_case):
    case_id = new_case()["id"]
    test = _test_by_code(client, tokens, case_id, "T-WP")
    attachment = _upload(client, tokens, case_id)

    response = _link(client, tokens, attachment["id"], test["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    # The upload also files the attachment against the case itself; the link
    # under test is the one that names the test.
    test_links = [link for link in body["links"] if link["test_instance_id"]]
    assert len(test_links) == 1
    assert test_links[0]["test_code"] == "T-WP"
    assert test_links[0]["revision_no"] == 1

    listed = client.get(f"{API}/cases/{case_id}/attachments", headers=tokens[ENGINEER]).json()
    assert [link["test_instance_id"] for link in listed[0]["links"] if link["test_instance_id"]] == [
        test["id"]
    ]

    again = _link(client, tokens, attachment["id"], test["id"])
    assert again.status_code == 409
    assert "already linked" in again.json()["detail"]

    removed = client.delete(
        f"{API}/attachments/{attachment['id']}/links/{test_links[0]['id']}",
        headers=tokens[ENGINEER],
    )
    assert removed.status_code == 204
    listed = client.get(f"{API}/cases/{case_id}/attachments", headers=tokens[ENGINEER]).json()
    assert [link for link in listed[0]["links"] if link["test_instance_id"]] == []


def test_evidence_cannot_be_linked_to_a_superseded_test(client, tokens, case_factory):
    case_id = case_factory()["id"]
    original = _test_by_code(client, tokens, case_id, "T-WP")
    replacement = client.post(
        f"{API}/tests/{original['id']}/retest",
        json={"reason": "Reference weights re-calibrated; repeat the test."},
        headers=tokens[ENGINEER],
    ).json()
    attachment = _upload(client, tokens, case_id)

    refused = _link(client, tokens, attachment["id"], original["id"])
    assert refused.status_code == 409
    assert "superseded" in refused.json()["detail"]
    allowed = _link(client, tokens, attachment["id"], replacement["id"])
    assert allowed.status_code == 201, allowed.text


def test_evidence_cannot_be_linked_across_cases(client, tokens, new_case):
    first = new_case()["id"]
    second = new_case()["id"]
    attachment = _upload(client, tokens, first)
    foreign = _test_by_code(client, tokens, second, "T-WP")
    refused = _link(client, tokens, attachment["id"], foreign["id"])
    assert refused.status_code == 422


def test_an_upload_can_name_the_test_it_supports(client, tokens, new_case):
    case_id = new_case()["id"]
    test = _test_by_code(client, tokens, case_id, "T-WP")
    attachment = _upload(client, tokens, case_id, test_id=test["id"])
    assert [link["test_code"] for link in attachment["links"]] == ["T-WP"]

    listed = client.get(f"{API}/cases/{case_id}/attachments", headers=tokens[ENGINEER]).json()
    stored = next(item for item in listed if item["id"] == attachment["id"])
    assert [link["test_instance_id"] for link in stored["links"]] == [test["id"]]


def test_an_upload_cannot_support_a_foreign_or_superseded_test(client, tokens, new_case):
    first = new_case()["id"]
    second = new_case()["id"]
    foreign = _test_by_code(client, tokens, second, "T-WP")
    refused = client.post(
        f"{API}/cases/{first}/attachments",
        files={"file": ("test_setup_photograph.png", png_bytes(), "image/png")},
        data={"category": "test_setup_photograph", "test_instance_id": foreign["id"], "auto_classify": "false"},
        headers=tokens[ENGINEER],
    )
    assert refused.status_code == 422

    test = _test_by_code(client, tokens, first, "T-WP")
    replacement = client.post(
        f"{API}/tests/{test['id']}/retest",
        json={"reason": "Reference weights re-calibrated; repeat the test."},
        headers=tokens[ENGINEER],
    ).json()
    superseded = client.post(
        f"{API}/cases/{first}/attachments",
        files={"file": ("test_setup_photograph.png", png_bytes(), "image/png")},
        data={"category": "test_setup_photograph", "test_instance_id": test["id"], "auto_classify": "false"},
        headers=tokens[ENGINEER],
    )
    assert superseded.status_code == 409
    assert "superseded" in superseded.json()["detail"]
    assert replacement["superseded_at"] is None


# --------------------------------------------------------- mandatory evidence ---

def test_a_procedure_that_requires_evidence_cannot_be_completed_without_it(
    client, tokens, case_factory, observations, db
):
    case_id = case_factory(evidence=True)["id"]
    test = _test_by_code(client, tokens, case_id, "T-WP")
    stored = client.put(
        f"{API}/tests/{test['id']}/observations",
        json={"observations": observations["T-WP"], "replace": True},
        headers=tokens[ENGINEER],
    )
    assert stored.status_code == 200, stored.text
    assert client.post(
        f"{API}/tests/{test['id']}/calculate", headers=tokens[ENGINEER]
    ).status_code == 200

    definition = _require_evidence(db, "T-WP", required=True)
    try:
        refused = client.patch(
            f"{API}/tests/{test['id']}", json={"mark_complete": True}, headers=tokens[ENGINEER]
        )
        assert refused.status_code == 409
        assert "requires evidence" in refused.json()["detail"]

        # The case says the same thing before the submit button is pressed.
        readiness = client.get(
            f"{API}/cases/{case_id}/readiness", headers=tokens[ENGINEER]
        ).json()
        evidence_blockers = [
            item for item in readiness["blocking"] if item["kind"] == "evidence"
        ]
        assert any(item["code"] == "T-WP" for item in evidence_blockers)
        assert readiness["evidence"]["missing_per_test"][0]["test_code"] == "T-WP"

        attachment = _upload(client, tokens, case_id)
        assert _link(client, tokens, attachment["id"], test["id"]).status_code == 201

        readiness = client.get(
            f"{API}/cases/{case_id}/readiness", headers=tokens[ENGINEER]
        ).json()
        assert readiness["evidence"]["missing_per_test"] == []
        assert not [
            item for item in readiness["blocking"]
            if item["kind"] == "evidence" and item["code"] == "T-WP"
        ]

        completed = client.patch(
            f"{API}/tests/{test['id']}", json={"mark_complete": True}, headers=tokens[ENGINEER]
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "COMPLETED"
    finally:
        _require_evidence(db, "T-WP", required=False)


def test_the_report_records_which_test_each_piece_of_evidence_supports(
    client, tokens, case_factory, db
):
    case_id = case_factory()["id"]
    test = _test_by_code(client, tokens, case_id, "T-WP")
    attachment = _upload(client, tokens, case_id)
    assert _link(client, tokens, attachment["id"], test["id"]).status_code == 201

    from app.models import EvaluationCase

    stored = db.get(EvaluationCase, uuid.UUID(case_id))
    snapshot, _digest = build_report_snapshot(db, case=stored, report_no="RPT-EVIDENCE-1")
    # Every fixture PNG has the same bytes, so the caption identifies the one
    # this test uploaded.
    linked = [item for item in snapshot["evidence"] if item["caption"] == attachment["caption"]]
    assert linked and linked[0]["tests"] == ["T-WP"]

    conditions = client.post(
        f"{API}/cases/{case_id}/conditions",
        json={"test_instance_id": test["id"], "temperature_c": "21"},
        headers=tokens[ENGINEER],
    )
    assert conditions.status_code == 201
    # A fresh request gets its own session; expire the identity map so the
    # second snapshot sees the condition the API just committed.
    db.expire_all()
    snapshot, _digest = build_report_snapshot(db, case=stored, report_no="RPT-EVIDENCE-2")
    assert snapshot["conditions"][0]["test_code"] == "T-WP"
