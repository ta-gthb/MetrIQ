"""Workflow, report and immutability tests (FR-05, PRD 18.1, 24)."""

from __future__ import annotations

import hashlib
import uuid

from app.security.permissions import APPROVER, ENGINEER, REVIEWER, SUPER_ADMIN

API = "/api/v1"


def run_lifecycle(client, tokens, case: dict) -> dict:
    """Drive a fully recorded case through submit -> verify -> approve -> finalize."""
    case_id = case["id"]

    submitted = client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens[ENGINEER])
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "TESTING_COMPLETED"

    verified = client.post(f"{API}/cases/{case_id}/verify", json={}, headers=tokens[REVIEWER])
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "VERIFIED"

    approved = client.post(
        f"{API}/cases/{case_id}/approve",
        json={"reason": "Reviewed and recommended for release."},
        headers=tokens[APPROVER],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"

    finalized = client.post(
        f"{API}/cases/{case_id}/finalize",
        json={"reason": "Type evaluation complete; report released."},
        headers=tokens[APPROVER],
    )
    assert finalized.status_code == 200, finalized.text
    return finalized.json()


def failing_view(client, tokens, case_id: str) -> None:
    """Re-record the weighing performance test so that it fails."""
    detail = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()
    target = next(test for test in detail["tests"] if test["test_code"] == "T-WP")
    client.put(
        f"{API}/tests/{target['id']}/observations",
        json={
            "replace": True,
            "observations": [
                {"observation_no": 1, "position_label": "Zero", "load": "0",
                 "indication": "0", "additional_load": "5"},
                {"observation_no": 2, "load": "10000", "indication": "10020",
                 "additional_load": "5"},
            ],
        },
        headers=tokens[ENGINEER],
    )
    result = client.post(f"{API}/tests/{target['id']}/calculate", headers=tokens[ENGINEER]).json()
    assert result["status"] == "FAIL", result


def test_full_lifecycle_produces_a_locked_report(client, tokens, case_factory):
    payload = run_lifecycle(client, tokens, case_factory())
    assert payload["status"] == "FINALIZED"
    assert payload["finalized_at"]

    report = payload["report"]
    assert report["report_no"].startswith("RPT-")
    assert report["revision_no"] == 1
    assert set(report["formats"]) == {"pdf", "docx"}
    assert len(report["content_hash"]) == 64
    assert report["verification_code"]


def test_report_artefacts_are_written_to_the_object_store(client, tokens, case_factory):
    """Deployed instances must not depend on local disk (Render is ephemeral).

    The bytes live behind the configured storage backend - Supabase Storage in a
    deployment - under the reserved ``reports/`` prefix, never an absolute path.
    """
    from app.database import SessionLocal
    from app.models import GeneratedReport, ReportRevision
    from app.services.attachment_service.storage import get_storage
    from app.services.report_engine.service import ARTEFACT_PREFIX
    from sqlalchemy import select

    payload = run_lifecycle(client, tokens, case_factory())
    storage = get_storage()
    report_id = uuid.UUID(payload["report"]["id"])

    with SessionLocal() as db:
        report = db.execute(
            select(GeneratedReport).where(GeneratedReport.id == report_id)
        ).scalars().one()
        revisions = db.execute(
            select(ReportRevision).where(ReportRevision.report_id == report.id)
        ).scalars().all()

    assert {row.format for row in revisions} == {"pdf", "docx"}
    for row in revisions:
        assert row.storage_key.startswith(f"{ARTEFACT_PREFIX}/"), row.storage_key
        assert row.storage_key == f"{ARTEFACT_PREFIX}/{report.report_no}-R{row.revision_no}.{row.format}"
        assert storage.exists(row.storage_key), row.storage_key
        assert storage.read(row.storage_key)


def test_the_object_store_overwrites_a_deterministic_key(monkeypatch):
    """Writing the same report key twice must overwrite, never fail.

    Report keys are derived from the report number, revision and format, so the
    local and Supabase backends have to agree on what a repeated write means.
    With upsert disabled, one artefact left in the bucket by a failed commit
    turns every later generation of that report into a permanent 500.
    """
    import httpx

    from app.services.attachment_service.storage import SupabaseStorageBackend

    captured: dict = {}

    class FakeResponse:
        status_code = 200
        text = ""

    def fake_post(url, content=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    backend = SupabaseStorageBackend(
        url="https://example.supabase.co", service_key="service-key", bucket="metriq-evidence"
    )
    key = "reports/RPT-2026-00001-R1.pdf"
    backend.save(key, b"%PDF-1.4", "application/pdf")

    assert captured["headers"]["x-upsert"] == "true"
    assert captured["url"].endswith(f"/storage/v1/object/metriq-evidence/{key}")


def test_a_missing_report_artefact_reports_410_not_500(client, tokens, case_factory):
    payload = run_lifecycle(client, tokens, case_factory())
    report_id = uuid.UUID(payload["report"]["id"])

    # Simulate an artefact that has been purged from the bucket.
    from app.database import SessionLocal
    from app.models import ReportRevision
    from sqlalchemy import select

    with SessionLocal() as db:
        for row in db.execute(
            select(ReportRevision).where(ReportRevision.report_id == report_id)
        ).scalars().all():
            row.storage_key = "reports/does-not-exist.pdf"
        db.commit()

    response = client.get(
        f"{API}/reports/{report_id}/download", params={"fmt": "pdf"}, headers=tokens[APPROVER]
    )
    assert response.status_code == 410, response.text


def test_a_finalized_case_cannot_be_edited(client, tokens, case_factory):
    payload = run_lifecycle(client, tokens, case_factory())
    case_id = payload["id"]
    detail = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()
    target = next(test for test in detail["tests"] if test["test_code"] == "T-WP")

    response = client.put(
        f"{API}/tests/{target['id']}/observations",
        json={"replace": True, "observations": [{"observation_no": 1, "load": "1",
                                                 "indication": "1"}]},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 409

    response = client.patch(
        f"{API}/cases/{case_id}", json={"title": "Renamed after release"}, headers=tokens[SUPER_ADMIN]
    )
    assert response.status_code == 409


def test_a_finalized_case_cannot_be_reassigned(client, tokens, case_factory):
    case = case_factory()
    run_lifecycle(client, tokens, case)
    response = client.post(
        f"{API}/cases/{case['id']}/assignments",
        json={"reviewer_id": case["_fixture_ids"]["reviewer"]},
        headers=tokens[SUPER_ADMIN],
    )
    assert response.status_code == 409


def test_submission_is_blocked_while_a_test_is_unresolved(client, tokens, new_case):
    case = new_case()
    response = client.post(
        f"{API}/cases/{case['id']}/submit", json={}, headers=tokens[ENGINEER]
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"].startswith("The case cannot be submitted")
    assert len(detail["blocking_tests"]) == 11


def test_submission_is_blocked_when_any_test_fails(client, tokens, case_factory, fill_case):
    case = case_factory()
    failing_view(client, tokens, case["id"])
    response = client.post(
        f"{API}/cases/{case['id']}/submit", json={}, headers=tokens[ENGINEER]
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["failed_tests"] == ["T-WP"]
    assert "failed type evaluation" in detail["message"]


def test_a_waiver_unblocks_submission(client, tokens, case_factory):
    case_id = case_factory()["id"]
    failing_view(client, tokens, case_id)
    detail = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()
    target = next(test for test in detail["tests"] if test["test_code"] == "T-WP")
    waived = client.post(
        f"{API}/tests/{target['id']}/override",
        json={"status": "WAIVED",
              "reason": "Manufacturer supplied a traceable certificate covering this load point."},
        headers=tokens[ENGINEER],
    )
    assert waived.status_code == 200, waived.text

    submitted = client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens[ENGINEER])
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "TESTING_COMPLETED"


def test_submission_is_blocked_without_the_two_mandatory_photographs(client, tokens, case_factory):
    """A new evaluation needs the nameplate and test-setup images (PRD 19.3)."""
    case = case_factory(evidence=False)
    response = client.post(
        f"{API}/cases/{case['id']}/submit", json={}, headers=tokens[ENGINEER]
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["missing_evidence"] == ["nameplate_photograph", "test_setup_photograph"]
    assert "two clear photographs" in detail["message"]
    assert detail["evidence_requirements"]["satisfied"] is False


def test_one_photograph_is_not_enough(client, tokens, case_factory, attach_evidence):
    case = case_factory(evidence=False)
    attach_evidence(case["id"], categories=("nameplate_photograph",))

    response = client.post(
        f"{API}/cases/{case['id']}/submit", json={}, headers=tokens[ENGINEER]
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["missing_evidence"] == ["test_setup_photograph"]

    attach_evidence(case["id"], categories=("test_setup_photograph",))
    accepted = client.post(
        f"{API}/cases/{case['id']}/submit", json={}, headers=tokens[ENGINEER]
    )
    assert accepted.status_code == 200, accepted.text


def test_a_non_image_does_not_satisfy_the_photograph_requirement(client, tokens, case_factory):
    case = case_factory(evidence=False)
    for category in ("nameplate_photograph", "test_setup_photograph"):
        uploaded = client.post(
            f"{API}/cases/{case['id']}/attachments",
            files={"file": (f"{category}.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
            data={"category": category, "auto_classify": "false"},
            headers=tokens[ENGINEER],
        )
        assert uploaded.status_code == 201, uploaded.text

    response = client.post(
        f"{API}/cases/{case['id']}/submit", json={}, headers=tokens[ENGINEER]
    )
    assert response.status_code == 422, response.text
    assert set(response.json()["detail"]["missing_evidence"]) == {
        "nameplate_photograph", "test_setup_photograph"
    }


def test_evidence_requirements_endpoint_reports_progress(client, tokens, case_factory, attach_evidence):
    case = case_factory(evidence=False)
    empty = client.get(
        f"{API}/cases/{case['id']}/evidence-requirements", headers=tokens[ENGINEER]
    )
    assert empty.status_code == 200, empty.text
    assert empty.json() == {
        "required": ["nameplate_photograph", "test_setup_photograph"],
        "present": [],
        "missing": ["nameplate_photograph", "test_setup_photograph"],
        "satisfied": False,
        "per_test": [],
        "missing_per_test": [],
    }

    attach_evidence(case["id"], categories=("test_setup_photograph",))
    partial = client.get(
        f"{API}/cases/{case['id']}/evidence-requirements", headers=tokens[ENGINEER]
    ).json()
    assert partial["present"] == ["test_setup_photograph"]
    assert partial["missing"] == ["nameplate_photograph"]
    assert partial["satisfied"] is False


def test_correction_request_returns_the_case_to_the_engineer(client, tokens, case_factory):
    case_id = case_factory()["id"]
    client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens[ENGINEER])

    requested = client.post(
        f"{API}/cases/{case_id}/request-correction",
        json={"reason": "The zero-return evidence photograph is missing."},
        headers=tokens[REVIEWER],
    )
    assert requested.status_code == 200, requested.text
    body = requested.json()
    assert body["status"] == "CORRECTION_REQUIRED"
    assert body["revision_no"] == 2
    assert "zero-return" in body["last_correction_reason"]

    # The engineer can edit again once a correction has been requested.
    detail = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()
    target = next(test for test in detail["tests"] if test["test_code"] == "T-ZR")
    response = client.put(
        f"{API}/tests/{target['id']}/observations",
        json={"replace": True,
              "observations": [{"observation_no": 1, "value": "0"},
                               {"observation_no": 2, "value": "1"}]},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text


def test_rejection_at_approval_requires_a_substantive_reason(client, tokens, case_factory):
    case_id = case_factory()["id"]
    client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens[ENGINEER])
    client.post(f"{API}/cases/{case_id}/verify", json={}, headers=tokens[REVIEWER])

    too_short = client.post(
        f"{API}/cases/{case_id}/reject", json={"reason": "no"}, headers=tokens[APPROVER]
    )
    assert too_short.status_code == 422

    rejected = client.post(
        f"{API}/cases/{case_id}/reject",
        json={"reason": "Eccentricity evidence is inconsistent with the recorded positions."},
        headers=tokens[APPROVER],
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"


def test_an_invalid_transition_is_refused(client, tokens, case_factory):
    response = client.post(
        f"{API}/cases/{case_factory()['id']}/approve", json={}, headers=tokens[APPROVER]
    )
    assert response.status_code == 409
    assert "not available while the case is in status" in response.json()["detail"]


def test_a_report_cannot_be_generated_for_a_draft_case(client, tokens, new_case):
    draft = new_case()
    assert draft["status"] == "DRAFT"
    response = client.post(
        f"{API}/cases/{draft['id']}/reports/generate",
        json={"formats": ["pdf"]},
        headers=tokens[APPROVER],
    )
    assert response.status_code == 404


def test_reports_can_be_regenerated_as_a_new_revision(client, tokens, case_factory):
    finalized = run_lifecycle(client, tokens, case_factory())
    report_id = finalized["report"]["id"]
    case_id = finalized["id"]

    regenerated = client.post(
        f"{API}/cases/{case_id}/reports/generate",
        json={"formats": ["pdf", "docx"]},
        headers=tokens[APPROVER],
    )
    assert regenerated.status_code == 201, regenerated.text
    assert regenerated.json()["revision_no"] == 2
    # The report number is stable; only the revision advances.
    assert regenerated.json()["report_no"] == finalized["report"]["report_no"]

    revisions = client.get(f"{API}/reports/{report_id}/revisions", headers=tokens[APPROVER]).json()
    assert {row["revision_no"] for row in revisions} == {1, 2}
    assert {row["format"] for row in revisions} == {"pdf", "docx"}


def test_finalizing_twice_is_idempotent(client, tokens, case_factory):
    """A finalized revision is locked: re-finalizing must not mint a new revision
    or change the content hash and verification code (PRD 17.4)."""
    finalized = run_lifecycle(client, tokens, case_factory())
    report = finalized["report"]
    case_id = finalized["id"]

    again = client.post(f"{API}/cases/{case_id}/finalize", json={}, headers=tokens[APPROVER])
    assert again.status_code == 200, again.text
    assert again.json()["report"]["id"] == report["id"]
    assert again.json()["report"]["content_hash"] == report["content_hash"]
    assert again.json()["report"]["verification_code"] == report["verification_code"]

    revisions = client.get(f"{API}/reports/{report['id']}/revisions", headers=tokens[APPROVER]).json()
    assert {row["revision_no"] for row in revisions} == {1}


def test_the_downloaded_report_matches_its_recorded_hash(client, tokens, case_factory):
    finalized = run_lifecycle(client, tokens, case_factory())
    report_id = finalized["report"]["id"]

    digests: dict[str, str] = {}
    for fmt, expected_type in (
        ("pdf", "application/pdf"),
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ):
        response = client.get(
            f"{API}/reports/{report_id}/download",
            params={"fmt": fmt},
            headers=tokens[APPROVER],
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == expected_type
        assert response.headers["x-content-sha256"]
        assert len(response.content) > 1000
        # The served bytes are the artefact the platform recorded, not a re-render.
        digest = hashlib.sha256(response.content).hexdigest()
        assert digest == response.headers["x-content-sha256"]
        digests[fmt] = digest

    revision = client.get(
        f"{API}/reports/{report_id}/download",
        params={"fmt": "pdf", "revision": 1},
        headers=tokens[APPROVER],
    )
    assert revision.status_code == 200
    assert revision.headers["x-content-sha256"] == digests["pdf"]
    assert revision.headers["content-disposition"].endswith('.pdf"')


def test_every_workflow_step_is_recorded_in_the_audit_trail(client, tokens, case_factory):
    case = case_factory()
    run_lifecycle(client, tokens, case)
    case_id = case["id"]

    actions = client.get(f"{API}/cases/{case_id}/workflow-actions", headers=tokens[APPROVER]).json()
    sequence = [row["action"] for row in actions]
    for expected in ("CREATE", "ASSIGN", "SUBMIT", "VERIFY", "APPROVE", "FINALIZE"):
        assert expected in sequence, sequence
    assert sequence.index("SUBMIT") < sequence.index("VERIFY") < sequence.index("APPROVE")

    logs = client.get(f"{API}/cases/{case_id}/audit-logs", headers=tokens[APPROVER]).json()
    assert logs
    assert all(row["occurred_at"] for row in logs)


def test_the_report_snapshot_is_a_self_contained_record(client, tokens, case_factory):
    finalized = run_lifecycle(client, tokens, case_factory())
    report_id = finalized["report"]["id"]
    snapshot = client.get(f"{API}/reports/{report_id}/snapshot", headers=tokens[APPROVER]).json()

    assert snapshot["meta"]["report_no"] == finalized["report"]["report_no"]
    assert snapshot["meta"]["content_hash"] == finalized["report"]["content_hash"]
    assert snapshot["summary"]["overall"] in {"PASS", "FAIL", "INCOMPLETE"}
    assert snapshot["tests"]
    assert snapshot["instrument"]["model"] == "TEST-BENCH-30K"
