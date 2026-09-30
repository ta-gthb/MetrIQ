"""Observation entry, calculation, compliance and override tests (FR-08, PRD 11)."""

from __future__ import annotations

from decimal import Decimal

from app.security.permissions import ENGINEER

API = "/api/v1"

MVP_TEST_CODES = {
    "T-WP", "T-REP", "T-ECC", "T-ZR", "T-CREEP", "T-TEMP-NL",
    "T-SENS", "T-DISC", "T-STAB", "T-CHK-CON", "T-CHK-ID",
}


def case_detail(client, tokens, case_id: str) -> dict:
    response = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER])
    assert response.status_code == 200, response.text
    return response.json()


def test_the_test_plan_is_generated_when_a_case_is_created(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    assert case["status"] == "DRAFT"
    codes = {test["test_code"] for test in case["tests"]}
    assert codes == MVP_TEST_CODES
    assert case["test_plan"]
    assert len(case["test_plan"]) == len(MVP_TEST_CODES)
    assert all(test["applicability_status"] == "APPLICABLE" for test in case["tests"])
    # Every planned test carries its rule provenance for the UI.
    assert all(test["name"] and test["clause_reference"] for test in case["tests"])


def test_the_test_plan_marks_phase_two_tests_inactive():
    from app.rules.loader import load_test_catalogue

    catalogue = load_test_catalogue("r76-1-2006-v1")
    phase_two = {entry["test_code"] for entry in catalogue["phase2_tests"]}
    assert phase_two
    assert phase_two.isdisjoint(MVP_TEST_CODES)


def test_observations_are_saved_and_move_the_test_into_progress(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-WP")
    assert target["status"] == "NOT_STARTED"

    response = client.put(
        f"{API}/tests/{target['id']}/observations",
        json={
            "replace": True,
            "observations": [
                {"observation_no": 1, "position_label": "Zero", "load": "0",
                 "indication": "0", "additional_load": "5"},
                {"observation_no": 2, "position_label": "10 kg", "load": "10000",
                 "indication": "10002", "additional_load": "5"},
            ],
        },
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["observations"]) == 2
    assert body["status"] == "IN_PROGRESS"
    assert body["compliance_result"] is None  # autosave never decides compliance


def test_observations_are_replaced_rather_than_appended(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-WP")
    payload = {
        "replace": True,
        "observations": [
            {"observation_no": 1, "load": "0", "indication": "0", "additional_load": "5"},
            {"observation_no": 2, "load": "10000", "indication": "10002", "additional_load": "5"},
        ],
    }
    client.put(f"{API}/tests/{target['id']}/observations", json=payload, headers=tokens[ENGINEER])
    payload["observations"] = payload["observations"][:1]
    response = client.put(
        f"{API}/tests/{target['id']}/observations", json=payload, headers=tokens[ENGINEER]
    )
    assert len(response.json()["observations"]) == 1


def test_validate_computes_without_persisting_a_result(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-WP")
    client.put(
        f"{API}/tests/{target['id']}/observations",
        json={
            "replace": True,
            "observations": [
                {"observation_no": 1, "load": "0", "indication": "0", "additional_load": "5"},
                {"observation_no": 2, "load": "10000", "indication": "10002", "additional_load": "5"},
            ],
        },
        headers=tokens[ENGINEER],
    )

    preview = client.post(f"{API}/tests/{target['id']}/validate", headers=tokens[ENGINEER])
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["status"] == "PASS"
    # Metrological values cross the JSON boundary as strings so they round-trip
    # without binary floating point loss (PRD 11.5).
    assert body["measured_value"] == "2"
    assert body["limit_value"] == "10"
    assert body["margin"] == "8"
    assert body["rule_id"].startswith("R76-MPE-III")
    assert body["rounding_policy"] == "EXACT-NO-ROUNDING"

    # Nothing was written: the stored test is still unresolved.
    stored = client.get(f"{API}/tests/{target['id']}", headers=tokens[ENGINEER]).json()
    assert stored["compliance_result"] is None
    assert stored["result_status"] == "PENDING"
    assert stored["latest_calculation"] is None


def test_calculate_persists_the_result_and_the_explanation(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-WP")
    client.put(
        f"{API}/tests/{target['id']}/observations",
        json={
            "replace": True,
            "observations": [
                {"observation_no": 1, "load": "0", "indication": "0", "additional_load": "5"},
                {"observation_no": 2, "load": "10000", "indication": "10002", "additional_load": "5"},
            ],
        },
        headers=tokens[ENGINEER],
    )

    response = client.post(f"{API}/tests/{target['id']}/calculate", headers=tokens[ENGINEER])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PASS"

    stored = client.get(f"{API}/tests/{target['id']}", headers=tokens[ENGINEER]).json()
    assert stored["result_status"] == "PASS"
    # Calculating records the result; completion is a separate, explicit act.
    assert stored["status"] == "IN_PROGRESS"
    assert stored["compliance_result"]["status"] == "PASS"
    assert stored["compliance_result"]["explanation"]
    run = stored["latest_calculation"]
    assert run["engine_version"]
    assert run["rule_version_label"] == "r76-1-2006-v1"
    # The stored intermediates let the result be re-explained later (PRD 11.5).
    assert run["intermediates"]["method"].startswith("P = I + 0.5e")
    assert run["input_snapshot"]["test_code"] == "T-WP"

    completed = client.patch(
        f"{API}/tests/{target['id']}", json={"mark_complete": True}, headers=tokens[ENGINEER]
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["result_status"] == "PASS"
    assert completed.json()["completed_at"]


def test_a_failing_result_is_recorded_as_fail_not_a_user_choice(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-WP")
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
    response = client.post(f"{API}/tests/{target['id']}/calculate", headers=tokens[ENGINEER])
    assert response.json()["status"] == "FAIL"
    assert Decimal(response.json()["margin"]) < 0
    assert client.get(f"{API}/tests/{target['id']}", headers=tokens[ENGINEER]).json()[
        "result_status"
    ] == "FAIL"


def test_missing_required_input_yields_incomplete(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-WP")
    client.put(
        f"{API}/tests/{target['id']}/observations",
        json={"replace": True, "observations": [{"observation_no": 1, "load": "10000"}]},
        headers=tokens[ENGINEER],
    )
    body = client.post(f"{API}/tests/{target['id']}/calculate", headers=tokens[ENGINEER]).json()
    assert body["is_valid"] is False
    assert body["status"] == "INCOMPLETE"
    assert body["errors"]


def test_negative_input_is_rejected_as_incomplete(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-WP")
    client.put(
        f"{API}/tests/{target['id']}/observations",
        json={"replace": True,
              "observations": [{"observation_no": 1, "load": "-1", "indication": "0"}]},
        headers=tokens[ENGINEER],
    )
    body = client.post(f"{API}/tests/{target['id']}/calculate", headers=tokens[ENGINEER]).json()
    assert body["status"] == "INCOMPLETE"
    assert any("negative" in problem for problem in body["errors"])


def test_the_engine_rejects_an_unsupported_test_code(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-WP")
    response = client.patch(
        f"{API}/tests/{target['id']}",
        json={"applicability_status": "NOT_APPLICABLE",
              "applicability_reason": "superseded by a range-specific test"},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200
    assert response.json()["result_status"] == "NOT_APPLICABLE"


def test_marking_not_applicable_requires_a_justification(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-SENS")
    response = client.patch(
        f"{API}/tests/{target['id']}",
        json={"applicability_status": "NOT_APPLICABLE"},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 422
    assert "justification" in response.json()["detail"]


def test_a_not_applicable_test_refuses_observations(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-DISC")
    client.patch(
        f"{API}/tests/{target['id']}",
        json={"applicability_status": "NOT_APPLICABLE",
              "applicability_reason": "instrument has no electronic discrimination device"},
        headers=tokens[ENGINEER],
    )
    response = client.put(
        f"{API}/tests/{target['id']}/observations",
        json={"replace": True, "observations": [{"observation_no": 1, "value": "10"}]},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 409


def test_pass_and_fail_cannot_be_typed_in_as_an_override(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-WP")
    for status in ("PASS", "FAIL"):
        response = client.post(
            f"{API}/tests/{target['id']}/override",
            json={"status": status,
                  "reason": "Attempting to type a compliance outcome directly."},
            headers=tokens[ENGINEER],
        )
        assert response.status_code == 403, status
        assert "deterministic compliance engine" in response.json()["detail"]


def test_an_override_requires_a_substantive_reason(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-WP")
    response = client.post(
        f"{API}/tests/{target['id']}/override",
        json={"status": "WAIVED", "reason": "too short"},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 422


def test_an_approved_waiver_is_recorded_and_recoverable(client, tokens, new_case):
    case = case_detail(client, tokens, new_case()["id"])
    target = next(test for test in case["tests"] if test["test_code"] == "T-WP")
    client.put(
        f"{API}/tests/{target['id']}/observations",
        json={"replace": True,
              "observations": [
                  {"observation_no": 1, "position_label": "Zero", "load": "0",
                   "indication": "0", "additional_load": "5"},
                  {"observation_no": 2, "load": "10000", "indication": "10020",
                   "additional_load": "5"},
              ]},
        headers=tokens[ENGINEER],
    )
    automated = client.post(f"{API}/tests/{target['id']}/calculate", headers=tokens[ENGINEER]).json()
    assert automated["status"] == "FAIL"

    response = client.post(
        f"{API}/tests/{target['id']}/override",
        json={"status": "WAIVED",
              "reason": "Customer supplied a calibration certificate covering this load point."},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text
    assert response.json()["previous_status"] == "FAIL"
    assert response.json()["automated_result_recoverable"] is True

    stored = client.get(f"{API}/tests/{target['id']}", headers=tokens[ENGINEER]).json()
    assert stored["result_status"] == "WAIVED"
    assert stored["is_waived"] is True
    # The automated run is still in the history, so the waiver is auditable.
    assert stored["latest_calculation"]["outputs"]["status"] == "FAIL"


def test_engine_capabilities_list_every_mvp_test_code(client, tokens):
    body = client.get(f"{API}/calculations/engine", headers=tokens[ENGINEER]).json()
    assert body["deterministic"] is True
    assert body["uses_ai"] is False
    assert MVP_TEST_CODES.issubset(set(body["supported_test_codes"]))
    assert "abs_lte" in body["comparators"]


def test_the_mpe_endpoint_defaults_to_the_active_ruleset(client, tokens):
    response = client.post(
        f"{API}/calculations/mpe",
        json={"instrument_class": "III", "load": "5000", "e": "10"},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mpe_value"] == "5"
    assert body["rule_version"] == "r76-1-2006-v1"


def test_the_mpe_endpoint_applies_the_band_boundary(client, tokens):
    at_boundary = client.post(
        f"{API}/calculations/mpe",
        json={"instrument_class": "III", "load": "5000", "e": "10"},
        headers=tokens[ENGINEER],
    ).json()
    over_boundary = client.post(
        f"{API}/calculations/mpe",
        json={"instrument_class": "III", "load": "5001", "e": "10"},
        headers=tokens[ENGINEER],
    ).json()
    assert at_boundary["mpe_value"] == "5" and at_boundary["factor"] == "0.5"
    assert over_boundary["mpe_value"] == "10" and over_boundary["factor"] == "1"


def test_the_preview_endpoint_runs_the_real_engine(client, tokens):
    response = client.post(
        f"{API}/calculations/preview",
        json={
            "test_code": "T-ZR",
            "instrument": {"instrument_class": "III", "e": "10", "d": "10", "unit": "g"},
            "observations": [{"value": "0"}, {"value": "6"}],
        },
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["calculation"]["status"] == "FAIL"
    assert body["compliance"]["status"] == "FAIL"
    assert body["calculation"]["limit_value"] == "5"
    assert body["ruleset_label"] == "r76-1-2006-v1"
