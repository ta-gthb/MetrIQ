"""Repository search, history and revision comparison (audit item 13).

The repository has to answer history questions, not only list what exists:
which evaluations used one instrument, what an earlier revision of a report
said, what changed between two revisions, and what happened to a test that was
re-tested. Every answer is scoped the same way the screens are.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

from app.security.permissions import APPROVER, ENGINEER, REVIEWER, SUPER_ADMIN
from tests.test_api_workflow import failing_view, run_lifecycle

API = "/api/v1"


def _generate(client, tokens, case_id: str) -> dict:
    response = client.post(
        f"{API}/cases/{case_id}/reports/generate",
        json={"formats": ["pdf"]},
        headers=tokens[APPROVER],
    )
    assert response.status_code == 201, response.text
    return response.json()


def _test_by_code(client, tokens, case_id: str, code: str) -> dict:
    detail = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()
    return next(item for item in detail["tests"] if item["test_code"] == code)


def _result_status(detail: dict, code: str) -> str | None:
    return next(
        (item["result_status"] for item in detail["tests"] if item["test_code"] == code), None
    )


# ---------------------------------------------------------------- the repository ---

def test_the_repository_filters_by_instrument_result_and_date(client, tokens, case_factory):
    passing = run_lifecycle(client, tokens, case_factory())
    run_lifecycle(client, tokens, case_factory())

    failing_case = case_factory()
    failing_view(client, tokens, failing_case["id"])
    _generate(client, tokens, failing_case["id"])

    instrument_id = passing["instrument"]["id"]
    by_instrument = client.get(
        f"{API}/reports", params={"instrument_id": instrument_id}, headers=tokens[APPROVER]
    ).json()
    assert [row["application_no"] for row in by_instrument["items"]] == [passing["application_no"]]
    assert by_instrument["items"][0]["instrument_serial_number"] == passing["instrument"]["serial_number"]

    failures = client.get(
        f"{API}/reports", params={"result": "FAIL"}, headers=tokens[APPROVER]
    ).json()
    assert {row["application_no"] for row in failures["items"]} == {failing_case["application_no"]}

    passes = client.get(
        f"{API}/reports", params={"result": "PASS"}, headers=tokens[APPROVER]
    ).json()
    assert passing["application_no"] in {row["application_no"] for row in passes["items"]}

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    none_yet = client.get(
        f"{API}/reports", params={"generated_from": tomorrow}, headers=tokens[APPROVER]
    ).json()
    assert none_yet["items"] == []

    everything = client.get(
        f"{API}/reports",
        params={"generated_to": tomorrow, "search": passing["application_no"]},
        headers=tokens[APPROVER],
    ).json()
    assert {row["application_no"] for row in everything["items"]} == {passing["application_no"]}
    assert everything["items"][0]["instrument_serial_number"] == passing["instrument"]["serial_number"]


def test_the_repository_exports_the_filtered_rows_as_csv(client, tokens, case_factory):
    finalized = run_lifecycle(client, tokens, case_factory())
    report = client.get(
        f"{API}/reports/{finalized['report']['id']}", headers=tokens[APPROVER]
    ).json()

    response = client.get(
        f"{API}/reports/export.csv",
        params={"search": report["report_no"]},
        headers=tokens[APPROVER],
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "metriq-repository.csv" in response.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert rows[0]["report_no"] == report["report_no"]
    assert rows[0]["application_no"] == finalized["application_no"]
    assert rows[0]["instrument_serial_number"] == finalized["instrument"]["serial_number"]
    assert rows[0]["overall_result"] == "PASS"
    assert rows[0]["is_immutable"] == "true"


# ------------------------------------------------------------------- history ---

def test_an_instrument_keeps_the_history_of_its_evaluations(client, tokens, case_factory):
    finalized = run_lifecycle(client, tokens, case_factory())
    instrument_id = finalized["instrument"]["id"]

    history = client.get(
        f"{API}/instruments/{instrument_id}/history", headers=tokens[APPROVER]
    ).json()
    assert history["instrument"]["id"] == instrument_id
    assert history["instrument"]["serial_number"] == finalized["instrument"]["serial_number"]

    entry = next(row for row in history["cases"] if row["case_id"] == finalized["id"])
    assert entry["application_no"] == finalized["application_no"]
    assert entry["report"]["report_no"] == finalized["report"]["report_no"]
    assert entry["report"]["is_immutable"] is True
    assert entry["report"]["overall_result"] == "PASS"


def test_a_retested_test_keeps_its_revision_chain(client, tokens, case_factory):
    case_id = case_factory()["id"]
    original = _test_by_code(client, tokens, case_id, "T-WP")
    replacement = client.post(
        f"{API}/tests/{original['id']}/retest",
        json={"reason": "Reference weights re-calibrated; repeat the test."},
        headers=tokens[ENGINEER],
    ).json()

    history = client.get(
        f"{API}/cases/{case_id}/tests/{original['id']}/history", headers=tokens[ENGINEER]
    ).json()
    assert history["test_code"] == "T-WP"
    assert history["revision_count"] == 2
    oldest, newest = history["revisions"]
    assert oldest["id"] == original["id"]
    assert oldest["live"] is False
    assert oldest["result_status"] == original["result_status"]
    assert oldest["superseded_at"] is not None
    assert newest["id"] == replacement["id"]
    assert newest["live"] is True
    assert newest["retest_reason"] == "Reference weights re-calibrated; repeat the test."

    # Asking through the replacement returns the same chain.
    again = client.get(
        f"{API}/cases/{case_id}/tests/{replacement['id']}/history", headers=tokens[ENGINEER]
    ).json()
    assert [row["id"] for row in again["revisions"]] == [row["id"] for row in history["revisions"]]


# ------------------------------------------------------- revision comparison ---

def test_an_earlier_report_revision_still_reads_its_own_snapshot(client, tokens, case_factory):
    case = case_factory()
    case_id = case["id"]
    first = _generate(client, tokens, case_id)
    before = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()

    failing_view(client, tokens, case_id)
    second = _generate(client, tokens, case_id)
    assert second["revision_no"] == 2
    after = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()
    assert _result_status(after, "T-WP") == "FAIL"
    assert _result_status(before, "T-WP") == "PASS"

    revision_one = client.get(
        f"{API}/reports/{first['id']}/revisions/1/snapshot", headers=tokens[APPROVER]
    ).json()
    old_test = next(item for item in revision_one["tests"] if item["test_code"] == "T-WP")
    assert old_test["result_status"] == "PASS"
    assert revision_one["meta"]["revision_no"] == 1

    comparison = client.get(
        f"{API}/reports/{first['id']}/compare",
        params={"left": 1, "right": 2},
        headers=tokens[APPROVER],
    ).json()
    assert comparison["changed_tests"] == ["T-WP"]
    fields = {(item["area"], item["code"], item["field"]) for item in comparison["changes"]}
    assert ("tests", "T-WP", "result_status") in fields
    assert comparison["before"]["revision_no"] == 1
    assert comparison["after"]["revision_no"] == 2

    same = client.get(
        f"{API}/reports/{first['id']}/compare",
        params={"left": 2, "right": 2},
        headers=tokens[APPROVER],
    )
    assert same.status_code == 422


def test_an_export_is_scoped_to_the_callers_cases(client, tokens, case_factory, accounts):
    """An engineer's export is scoped like the screen: their own cases only."""
    mine = case_factory()
    theirs = case_factory()
    # Only a registered engineer may hold a case as its engineer, so the case
    # is handed to a second engineer to move it outside the first one's list.
    replacement = client.post(
        f"{API}/users",
        json={
            "email": f"second.engineer.{uuid.uuid4().hex[:8]}@metriq.local",
            "full_name": "Second Engineer",
            "password": "Second@Engineer1",
            "role_code": ENGINEER,
            "designation": "Officer",
            "laboratory_id": accounts["laboratory_id"],
        },
        headers=tokens[SUPER_ADMIN],
    )
    assert replacement.status_code == 201, replacement.text
    moved = client.post(
        f"{API}/cases/{theirs['id']}/assignments",
        json={
            "engineer_id": replacement.json()["id"],
            "reason": "Reassigned so the scope check has a case outside the engineer's list.",
        },
        headers=tokens[SUPER_ADMIN],
    )
    assert moved.status_code == 200, moved.text
    _generate(client, tokens, mine["id"])
    _generate(client, tokens, theirs["id"])

    def export(headers) -> list[dict]:
        response = client.get(f"{API}/reports/export.csv", headers=headers)
        assert response.status_code == 200, response.text
        return list(csv.DictReader(io.StringIO(response.text)))

    engineer_ids = {row["case_id"] for row in export(tokens[ENGINEER])}
    assert mine["id"] in engineer_ids
    assert theirs["id"] not in engineer_ids

    admin_ids = {row["case_id"] for row in export(tokens[SUPER_ADMIN])}
    assert admin_ids >= engineer_ids
    assert theirs["id"] in admin_ids
