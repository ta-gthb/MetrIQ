"""Deployment discovery and System Administrator ruleset controls."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Rule, RuleVersion, Standard, StandardVersion, TestDefinition as RulesetTestDefinition
from app.security.permissions import APPROVER, ENGINEER, LAB_ADMIN, REVIEWER, SUPER_ADMIN

API = "/api/v1"


@pytest.fixture
def inactive_ruleset():
    standard_code = f"GO-{uuid.uuid4().hex[:8].upper()}"
    version_label = f"gov-order-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        standard = Standard(
            code=standard_code,
            title="Government Order fixture",
            publisher="Government",
        )
        db.add(standard)
        db.flush()
        version = StandardVersion(
            standard_id=standard.id,
            edition="2026",
            version_label=version_label,
            status="inactive",
            is_active=False,
        )
        db.add(version)
        db.flush()
        rule = Rule(
            code=f"{standard_code}-RULE-1",
            name="Government Order rule",
            category="mpe",
        )
        db.add(rule)
        db.flush()
        db.add(
            RuleVersion(
                rule_id=rule.id,
                standard_version_id=version.id,
                version_label=version_label,
                definition={"factor": "0.5"},
            )
        )
        test_definition = RulesetTestDefinition(
            test_code=f"{standard_code}-TEST",
            name="Government Order test",
            standard_version_id=version.id,
            phase="2",
            is_active=False,
        )
        db.add(test_definition)
        db.commit()
        return {
            "standard_id": standard.id,
            "version_id": version.id,
            "version_label": version_label,
            "test_definition_id": test_definition.id,
        }


def test_rule_files_are_discovered_by_deployment_seeding(
    tmp_path, monkeypatch, client, tokens, accounts
):
    import json

    from app.rules import loader
    from app.services.reference_data.rules import seed_new_rulesets

    label = f"gov-order-{uuid.uuid4().hex[:8]}"
    payload = {
        "standard": "OIML R 76-1",
        "version_label": label,
        "edition": "Government Order 2026",
        "mpe": {
            "clause_reference": "Government Order 2026 section 4",
            "unit": "e",
            "bands": {"III": [{"min_m": 0, "max_m": 10, "factor": "0.5"}]},
        },
        "tolerances": {},
    }
    (tmp_path / f"{label}.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    loader.load_ruleset.cache_clear()

    try:
        with SessionLocal() as db:
            versions = seed_new_rulesets(db)
            db.commit()
            assert [version.version_label for version in versions] == [label]
            version = db.execute(
                select(StandardVersion).where(StandardVersion.version_label == label)
            ).scalars().one()
            assert version.is_active is False
            assert version.status == "inactive"

        response = client.get(f"{API}/rulesets", headers=tokens[SUPER_ADMIN])
        assert response.status_code == 200, response.text
        entry = next(row for row in response.json() if row["version_label"] == label)
        assert entry["status"] == "inactive"
        assert entry["is_active"] is False
        assert entry["rule_count"] > 0
    finally:
        loader.load_ruleset.cache_clear()


def test_flatten_rules_supports_government_order_rule_entries():
    from app.rules.loader import flatten_rules

    entries = flatten_rules(
        {
            "version_label": "gov-order-2026-v1",
            "rules": [
                {
                    "code": "GO-2026-01",
                    "name": "Maximum error",
                    "category": "tolerance",
                    "clause_reference": "Government Order section 4",
                    "definition": {"factor": "0.5"},
                    "formula": "factor * e",
                }
            ],
        }
    )
    assert entries[0]["code"] == "GO-2026-01"
    assert entries[0]["definition"] == {"factor": "0.5"}
    assert entries[0]["version_label"] == "gov-order-2026-v1"


def test_rule_set_list_exposes_only_active_or_inactive_state(client, tokens, inactive_ruleset):
    response = client.get(f"{API}/rulesets", headers=tokens[SUPER_ADMIN])
    assert response.status_code == 200, response.text
    entry = next(
        row for row in response.json()
        if row["version_label"] == inactive_ruleset["version_label"]
    )
    assert entry["status"] == "inactive"
    assert "can_activate" not in entry
    assert "review_status" not in entry


def test_inactive_ruleset_cannot_be_selected_for_a_new_case(
    new_case, inactive_ruleset
):
    response = new_case(
        _expected_status=422,
        standard_version_id=str(inactive_ruleset["version_id"]),
    )
    assert "inactive" in response["detail"]
    assert "System Administrator" in response["detail"]


def test_only_system_administrator_can_activate_or_deactivate(
    client, tokens, inactive_ruleset
):
    version_id = inactive_ruleset["version_id"]
    for role in (ENGINEER, LAB_ADMIN, REVIEWER, APPROVER):
        response = client.post(
            f"{API}/rulesets/{version_id}/activate", headers=tokens[role]
        )
        assert response.status_code == 403, (role, response.text)

    activated = client.post(
        f"{API}/rulesets/{version_id}/activate",
        json={"reason": "Government Order adopted."},
        headers=tokens[SUPER_ADMIN],
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"
    assert "activation_basis" not in activated.json()
    with SessionLocal() as db:
        test_definition = db.get(RulesetTestDefinition, inactive_ruleset["test_definition_id"])
        assert test_definition.is_active is True

    for role in (ENGINEER, LAB_ADMIN, REVIEWER, APPROVER):
        response = client.post(
            f"{API}/rulesets/{version_id}/deactivate",
            json={"reason": "Not allowed"},
            headers=tokens[role],
        )
        assert response.status_code == 403, (role, response.text)


def test_deactivation_returns_the_ruleset_to_inactive(client, tokens, inactive_ruleset):
    version_id = inactive_ruleset["version_id"]
    activated = client.post(
        f"{API}/rulesets/{version_id}/activate", headers=tokens[SUPER_ADMIN]
    )
    assert activated.status_code == 200, activated.text

    response = client.post(
        f"{API}/rulesets/{version_id}/deactivate",
        json={"reason": "Government Order withdrawn."},
        headers=tokens[SUPER_ADMIN],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_active"] is False
    assert body["status"] == "inactive"
    assert body["deactivation_reason"] == "Government Order withdrawn."
    assert body["deactivated_at"]


def test_review_approval_and_schedule_endpoints_are_unavailable(
    client, tokens, inactive_ruleset
):
    version_id = inactive_ruleset["version_id"]
    obsolete_routes = [
        ("get", f"/rulesets/{version_id}/review-package", None),
        ("post", f"/rulesets/{version_id}/submit-review", {}),
        ("post", f"/rulesets/{version_id}/approve", {}),
        ("post", f"/rulesets/{version_id}/reject", {}),
        ("post", f"/rulesets/{version_id}/schedule", {"effective_from": "2027-01-01"}),
    ]
    for method, path, payload in obsolete_routes:
        response = getattr(client, method)(
            f"{API}{path}", json=payload, headers=tokens[SUPER_ADMIN]
        ) if method == "post" else client.get(
            f"{API}{path}", headers=tokens[SUPER_ADMIN]
        )
        assert response.status_code in {404, 405}, (path, response.status_code)
