"""Shared fixtures for the MetrIQ test suite (PRD 22).

The suite runs against a throwaway SQLite database in the system temp folder so
it never touches the developer's demonstration data. Environment variables are
set before the application package is imported because the settings object is
created once at import time.
"""

from __future__ import annotations

import itertools
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TMP = Path(tempfile.mkdtemp(prefix="metriq-tests-"))
(_TMP / "storage").mkdir(parents=True, exist_ok=True)
(_TMP / "reports").mkdir(parents=True, exist_ok=True)

os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "false"
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'metriq-test.db').as_posix()}"
os.environ["AUTH_PROVIDER"] = "local"
os.environ["AI_PROVIDER"] = "stub"
os.environ["AI_ENABLED"] = "true"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["STORAGE_LOCAL_PATH"] = str(_TMP / "storage")
os.environ["REPORT_STORAGE_PATH"] = str(_TMP / "reports")
os.environ["JWT_SECRET"] = "test-only-secret-not-for-deployment"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.security.permissions import ENGINEER  # noqa: E402
from app.security.permissions import REVIEWER, SUPER_ADMIN  # noqa: E402
from scripts.seed_db import (  # noqa: E402
    DEFAULT_PASSWORD,
    seed_laboratory,
    seed_masters,
    seed_users,
)
from scripts.seed_identity import seed_roles_and_permissions  # noqa: E402
from scripts.seed_rules import (  # noqa: E402
    seed_report_template,
    seed_ruleset,
    seed_test_catalogue,
)

API = "/api/v1"

INSTRUMENT_TEMPLATE: dict[str, str] = {
    "model": "TEST-BENCH-30K",
    "type_designation": "TB-30K-III",
    "instrument_class": "III",
    "max_capacity": "30000",
    "min_capacity": "200",
    "verification_scale_interval": "10",
    "actual_scale_interval": "10",
    "unit": "g",
}

# Reference observation sets that must resolve to PASS for a Class III,
# e = d = 10 g electronic instrument. They double as worked examples in
# docs/calculations.md.
OBSERVATIONS: dict[str, list[dict]] = {
    "T-WP": [
        {"observation_no": 1, "position_label": "Zero", "load": "0",
         "indication": "0", "additional_load": "5"},
        {"observation_no": 2, "position_label": "10 kg", "load": "10000",
         "indication": "10002", "additional_load": "5"},
        {"observation_no": 3, "position_label": "25 kg", "load": "25000",
         "indication": "25001", "additional_load": "5"},
    ],
    "T-REP": [
        {"observation_no": 1, "position_label": "Run 1", "load": "15000",
         "indication": "15000", "additional_load": "5"},
        {"observation_no": 2, "position_label": "Run 2", "load": "15000",
         "indication": "15002", "additional_load": "5"},
        {"observation_no": 3, "position_label": "Run 3", "load": "15000",
         "indication": "15001", "additional_load": "5"},
    ],
    "T-ECC": [
        {"observation_no": 1, "position_label": "centre", "load": "15000",
         "indication": "15000", "additional_load": "5"},
        {"observation_no": 2, "position_label": "front-left", "load": "15000",
         "indication": "15003", "additional_load": "5"},
        {"observation_no": 3, "position_label": "rear-right", "load": "15000",
         "indication": "14998", "additional_load": "5"},
    ],
    "T-ZR": [
        {"observation_no": 1, "position_label": "initial", "value": "0"},
        {"observation_no": 2, "position_label": "after unloading", "value": "2"},
    ],
    "T-CREEP": [
        {"observation_no": 1, "position_label": "t = 0", "value": "0", "elapsed_seconds": "0"},
        {"observation_no": 2, "position_label": "t = 30 min", "value": "2", "elapsed_seconds": "1800"},
    ],
    "T-TEMP-NL": [
        {"observation_no": 1, "position_label": "+20 C", "value": "0", "temperature_c": "20"},
        {"observation_no": 2, "position_label": "+40 C", "value": "3", "temperature_c": "40"},
    ],
    "T-SENS": [
        {"observation_no": 1, "position_label": "initial", "load": "5000", "indication": "5000"},
        {"observation_no": 2, "position_label": "after 10 g", "load": "5010", "indication": "5012"},
    ],
    "T-DISC": [
        {"observation_no": 1, "position_label": "1.4 d added", "load": "14", "value": "10"},
    ],
    "T-STAB": [
        {"observation_no": 1, "position_label": "start", "value": "0"},
        {"observation_no": 2, "position_label": "end", "value": "4"},
    ],
    "T-CHK-CON": [
        {"observation_no": index, "item_code": code, "conforms": True}
        for index, code in enumerate(
            ["CON-01", "CON-02", "CON-03", "CON-04", "CON-06", "CON-07"], start=1
        )
    ],
    "T-CHK-ID": [
        {"observation_no": index, "item_code": code, "conforms": True}
        for index, code in enumerate(
            ["ID-01", "ID-02", "ID-03", "ID-04", "ID-05", "ID-06", "ID-09"], start=1
        )
    ],
}


