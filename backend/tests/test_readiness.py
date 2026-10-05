"""Readiness, "why this result?" and the re-test workflow (audit item 16).

Three promises are held here: a case says what is missing before it is
submitted, a result can be explained from the record it came from, and a
corrected measurement supersedes the old one without destroying it.
"""

from __future__ import annotations

import uuid

import pytest

from app.database import SessionLocal
from app.models import AuditLog, EvaluationCase
from app.models import TestInstance as TestInstanceModel
from app.security.permissions import ENGINEER
from app.services.report_engine.snapshot import build_report_snapshot

API = "/api/v1"


def _case_detail(client, tokens, case_id: str) -> dict:
    response = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER])
    assert response.status_code == 200, response.text
    return response.json()


def _test_by_code(client, tokens, case_id: str, code: str) -> dict:
    """The live revision of a test, as the execution view sees it."""
    detail = _case_detail(client, tokens, case_id)
    return next(item for item in detail["tests"] if item["test_code"] == code)


def _test_record(client, tokens, test_id: str) -> dict:
    """One test record with its observations, as the execution view loads it."""
    response = client.get(f"{API}/tests/{test_id}", headers=tokens[ENGINEER])
    assert response.status_code == 200, response.text
    return response.json()


def _history_by_code(client, tokens, case_id: str, code: str) -> list[dict]:
    response = client.get(
        f"{API}/cases/{case_id}/tests",
        params={"include_superseded": "true"},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text
    return [item for item in response.json() if item["definition"]["test_code"] == code]


def _readiness(client, tokens, case_id: str) -> dict:
    response = client.get(f"{API}/cases/{case_id}/readiness", headers=tokens[ENGINEER])
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------- readiness ---

def test_a_fresh_case_reports_what_it_is_waiting_for(client, tokens, new_case):
    case_id = new_case()["id"]
    payload = _readiness(client, tokens, case_id)

    assert payload["ready_for_review"] is False
    assert payload["completion"]["tests_total"] > 0
    assert payload["completion"]["completed"] == 0
    assert payload["completion"]["percent"] == 0
    codes = {item["code"] for item in payload["blocking"]}
    assert {"T-WP", "T-REP"} <= codes
    assert all(item["kind"] == "status" for item in payload["blocking"] if item["code"])

    # The evidence gate is reported as a blocking item, not a surprise later.
    assert any(item["kind"] == "evidence" for item in payload["blocking"])

    # What each unresolved test still needs, worked out by a dry run.
    missing = {item["code"]: item for item in payload["missing_data"]}
    assert "T-WP" in missing
    assert missing["T-WP"]["hint"]


def test_a_recorded_case_is_reported_ready(client, tokens, case_factory):
    case_id = case_factory()["id"]
    payload = _readiness(client, tokens, case_id)

    assert payload["blocking"] == [], payload["blocking"]
    assert payload["ready_for_review"] is True
    assert payload["completion"]["percent"] == 100
    assert payload["overall_result"] == "PASS"
    assert payload["evidence"]["satisfied"] is True


def test_the_case_detail_hides_superseded_records(client, tokens, case_factory):
    case_id = case_factory()["id"]
    detail = _case_detail(client, tokens, case_id)

    assert detail["tests"], "the live revision of every test must be listed"
    assert all(item["superseded_at"] is None for item in detail["tests"])
    assert detail["superseded_tests"] == []


# ------------------------------------------------------------- explanation ---

def test_the_explanation_says_why_a_test_passed(client, tokens, case_factory):
    case_id = case_factory()["id"]
    test = _test_by_code(client, tokens, case_id, "T-WP")

    response = client.get(f"{API}/tests/{test['id']}/explanation", headers=tokens[ENGINEER])
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["result_status"] == "PASS"
    assert body["decision"]["status"] == "PASS"
    assert body["decision"]["rule_id"]
    assert body["decision"]["clause_reference"]
    assert body["why"], "the explanation must say something"
    assert any("governing" in sentence.lower() for sentence in body["why"])
    assert body["rows"], "the row table the decision was taken from must be shown"
    assert body["investigation"]
    assert body["retest"]["allowed"] is True
    assert body["retest"]["revision_no"] == 1
    assert len(body["revisions"]) == 1
    assert body["outstanding"] is None


def test_the_explanation_names_missing_inputs_before_a_calculation(client, tokens, new_case, observations):
    case_id = new_case()["id"]
    test = _test_by_code(client, tokens, case_id, "T-REP")
    # One repetition only: the spread of a series cannot be calculated from it.
    partial = observations["T-REP"][:1]
    stored = client.put(
        f"{API}/tests/{test['id']}/observations",
        json={"observations": partial, "replace": True},
        headers=tokens[ENGINEER],
    )
    assert stored.status_code == 200, stored.text

    body = client.get(f"{API}/tests/{test['id']}/explanation", headers=tokens[ENGINEER]).json()
    assert body["outstanding"] is not None
    assert body["outstanding"]["status"] in {"INCOMPLETE", "INVALID"}
    assert body["outstanding"]["errors"] or body["outstanding"]["explanation"]
    assert body["result_status"] == "PENDING"
    assert any("Calculate the test" in sentence for sentence in body["investigation"])


def test_procedures_outside_the_plan_are_named_with_their_reason(client, tokens, new_case):
    """A procedure that is defined but not run must be visible, with the reason.

    The plan covers active catalogue entries; disabled procedures remain visible
    with their inactive state instead of leaving a silent gap in evaluation scope.
    """
    case_id = new_case()["id"]
    payload = _readiness(client, tokens, case_id)

    outside = {item["code"]: item for item in payload["plan_scope"]["outside_plan"]}
    assert {"T-TILT", "T-VOLT"} <= set(outside)
    assert all(item["reason"] for item in outside.values())

    tilt = outside["T-TILT"]
    assert tilt["implementation_status"] == "implemented"
    assert tilt["reason"] == "Implemented, but disabled in this ruleset version."
    assert "proposed_limits" not in tilt
    in_plan = {item["code"] for item in payload["plan_scope"]["in_plan"]}
    assert "T-WP" in in_plan
    assert not ({"T-TILT", "T-VOLT"} & in_plan)


# ------------------------------------------------------------------ retest ---

def test_a_retest_supersedes_without_deleting(client, tokens, case_factory):
    case_id = case_factory()["id"]
    original = _test_record(client, tokens, _test_by_code(client, tokens, case_id, "T-WP")["id"])
    original_observations = len(original["observations"])
    assert original_observations

    response = client.post(
        f"{API}/tests/{original['id']}/retest",
        json={"reason": "Balance re-levelled and the test repeated from scratch."},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 201, response.text
    replacement = response.json()

    assert replacement["revision_no"] == 2
    assert replacement["supersedes_test_instance_id"] == original["id"]
    assert replacement["status"] == "NOT_STARTED"
    assert replacement["result_status"] == "PENDING"
    assert replacement["observations"] == []
    assert replacement["retest_reason"].startswith("Balance re-levelled")

    # The superseded record is intact: same rows, same result, now marked.
    superseded = client.get(f"{API}/tests/{original['id']}", headers=tokens[ENGINEER]).json()
    assert superseded["superseded_at"] is not None
    assert superseded["superseded_by_test_instance_id"] == replacement["id"]
    assert superseded["result_status"] == "PASS"
    assert len(superseded["observations"]) == original_observations

    # The execution view shows one T-WP; the history shows both on request.
    live = [item for item in _case_detail(client, tokens, case_id)["tests"] if item["test_code"] == "T-WP"]
    assert [item["id"] for item in live] == [replacement["id"]]
    history = _history_by_code(client, tokens, case_id, "T-WP")
    assert {item["revision_no"] for item in history} == {1, 2}

    detail = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()
    assert detail["test_counts"]["superseded"] == 1
    assert [item["id"] for item in detail["superseded_tests"]] == [original["id"]]

    db = SessionLocal()
    try:
        events = [
            row for row in db.query(AuditLog).filter(AuditLog.event_type == "RETEST").all()
            if row.entity_id == replacement["id"]
        ]
    finally:
        db.close()
    assert events, "starting a re-test must be recorded"
    assert events[0].reason.startswith("Balance re-levelled")


def test_a_retest_needs_a_reason(client, tokens, case_factory):
    case_id = case_factory()["id"]
    test = _test_by_code(client, tokens, case_id, "T-WP")
    response = client.post(
        f"{API}/tests/{test['id']}/retest", json={"reason": "no"}, headers=tokens[ENGINEER]
    )
    assert response.status_code == 422


def test_a_retest_is_refused_once_the_case_is_in_review(client, tokens, case_factory):
    case_id = case_factory()["id"]
    test = _test_by_code(client, tokens, case_id, "T-WP")
    submitted = client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens[ENGINEER])
    assert submitted.status_code == 200, submitted.text

    response = client.post(
        f"{API}/tests/{test['id']}/retest",
        json={"reason": "Trying to re-test after submission."},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 409
    assert "re-test" in response.json()["detail"].lower()


def test_a_superseded_record_cannot_be_superseded_again(client, tokens, case_factory):
    case_id = case_factory()["id"]
    original = _test_by_code(client, tokens, case_id, "T-WP")
    first = client.post(
        f"{API}/tests/{original['id']}/retest",
        json={"reason": "First re-test of this record."},
        headers=tokens[ENGINEER],
    )
    assert first.status_code == 201, first.text
    again = client.post(
        f"{API}/tests/{original['id']}/retest",
        json={"reason": "Second attempt on the same record."},
        headers=tokens[ENGINEER],
    )
    assert again.status_code == 409
    assert "already been superseded" in again.json()["detail"]


def test_the_replaced_record_is_recalculated_under_its_new_revision(
    client, tokens, case_factory, observations
):
    """A re-test is a working record: it takes observations, calculates, and the
    case can then be submitted even though the superseded one is still present."""
    case_id = case_factory()["id"]
    original = _test_by_code(client, tokens, case_id, "T-WP")
    replacement = client.post(
        f"{API}/tests/{original['id']}/retest",
        json={"reason": "Reference weights re-calibrated; repeat the test."},
        headers=tokens[ENGINEER],
    ).json()

    stored = client.put(
        f"{API}/tests/{replacement['id']}/observations",
        json={"observations": observations["T-WP"], "replace": True},
        headers=tokens[ENGINEER],
    )
    assert stored.status_code == 200, stored.text
    calculated = client.post(
        f"{API}/tests/{replacement['id']}/calculate", headers=tokens[ENGINEER]
    )
    assert calculated.status_code == 200, calculated.text
    assert calculated.json()["status"] == "PASS"
    completed = client.patch(
        f"{API}/tests/{replacement['id']}",
        json={"mark_complete": True},
        headers=tokens[ENGINEER],
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["revision_no"] == 2

    ready = _readiness(client, tokens, case_id)
    assert ready["completion"]["superseded"] == 1
    assert ready["ready_for_review"] is True, ready["blocking"]

    submitted = client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens[ENGINEER])
    assert submitted.status_code == 200, submitted.text


def test_the_report_shows_the_live_revision_of_a_re_tested_measurement(
    client, tokens, case_factory, observations
):
    case_id = case_factory()["id"]
    original = _test_by_code(client, tokens, case_id, "T-WP")
    replacement = client.post(
        f"{API}/tests/{original['id']}/retest",
        json={"reason": "Reference weights re-calibrated; repeat the test."},
        headers=tokens[ENGINEER],
    ).json()
    client.put(
        f"{API}/tests/{replacement['id']}/observations",
        json={"observations": observations["T-WP"], "replace": True},
        headers=tokens[ENGINEER],
    )
    client.post(f"{API}/tests/{replacement['id']}/calculate", headers=tokens[ENGINEER])

    db = SessionLocal()
    try:
        case = db.get(EvaluationCase, uuid.UUID(case_id))
        snapshot, _digest = build_report_snapshot(db, case=case, report_no="RPT-RETEST-1")
        stored = db.get(TestInstanceModel, uuid.UUID(replacement["id"]))
        assert stored.revision_no == 2
    finally:
        db.close()

    wp = [item for item in snapshot["tests"] if item["test_code"] == "T-WP"]
    assert len(wp) == 1, "only the live revision belongs in the report"
    assert wp[0]["revision_no"] == 2
    assert snapshot["summary"]["superseded"] == 1
    assert snapshot["meta"]["superseded_tests"] == 1
    assert [item["test_code"] for item in snapshot["superseded_tests"]] == ["T-WP"]