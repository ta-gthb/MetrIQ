"""Test-equipment traceability and calibration gates (audit item 11).

The audit asks for four things, and each has a test here:

* calibration date and expiry are part of the equipment register;
* the equipment used for a test is recorded against that test;
* equipment that is out of calibration, uncalibrated or withdrawn in the
  register cannot be attached to a case, and a test that names it cannot be
  calculated;
* the state is judged when it is read, so a certificate that expires after the
  measurement is recorded still stops the case being submitted.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.database import SessionLocal
from app.models import EquipmentCalibration

API = "/api/v1"
ENGINEER = "ENGINEER"
LAB_ADMIN = "LAB_ADMIN"


@pytest.fixture
def db(database):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def equipment(client, tokens):
    """Register equipment through the API, with an optional calibration."""

    def _create(
        *,
        valid_until: str | None = None,
        issue_date: str | None = None,
        active: bool = True,
        certificate_no: str | None = None,
        **overrides,
    ) -> dict:
        payload = {
            "code": "EQ-" + overrides.pop("code_suffix", ""),
            "name": "Reference weight set",
            "equipment_type": "weights",
            "manufacturer": "Mettler-Toledo",
            "model": "REF-WS-20",
            "serial_no": "SN-" + uuid.uuid4().hex[:8].upper(),
            "unit": "g",
            "accuracy_class": "M1",
            "is_active": active,
        }
        payload.update(overrides)
        response = client.post(f"{API}/equipment", json=payload, headers=tokens[LAB_ADMIN])
        assert response.status_code == 201, response.text
        record = response.json()
        if valid_until is not None or certificate_no:
            calibration = client.post(
                f"{API}/equipment/{record['id']}/calibrations",
                json={
                    "certificate_no": certificate_no or ("CERT-" + record["code"]),
                    "issued_by": "National Metrology Institute",
                    "issue_date": issue_date,
                    "valid_until": valid_until,
                    "uncertainty": "0.5 mg",
                },
                headers=tokens[LAB_ADMIN],
            )
            assert calibration.status_code == 201, calibration.text
        return record

    def factory(**overrides) -> dict:
        # The suite shares one database, so the register code must be unique
        # across tests rather than only within this fixture.
        overrides.setdefault("code_suffix", uuid.uuid4().hex[:8].upper())
        return _create(**overrides)

    return factory


def _days(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _test_by_code(client, tokens, case_id: str, code: str) -> dict:
    detail = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()
    return next(item for item in detail["tests"] if item["test_code"] == code)


def _attach(client, tokens, case_id: str, equipment_id: str, test_id: str | None = None):
    body = {"equipment_id": equipment_id, "role": "reference standard"}
    if test_id:
        body["test_instance_id"] = test_id
    return client.post(f"{API}/cases/{case_id}/equipment", json=body, headers=tokens[ENGINEER])


def _calculate(client, tokens, test_id: str):
    return client.post(f"{API}/tests/{test_id}/calculate", headers=tokens[ENGINEER])


# ------------------------------------------------------------------ register ---

def test_the_register_reports_status_and_expiry(client, tokens, equipment):
    valid = equipment(valid_until=_days(365), issue_date=_days(-30))
    expiring = equipment(valid_until=_days(5), issue_date=_days(-360))
    expired = equipment(valid_until=_days(-1), issue_date=_days(-400))
    uncalibrated = equipment()

    response = client.get(f"{API}/equipment/status", headers=tokens[LAB_ADMIN])
    assert response.status_code == 200, response.text
    states = {item["equipment_id"]: item for item in response.json()}

    assert states[valid["id"]]["status"] == "valid"
    assert states[valid["id"]]["days_remaining"] == 365
    assert states[valid["id"]]["blocking"] is False
    assert states[expiring["id"]]["status"] == "expiring"
    assert states[expiring["id"]]["blocking"] is False
    assert states[expired["id"]]["status"] == "expired"
    assert states[expired["id"]]["blocking"] is True
    assert states[uncalibrated["id"]]["status"] == "missing"
    assert states[uncalibrated["id"]]["blocking"] is True


def test_an_inactive_equipment_cannot_be_used(client, tokens, new_case, equipment):
    case_id = new_case()["id"]
    record = equipment(valid_until=_days(365), active=False)
    refused = _attach(client, tokens, case_id, record["id"])
    assert refused.status_code == 409
    assert "withdrawn from service" in refused.json()["detail"]


def test_equipment_without_a_calibration_cannot_be_used(client, tokens, new_case, equipment):
    case_id = new_case()["id"]
    record = equipment()
    refused = _attach(client, tokens, case_id, record["id"])
    assert refused.status_code == 409
    assert "No calibration record" in refused.json()["detail"]


def test_equipment_with_an_expired_calibration_cannot_be_used(
    client, tokens, new_case, equipment
):
    case_id = new_case()["id"]
    record = equipment(valid_until=_days(-1), issue_date=_days(-400))
    refused = _attach(client, tokens, case_id, record["id"])
    assert refused.status_code == 409
    assert "expired" in refused.json()["detail"].lower()


# ------------------------------------------------------------------ usage ------

def test_equipment_is_recorded_against_the_test_that_used_it(
    client, tokens, new_case, equipment
):
    case_id = new_case()["id"]
    record = equipment(valid_until=_days(365), issue_date=_days(-30))
    test = _test_by_code(client, tokens, case_id, "T-WP")

    created = _attach(client, tokens, case_id, record["id"], test["id"])
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["test_code"] == "T-WP"
    assert body["revision_no"] == 1
    assert body["calibration"]["status"] == "valid"
    assert body["calibration"]["certificate_no"] == "CERT-" + record["code"]
    assert body["calibration"]["valid_until"] == _days(365)

    listed = client.get(f"{API}/cases/{case_id}/equipment", headers=tokens[ENGINEER]).json()
    assert listed["recorded"] == 1
    assert listed["blocking"] == []
    assert listed["items"][0]["test_instance_id"] == test["id"]

    explanation = client.get(
        f"{API}/tests/{test['id']}/explanation", headers=tokens[ENGINEER]
    ).json()
    assert [item["equipment_id"] for item in explanation["equipment"]] == [record["id"]]
    assert explanation["calibration_blocking"] == []


def test_the_same_equipment_is_not_recorded_twice_for_one_test(
    client, tokens, new_case, equipment
):
    case_id = new_case()["id"]
    record = equipment(valid_until=_days(365))
    test = _test_by_code(client, tokens, case_id, "T-WP")
    assert _attach(client, tokens, case_id, record["id"], test["id"]).status_code == 201
    again = _attach(client, tokens, case_id, record["id"], test["id"])
    assert again.status_code == 409
    assert "already recorded" in again.json()["detail"]


def test_equipment_cannot_be_attached_to_a_superseded_record(
    client, tokens, case_factory, equipment
):
    case_id = case_factory()["id"]
    record = equipment(valid_until=_days(365))
    original = _test_by_code(client, tokens, case_id, "T-WP")
    replacement = client.post(
        f"{API}/tests/{original['id']}/retest",
        json={"reason": "Reference weights re-calibrated; repeat the test."},
        headers=tokens[ENGINEER],
    ).json()
    refused = _attach(client, tokens, case_id, record["id"], original["id"])
    assert refused.status_code == 409
    assert "superseded" in refused.json()["detail"]
    allowed = _attach(client, tokens, case_id, record["id"], replacement["id"])
    assert allowed.status_code == 201, allowed.text


# ------------------------------------------------------------------ gates ------

def test_a_calculation_is_refused_while_the_recorded_equipment_is_out_of_calibration(
    client, tokens, case_factory, equipment, observations, db
):
    case_id = case_factory()["id"]
    test = _test_by_code(client, tokens, case_id, "T-WP")
    record = equipment(valid_until=_days(365), issue_date=_days(-30))
    assert _attach(client, tokens, case_id, record["id"], test["id"]).status_code == 201

    # The calculation runs while the certificate is valid...
    assert _calculate(client, tokens, test["id"]).status_code == 200

    # ...and stops running the moment the certificate is no longer valid.
    calibration = (
        db.query(EquipmentCalibration)
        .filter(EquipmentCalibration.equipment_id == uuid.UUID(record["id"]))
        .one()
    )
    calibration.valid_until = date.today() - timedelta(days=1)
    db.commit()

    refused = _calculate(client, tokens, test["id"])
    assert refused.status_code == 409
    assert "may not be used" in refused.json()["detail"]

    readiness = client.get(
        f"{API}/cases/{case_id}/readiness", headers=tokens[ENGINEER]
    ).json()
    equipment_blockers = [item for item in readiness["blocking"] if item["kind"] == "equipment"]
    assert equipment_blockers, readiness["blocking"]
    assert readiness["ready_for_review"] is False
    assert readiness["equipment"]["blocking"][0]["code"] == record["code"]

    # A new certificate brings the test back into service.
    renewed = client.post(
        f"{API}/equipment/{record['id']}/calibrations",
        json={
            "certificate_no": "CERT-RENEWED",
            "issued_by": "National Metrology Institute",
            "issue_date": date.today().isoformat(),
            "valid_until": _days(730),
        },
        headers=tokens[LAB_ADMIN],
    )
    assert renewed.status_code == 201, renewed.text
    assert _calculate(client, tokens, test["id"]).status_code == 200

    # The row that was recorded keeps the details of the certificate that was in
    # force when it was recorded; the state follows the current certificate.
    listed = client.get(f"{API}/cases/{case_id}/equipment", headers=tokens[ENGINEER]).json()
    assert listed["blocking"] == []
    assert listed["items"][0]["calibration"]["status"] == "valid"
    assert listed["items"][0]["calibration"]["certificate_no"] == "CERT-RENEWED"


def test_the_gate_applies_to_the_test_that_used_the_equipment(
    client, tokens, case_factory, equipment, db
):
    """Equipment recorded against one test must not block another."""
    case_id = case_factory()["id"]
    with_equipment = _test_by_code(client, tokens, case_id, "T-REP")
    other = _test_by_code(client, tokens, case_id, "T-WP")
    record = equipment(valid_until=_days(365))
    assert _attach(client, tokens, case_id, record["id"], with_equipment["id"]).status_code == 201

    calibration = (
        db.query(EquipmentCalibration)
        .filter(EquipmentCalibration.equipment_id == uuid.UUID(record["id"]))
        .one()
    )
    calibration.valid_until = date.today() - timedelta(days=1)
    db.commit()

    assert _calculate(client, tokens, with_equipment["id"]).status_code == 409
    assert _calculate(client, tokens, other["id"]).status_code == 200


def test_withdrawing_equipment_clears_the_gate(client, tokens, case_factory, equipment, db):
    case_id = case_factory()["id"]
    test = _test_by_code(client, tokens, case_id, "T-WP")
    record = equipment(valid_until=_days(365))
    created = _attach(client, tokens, case_id, record["id"], test["id"])
    assert created.status_code == 201

    calibration = (
        db.query(EquipmentCalibration)
        .filter(EquipmentCalibration.equipment_id == uuid.UUID(record["id"]))
        .one()
    )
    calibration.valid_until = date.today() - timedelta(days=1)
    db.commit()
    assert _calculate(client, tokens, test["id"]).status_code == 409

    withdrawn = client.delete(
        f"{API}/cases/{case_id}/equipment/{created.json()['id']}", headers=tokens[ENGINEER]
    )
    assert withdrawn.status_code == 204, withdrawn.text
    listed = client.get(f"{API}/cases/{case_id}/equipment", headers=tokens[ENGINEER]).json()
    assert listed["recorded"] == 0
    assert _calculate(client, tokens, test["id"]).status_code == 200


def test_a_case_with_no_equipment_warns_but_does_not_block(client, tokens, case_factory):
    case_id = case_factory()["id"]
    readiness = client.get(
        f"{API}/cases/{case_id}/readiness", headers=tokens[ENGINEER]
    ).json()
    assert readiness["ready_for_review"] is True
    assert readiness["equipment"]["recorded"] == 0
    assert any(item["kind"] == "equipment" for item in readiness["warnings"])