@pytest.fixture(scope="session", autouse=True)
def database():
    """Create the schema and seed reference data once per session."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_roles_and_permissions(db)
        standard_version = seed_ruleset(db)
        seed_report_template(db, standard_version)
        seed_test_catalogue(db, standard_version)
        standard_version.is_active = True
        standard_version.status = "active"
        db.commit()
    finally:
        db.close()
    yield
    engine.dispose()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def accounts(database) -> dict:
    db = SessionLocal()
    try:
        laboratory = seed_laboratory(db)
        users = seed_users(db, laboratory, DEFAULT_PASSWORD)
        masters = seed_masters(db)
        db.commit()
        return {
            "password": DEFAULT_PASSWORD,
            "laboratory_id": str(laboratory.id),
            "emails": {role: user.email for role, user in users.items()},
            "user_ids": {role: str(user.id) for role, user in users.items()},
            "applicant_id": str(masters["applicant"].id),
            "manufacturer_id": str(masters["manufacturer"].id),
        }
    finally:
        db.close()


@pytest.fixture(scope="session")
def client(accounts):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def tokens(client, accounts) -> dict[str, dict[str, str]]:
    """Authorization headers for one account per role."""
    issued: dict[str, dict[str, str]] = {}
    for role, email in accounts["emails"].items():
        response = client.post(
            f"{API}/auth/login", json={"email": email, "password": accounts["password"]}
        )
        assert response.status_code == 200, response.text
        issued[role] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    return issued


@pytest.fixture
def new_case(client, tokens, accounts):
    """Factory that creates a fresh evaluation case through the public API."""
    counter = itertools.count(1)

    def _create(**overrides) -> dict:
        number = next(counter)
        payload = {
            "title": f"Test bench evaluation #{number}",
            "purpose": "Automated test fixture",
            "instrument": {
                **INSTRUMENT_TEMPLATE,
                "serial_number": f"TEST-{number:05d}",
            },
        }
        payload.update(overrides)
        response = client.post(f"{API}/cases", json=payload, headers=tokens[ENGINEER])
        assert response.status_code == 201, response.text
        return response.json()

    return _create


@pytest.fixture
def fill_case(client, tokens):
    """Record a passing observation set on every applicable test of a case."""

    def _fill(case_id: str) -> dict:
        detail = client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()
        for test in detail["tests"]:
            if test["applicability_status"] != "APPLICABLE":
                continue
            code = test["test_code"]
            rows = OBSERVATIONS.get(code)
            assert rows is not None, f"no reference observations defined for {code}"
            response = client.put(
                f"{API}/tests/{test['id']}/observations",
                json={"observations": rows, "replace": True},
                headers=tokens[ENGINEER],
            )
            assert response.status_code == 200, response.text
            response = client.post(
                f"{API}/tests/{test['id']}/calculate", headers=tokens[ENGINEER]
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["is_valid"], f"{code}: {body['errors']}"
            assert body["status"] in {"PASS", "FAIL"}, f"{code}: {body['status']}"
            # Recording a result is not the same as declaring the test finished;
            # the engineer confirms completion explicitly.
            completed = client.patch(
                f"{API}/tests/{test['id']}",
                json={"mark_complete": True},
                headers=tokens[ENGINEER],
            )
            assert completed.status_code == 200, completed.text
            stored = completed.json()
            assert stored["status"] == "COMPLETED", f"{code}: {stored['status']}"
            assert stored["result_status"] == "PASS", f"{code}: {stored['result_status']}"
        return client.get(f"{API}/cases/{case_id}", headers=tokens[ENGINEER]).json()

    return _fill


@pytest.fixture
def case_factory(client, tokens, new_case, fill_case, accounts):
    """A fully recorded case with a named reviewer and approver attached."""

    def _build(**overrides) -> dict:
        case = new_case(**overrides)
        case_id = case["id"]
        response = client.post(
            f"{API}/cases/{case_id}/assignments",
            json={
                "engineer_id": accounts["user_ids"][ENGINEER],
                "reviewer_id": accounts["user_ids"][REVIEWER],
                "approver_id": accounts["user_ids"]["APPROVER"],
                "reason": "Automated test fixture assignment",
            },
            headers=tokens[SUPER_ADMIN],
        )
        assert response.status_code == 200, response.text
        detail = fill_case(case_id)
        detail["_fixture_ids"] = {
            "engineer": accounts["user_ids"][ENGINEER],
            "reviewer": accounts["user_ids"][REVIEWER],
            "approver": accounts["user_ids"]["APPROVER"],
        }
        return detail

    return _build
