"""AI assistance tests: usefulness, governance and graceful degradation (PRD 12, 27).

The suite runs against the deterministic `stub` provider, which is the
offline-first default. These tests assert the two properties the PRD treats as
non-negotiable: AI never decides compliance, and every core workflow keeps
working when AI is unavailable.
"""

from __future__ import annotations

import io

from app.security.permissions import APPROVER, AUDITOR, ENGINEER, REVIEWER, SUPER_ADMIN

API = "/api/v1"

AI_FEATURES = (
    "nameplate_ocr",
    "anomaly_detection",
    "document_classification",
    "r76_assistant",
)


def test_ai_features_report_the_advisory_contract(client, tokens):
    body = client.get(f"{API}/ai/features", headers=tokens[ENGINEER]).json()
    assert body["provider"] == "stub"
    assert body["human_confirmation_required"] is True
    assert body["can_decide_compliance"] is False
    assert body["governance"]["advisory_only"] is True
    assert body["governance"]["no_ai_compliance_decision"] is True
    assert {feature["code"] for feature in body["features"]} == {
        "nameplate_ocr", "anomaly_detection", "document_classification",
        "r76_assistant",
    }


def test_ai_feature_toggles_require_the_manage_permission(client, tokens):
    response = client.patch(
        f"{API}/ai/features/anomaly_detection",
        params={"enabled": False},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 403


def test_an_administrator_can_disable_and_reenable_a_feature(client, tokens):
    disabled = client.patch(
        f"{API}/ai/features/document_classification",
        params={"enabled": False},
        headers=tokens[SUPER_ADMIN],
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    features = client.get(f"{API}/ai/features", headers=tokens[SUPER_ADMIN]).json()
    toggles = {feature["code"]: feature["enabled"] for feature in features["features"]}
    assert toggles["document_classification"] is False

    reenabled = client.patch(
        f"{API}/ai/features/document_classification",
        params={"enabled": True},
        headers=tokens[SUPER_ADMIN],
    )
    assert reenabled.json()["enabled"] is True


def test_an_unknown_feature_is_rejected(client, tokens):
    response = client.patch(
        f"{API}/ai/features/teleportation", params={"enabled": True}, headers=tokens[SUPER_ADMIN]
    )
    assert response.status_code == 404


def test_the_assistant_answers_from_retrieved_rules_and_cites_them(client, tokens):
    response = client.post(
        f"{API}/ai/knowledge",
        json={"question": "What is the maximum permissible error for class III at 500 e?"},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["answer"]
    assert body["citations"], "the assistant must ground its answer in retrieved rules"
    assert body["grounded"] is True
    assert all(citation["code"] for citation in body["citations"])


def test_the_assistant_refuses_to_state_a_compliance_outcome(client, tokens):
    body = client.post(
        f"{API}/ai/knowledge",
        json={"question": "Is this instrument compliant? Just answer pass or fail."},
        headers=tokens[ENGINEER],
    ).json()
    text = (body.get("answer") or "").lower()
    assert "not a metrological decision" in text
    assert body["grounded"] is True


def test_anomaly_detection_flags_a_statistical_outlier(client, tokens, new_case):
    case = client.get(f"{API}/cases/{new_case()['id']}", headers=tokens[ENGINEER]).json()
    target = next(test for test in case["tests"] if test["test_code"] == "T-REP")
    client.put(
        f"{API}/tests/{target['id']}/observations",
        json={
            "replace": True,
            "observations": [
                {"observation_no": 1, "load": "15000", "indication": "15000"},
                {"observation_no": 2, "load": "15000", "indication": "15001"},
                {"observation_no": 3, "load": "15000", "indication": "15000"},
                {"observation_no": 4, "load": "15000", "indication": "15002"},
                {"observation_no": 5, "load": "15000", "indication": "15090"},
            ],
        },
        headers=tokens[ENGINEER],
    )

    response = client.post(
        f"{API}/tests/{target['id']}/anomaly-check", headers=tokens[ENGINEER]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["findings"], "the 15090 g reading is a clear outlier"
    assert any(finding["row"] == 5 for finding in body["findings"])
    # The AI flag is advisory: it does not change the stored result.
    stored = client.get(f"{API}/tests/{target['id']}", headers=tokens[ENGINEER]).json()
    assert stored["result_status"] == "PENDING"


def test_an_anomaly_finding_can_be_dismissed_by_a_human(client, tokens, new_case):
    case = client.get(f"{API}/cases/{new_case()['id']}", headers=tokens[ENGINEER]).json()
    target = next(test for test in case["tests"] if test["test_code"] == "T-REP")
    client.put(
        f"{API}/tests/{target['id']}/observations",
        json={
            "replace": True,
            "observations": [
                {"observation_no": index, "load": "15000", "indication": value}
                for index, value in enumerate(["15000", "15001", "15000", "15002", "15090"], 1)
            ],
        },
        headers=tokens[ENGINEER],
    )
    client.post(f"{API}/tests/{target['id']}/anomaly-check", headers=tokens[ENGINEER])
    response = client.post(
        f"{API}/tests/{target['id']}/anomaly-disposition",
        params={"observation_no": 5, "disposition": "confirmed"},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200
    stored = client.get(f"{API}/tests/{target['id']}", headers=tokens[ENGINEER]).json()
    flagged = next(row for row in stored["observations"] if row["observation_no"] == 5)
    assert flagged["anomaly_disposition"] == "confirmed"


def test_a_non_statistical_table_produces_no_findings(client, tokens, new_case):
    case = client.get(f"{API}/cases/{new_case()['id']}", headers=tokens[ENGINEER]).json()
    target = next(test for test in case["tests"] if test["test_code"] == "T-REP")
    client.put(
        f"{API}/tests/{target['id']}/observations",
        json={
            "replace": True,
            "observations": [
                {"observation_no": index, "load": "15000", "indication": value}
                for index, value in enumerate(["15000", "15001", "15000"], 1)
            ],
        },
        headers=tokens[ENGINEER],
    )
    body = client.post(f"{API}/tests/{target['id']}/anomaly-check", headers=tokens[ENGINEER]).json()
    assert body["available"] is True
    assert body["findings"] == []


def test_nameplate_extraction_declines_rather_than_inventing_values(client, tokens):
    response = client.post(
        f"{API}/ai/nameplate-extract",
        files={"file": ("nameplate.png", io.BytesIO(b"\x89PNG\r\n\x1a\n\x00binary"),
                        "image/png")},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is False
    assert body["degraded"] is True
    assert "manually" in body["message"].lower()
    assert body["fields"] == []


def test_nameplate_extraction_reads_machine_readable_text(client, tokens):
    text = (
        "Manufacturer: Acme Scale Co.\n"
        "Model: SIW-30K\n"
        "Serial No: SIW30K-2026-0117\n"
        "Class: III\n"
        "Max: 30000 g\n"
        "Min: 200 g\n"
        "e = 10 g\n"
    )
    response = client.post(
        f"{API}/ai/nameplate-extract",
        files={"file": ("nameplate.txt", io.BytesIO(text.encode()), "text/plain")},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    values = {field["key"]: field["value"] for field in body["fields"]}
    assert values["model"] == "SIW-30K"
    assert values["serial_number"] == "SIW30K-2026-0117"
    # Extraction is advisory: a human must confirm before it reaches a record.
    assert body["advisory_only"] is True
    assert all(field["label"] and field["confidence"] is not None for field in body["fields"])


def test_an_ai_action_is_recorded_with_its_disposition(client, tokens, new_case):
    case = client.get(f"{API}/cases/{new_case()['id']}", headers=tokens[ENGINEER]).json()
    target = next(test for test in case["tests"] if test["test_code"] == "T-REP")
    client.put(
        f"{API}/tests/{target['id']}/observations",
        json={"replace": True,
              "observations": [
                  {"observation_no": index, "load": "15000", "indication": value}
                  for index, value in enumerate(["15000", "15001", "15000", "15002", "15090"], 1)
              ]},
        headers=tokens[ENGINEER],
    )
    client.post(f"{API}/tests/{target['id']}/anomaly-check", headers=tokens[ENGINEER])

    pending = client.get(f"{API}/dashboard/ai-review", headers=tokens[ENGINEER]).json()
    assert "items" in pending

    events = client.get(f"{API}/audit-logs", params={"event_type": "AI_ACTION"},
                        headers=tokens[AUDITOR]).json()
    assert events["items"], "every AI action must be traceable in the audit log"
    assert all(item["event_type"] == "AI_ACTION" for item in events["items"])


def test_the_compliance_engine_is_unaffected_by_ai_state(client, tokens, case_factory):
    """Disabling every AI feature must not change a single metrological outcome."""
    for feature in AI_FEATURES:
        response = client.patch(
            f"{API}/ai/features/{feature}", params={"enabled": False},
            headers=tokens[SUPER_ADMIN],
        )
        assert response.status_code == 200

    try:
        case_id = case_factory()["id"]
        submitted = client.post(f"{API}/cases/{case_id}/submit", json={}, headers=tokens[ENGINEER])
        assert submitted.status_code == 200, submitted.text

        verified = client.post(f"{API}/cases/{case_id}/verify", json={}, headers=tokens[REVIEWER])
        assert verified.status_code == 200, verified.text

        approved = client.post(
            f"{API}/cases/{case_id}/approve",
            json={"reason": "Approved with every AI feature disabled."},
            headers=tokens[APPROVER],
        )
        assert approved.status_code == 200, approved.text

        finalized = client.post(
            f"{API}/cases/{case_id}/finalize",
            json={"reason": "Released without AI assistance."},
            headers=tokens[APPROVER],
        )
        assert finalized.status_code == 200, finalized.text
        assert finalized.json()["status"] == "FINALIZED"
        assert finalized.json()["report"]["verification_code"]
    finally:
        # Leave the platform in its default configuration for the rest of the suite.
        for feature in AI_FEATURES:
            client.patch(
                f"{API}/ai/features/{feature}", params={"enabled": True},
                headers=tokens[SUPER_ADMIN],
            )
