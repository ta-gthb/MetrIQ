"""The governed ruleset lifecycle, end to end (audit items 6 and 10).

These tests drive the states a ruleset has to pass through before it can decide
anything in production - draft, under review, approved, scheduled, active - and
the ways it must refuse to move:

* an unreviewed rule cannot reach a production ruleset, and the refusal names
  the rules that block it rather than saying "not allowed";
* signing a rule off binds the approval to the content that was signed, so a
  later edit silently revokes the approval instead of inheriting it;
* drafting, reviewing and approving are different permissions held by different
  roles, so one account cannot carry a change from idea to production alone;
* every step leaves an audit entry, and a historical case keeps the ruleset it
  was created against.

A private copy of the shipped ruleset is used throughout, under its own
``Standard``, so nothing here disturbs the reference data the rest of the suite
relies on.
"""

from __future__ import annotations

import uuid
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.security.permissions import APPROVER, ENGINEER, LAB_ADMIN, REVIEWER, SUPER_ADMIN

API = "/api/v1"

REVIEW_FIXTURE = {
    "reviewer_name": "Dr A. Metrologist",
    "reviewer_credentials": "Legal metrology reviewer, OIML R 76 scope",
    "source_revision": "OIML R 76-1:2006 (E)",
    "change_note": "Bands, tolerances and clause references checked against the controlled copy.",
    "boundary_cases_passed": True,
    "boundary_case_reference": "tests/test_rule_boundaries.py",
}


@pytest.fixture
def draft_ruleset(client, tokens):
    """Factory for a private, un-reviewed copy of the shipped catalogue.

    Each copy hangs off its own ``Standard`` rather than the shipped one, so
    activating it cannot supersede the reference ruleset the rest of the suite
    evaluates against. Test definitions are copied too, so a case can be opened
    against the copy while it is still a draft.
    """
    from app.database import SessionLocal
    from app.models import RuleVersion, Standard, StandardVersion, TestDefinition

    def _make(standard_id: str | None = None) -> dict:
        db = SessionLocal()
        try:
            source = db.execute(
                select(StandardVersion).where(StandardVersion.version_label == "r76-1-2006-v1")
            ).scalars().first()
            assert source is not None, "the shipped ruleset must be seeded"

            if standard_id is not None:
                # A second version of an existing fixture standard, so the
                # tests can activate a successor and watch what happens to
                # the records that were created under the predecessor.
                standard = db.get(Standard, uuid.UUID(standard_id))
                assert standard is not None
            else:
                standard = Standard(
                    code=f"OIML R 76-1 FIXTURE {uuid.uuid4().hex[:6].upper()}",
                    title="OIML R 76-1 fixture copy",
                    publisher="OIML",
                    category="oiml",
                    description="Test fixture: a private copy of the shipped ruleset.",
                )
                db.add(standard)
                db.flush()

            version = StandardVersion(
                standard_id=standard.id,
                edition=source.edition,
                version_label=f"fixture-{uuid.uuid4().hex[:8]}",
                status="draft",
                is_active=False,
                source_reference=source.source_reference,
                notes="Test fixture: un-reviewed draft.",
            )
            db.add(version)
            db.flush()

            rule_versions = db.execute(
                select(RuleVersion).where(RuleVersion.standard_version_id == source.id)
            ).scalars().all()
            assert rule_versions, "the shipped ruleset must carry rules"
            # (rule_id, version_label) is unique across the whole table, so the
            # copies need their own label; the content is what the tests exercise.
            tag = version.version_label
            copies = []
            for row in rule_versions:
                copy = RuleVersion(
                    rule_id=row.rule_id,
                    standard_version_id=version.id,
                    version_label=tag,
                    definition=deepcopy(row.definition),
                    formula=row.formula,
                    threshold=row.threshold,
                    unit=row.unit,
                    applicability=deepcopy(row.applicability),
                    rounding_policy=row.rounding_policy,
                    review_status="pending_domain_review",
                    is_active=row.is_active,
                )
                db.add(copy)
                copies.append(copy)

            definitions = db.execute(
                select(TestDefinition).where(TestDefinition.standard_version_id == source.id)
            ).scalars().all()
            for row in definitions:
                db.add(TestDefinition(
                    test_code=row.test_code,
                    name=row.name,
                    standard_version_id=version.id,
                    clause_reference=row.clause_reference,
                    category=row.category,
                    phase=row.phase,
                    description=row.description,
                    applicability_expression=deepcopy(row.applicability_expression),
                    input_schema=deepcopy(row.input_schema),
                    validation_rules=deepcopy(row.validation_rules),
                    calculation_rules=deepcopy(row.calculation_rules),
                    compliance_rules=deepcopy(row.compliance_rules),
                    report_section_mapping=deepcopy(row.report_section_mapping),
                    evidence_requirements=deepcopy(row.evidence_requirements),
                    sequence_no=row.sequence_no,
                    is_active=row.is_active,
                ))
            db.commit()

            return {
                "standard_id": str(standard.id),
                "version_id": str(version.id),
                "rule_version_ids": [str(row.id) for row in copies],
                "rule_count": len(copies),
            }
        finally:
            db.close()

    return _make


def review_every_rule(client, tokens, version_id, rule_version_ids, **overrides):
    payload = {**REVIEW_FIXTURE, "decision": "approved", **overrides}
    for rule_version_id in rule_version_ids:
        response = client.post(
            f"{API}/rulesets/{version_id}/rules/{rule_version_id}/review",
            json=payload,
            headers=tokens[SUPER_ADMIN],
        )
        assert response.status_code == 200, response.text
    return payload


def approve_and_activate(client, tokens, version_id, **overrides):
    payload = {**REVIEW_FIXTURE, "change_note": "Whole rule set approved.", **overrides}
    approved = client.post(
        f"{API}/rulesets/{version_id}/approve", json=payload, headers=tokens[SUPER_ADMIN]
    )
    assert approved.status_code == 200, approved.text
    activated = client.post(
        f"{API}/rulesets/{version_id}/activate",
        json={"reason": "Approved for production use"},
        headers=tokens[SUPER_ADMIN],
    )
    assert activated.status_code == 200, activated.text
    return activated.json()


# ------------------------------------------------------------- the gate ---
def test_an_unreviewed_ruleset_cannot_be_activated(client, tokens, draft_ruleset):
    fixture = draft_ruleset()
    version_id = fixture["version_id"]
    response = client.post(
        f"{API}/rulesets/{version_id}/activate", headers=tokens[SUPER_ADMIN]
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["unreviewed_rules"], detail
    assert len(detail["unreviewed_rules"]) == fixture["rule_count"], detail
    assert "metrology review" in detail["message"]
    # The refusal names the rules, so the reviewer knows what to look at.
    assert all(row["rule_code"] and row["clause_reference"] for row in detail["unreviewed_rules"])


def test_the_review_package_lists_every_rule_with_its_clause_and_formula(client, tokens, draft_ruleset):
    fixture = draft_ruleset()
    response = client.get(
        f"{API}/rulesets/{fixture['version_id']}/review-package",
        headers=tokens[SUPER_ADMIN],
    )
    assert response.status_code == 200, response.text
    package = response.json()
    assert package["rule_count"] == fixture["rule_count"]
    assert package["reviewed_rule_count"] == 0
    assert package["can_activate"] is False
    for rule in package["rules"]:
        assert rule["clause_reference"], rule
        assert rule["formula"], rule
        assert rule["fingerprint"], rule
        assert rule["review_status"] == "pending"
        assert rule["pending_reason"]


def test_reviewing_every_rule_is_not_enough_without_a_ruleset_approval(client, tokens, draft_ruleset):
    fixture = draft_ruleset()
    version_id = fixture["version_id"]
    review_every_rule(client, tokens, version_id, fixture["rule_version_ids"])

    package = client.get(f"{API}/rulesets/{version_id}/review-package", headers=tokens[SUPER_ADMIN]).json()
    assert package["reviewed_rule_count"] == package["rule_count"]
    # Every rule is signed off, but the set as a whole still has not been.
    assert package["can_activate"] is False
    assert "whole" in package["activation_gate"]["summary"]

    refused = client.post(f"{API}/rulesets/{version_id}/activate", headers=tokens[SUPER_ADMIN])
    assert refused.status_code == 422, refused.text


def test_the_full_lifecycle_reaches_an_active_domain_reviewed_ruleset(client, tokens, draft_ruleset):
    fixture = draft_ruleset()
    version_id = fixture["version_id"]

    submitted = client.post(
        f"{API}/rulesets/{version_id}/submit-review",
        json={"note": "Catalogue complete; ready for technical review."},
        headers=tokens[SUPER_ADMIN],
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["lifecycle_state"] == "under_review"

    review_every_rule(client, tokens, version_id, fixture["rule_version_ids"])
    body = approve_and_activate(client, tokens, version_id)

    assert body["is_active"] is True
    assert body["status"] == "active"
    assert body["activation_basis"] == "domain_review"
    assert body["approved_by_name"] == REVIEW_FIXTURE["reviewer_name"]
    assert body["approved_fingerprint"]
    assert body["can_activate"] is True


def test_super_admin_activation_enables_the_six_proposed_tests(client, tokens, draft_ruleset):
    from app.database import SessionLocal
    from app.models import TestDefinition

    fixture = draft_ruleset()
    version_id = fixture["version_id"]
    submitted = client.post(
        f"{API}/rulesets/{version_id}/submit-review",
        json={"note": "Review proposed influence-factor procedures."},
        headers=tokens[SUPER_ADMIN],
    )
    assert submitted.status_code == 200, submitted.text
    review_every_rule(client, tokens, version_id, fixture["rule_version_ids"])
    approve_and_activate(client, tokens, version_id)

    db = SessionLocal()
    try:
        rows = db.execute(
            select(TestDefinition).where(TestDefinition.standard_version_id == uuid.UUID(version_id))
        ).scalars().all()
        active = {row.test_code for row in rows if row.is_active}
    finally:
        db.close()

    assert {"T-TILT", "T-TARE", "T-WARMUP", "T-VOLT", "T-EMC", "T-DAMP"} <= active


def test_scheduling_records_the_effective_date_and_still_activates(client, tokens, draft_ruleset):
    fixture = draft_ruleset()
    version_id = fixture["version_id"]
    client.post(f"{API}/rulesets/{version_id}/submit-review", json={}, headers=tokens[SUPER_ADMIN])
    review_every_rule(client, tokens, version_id, fixture["rule_version_ids"])

    approved = client.post(
        f"{API}/rulesets/{version_id}/approve",
        json={**REVIEW_FIXTURE, "change_note": "Approved for a future edition."},
        headers=tokens[SUPER_ADMIN],
    )
    assert approved.status_code == 200, approved.text

    scheduled = client.post(
        f"{API}/rulesets/{version_id}/schedule",
        json={"effective_from": "2027-01-01", "reason": "Aligned to the next verification cycle."},
        headers=tokens[SUPER_ADMIN],
    )
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["lifecycle_state"] == "scheduled"
    assert scheduled.json()["scheduled_for"] == "2027-01-01"
    assert scheduled.json()["is_active"] is False

    activated = client.post(
        f"{API}/rulesets/{version_id}/activate",
        json={"reason": "Effective date reached"},
        headers=tokens[SUPER_ADMIN],
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["is_active"] is True


# --------------------------------------------------- content-bound review ---
def test_editing_a_rule_after_sign_off_revokes_its_approval(client, tokens, draft_ruleset):
    """The approval binds to content, not to a flag someone has to remember."""
    from app.database import SessionLocal
    from app.models import RuleVersion

    fixture = draft_ruleset()
    version_id = fixture["version_id"]
    client.post(f"{API}/rulesets/{version_id}/submit-review", json={}, headers=tokens[SUPER_ADMIN])
    review_every_rule(client, tokens, version_id, fixture["rule_version_ids"])
    approved = client.post(
        f"{API}/rulesets/{version_id}/approve",
        json={**REVIEW_FIXTURE, "change_note": "Approved before the edit."},
        headers=tokens[SUPER_ADMIN],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["can_activate"] is True, approved.text

    target = next(rule for rule in approved.json()["rules"] if rule["code"].startswith("R76-MPE"))
    db = SessionLocal()
    try:
        row = db.get(RuleVersion, uuid.UUID(target["rule_version_id"]))
        # A quiet edit to a band, after the reviewer signed it off.
        row.definition = {**(row.definition or {}), "bands": []}
        db.add(row)
        db.commit()
    finally:
        db.close()

    package = client.get(
        f"{API}/rulesets/{version_id}/review-package", headers=tokens[SUPER_ADMIN]
    ).json()
    assert package["can_activate"] is False, package["activation_gate"]
    stale = [
        rule for rule in package["rules"]
        if rule["pending_reason"] == "rule changed after it was reviewed"
    ]
    assert target["code"] in {rule["rule_code"] for rule in stale}, package["activation_gate"]

    refused = client.post(f"{API}/rulesets/{version_id}/activate", headers=tokens[SUPER_ADMIN])
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["unreviewed_rules"]


def test_a_rejected_rule_blocks_the_ruleset_and_a_later_approval_clears_it(client, tokens, draft_ruleset):
    fixture = draft_ruleset()
    version_id = fixture["version_id"]
    first = fixture["rule_version_ids"][0]

    rejected = client.post(
        f"{API}/rulesets/{version_id}/rules/{first}/review",
        json={**REVIEW_FIXTURE, "decision": "needs_changes",
              "change_note": "Band 2 upper bound is wrong."},
        headers=tokens[SUPER_ADMIN],
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["review_status"] == "needs_changes"

    gap = client.get(f"{API}/rulesets/{version_id}/review-package", headers=tokens[SUPER_ADMIN]).json()
    assert any(row["rule_version_id"] == first for row in gap["activation_gate"]["unreviewed_rules"])

    cleared = client.post(
        f"{API}/rulesets/{version_id}/rules/{first}/review",
        json={**REVIEW_FIXTURE, "decision": "approved",
              "change_note": "Bound corrected and re-checked."},
        headers=tokens[SUPER_ADMIN],
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["review_status"] == "approved"


def test_a_rule_cannot_be_approved_while_its_boundary_cases_fail(client, tokens, draft_ruleset):
    fixture = draft_ruleset()
    response = client.post(
        f"{API}/rulesets/{fixture['version_id']}/rules/{fixture['rule_version_ids'][0]}/review",
        json={**REVIEW_FIXTURE, "decision": "approved", "boundary_cases_passed": False},
        headers=tokens[SUPER_ADMIN],
    )
    assert response.status_code == 422, response.text


# --------------------------------------------------------- authorisation ---
def test_drafting_reviewing_and_approving_are_different_permissions(client, tokens, draft_ruleset):
    fixture = draft_ruleset()
    version_id = fixture["version_id"]
    rule_version_id = fixture["rule_version_ids"][0]
    review_body = {**REVIEW_FIXTURE, "decision": "approved"}

    for role in (ENGINEER, LAB_ADMIN, REVIEWER, APPROVER):
        response = client.post(
            f"{API}/rulesets/{version_id}/rules/{rule_version_id}/review",
            json=review_body, headers=tokens[role],
        )
        assert response.status_code == 403, role

    reviewed = client.post(
        f"{API}/rulesets/{version_id}/rules/{rule_version_id}/review",
        json=review_body, headers=tokens[SUPER_ADMIN],
    )
    assert reviewed.status_code == 200, reviewed.text


def test_a_rejection_needs_a_change_note(client, tokens, draft_ruleset):
    fixture = draft_ruleset()
    version_id = fixture["version_id"]
    client.post(f"{API}/rulesets/{version_id}/submit-review", json={}, headers=tokens[SUPER_ADMIN])
    response = client.post(
        f"{API}/rulesets/{version_id}/reject", json={}, headers=tokens[SUPER_ADMIN]
    )
    assert response.status_code == 422, response.text

    rejected = client.post(
        f"{API}/rulesets/{version_id}/reject",
        json={**REVIEW_FIXTURE,
              "change_note": "Table 1 upper bound for band 2 is transcribed wrongly."},
        headers=tokens[SUPER_ADMIN],
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["lifecycle_state"] == "draft"


# ------------------------------------------------------- activation edges ---
def test_deactivation_requires_a_reason_and_is_recorded(client, tokens, draft_ruleset):
    fixture = draft_ruleset()
    version_id = fixture["version_id"]
    client.post(f"{API}/rulesets/{version_id}/submit-review", json={}, headers=tokens[SUPER_ADMIN])
    review_every_rule(client, tokens, version_id, fixture["rule_version_ids"])
    approved = client.post(
        f"{API}/rulesets/{version_id}/approve",
        json={**REVIEW_FIXTURE, "change_note": "Approved."},
        headers=tokens[SUPER_ADMIN],
    )
    assert approved.status_code == 200, approved.text
    assert client.post(
        f"{API}/rulesets/{version_id}/activate", json={}, headers=tokens[SUPER_ADMIN]
    ).status_code == 200

    blank = client.post(
        f"{API}/rulesets/{version_id}/deactivate", json={"reason": "   "}, headers=tokens[SUPER_ADMIN]
    )
    assert blank.status_code == 422, blank.text

    withdrawn = client.post(
        f"{API}/rulesets/{version_id}/deactivate",
        json={"reason": "Withdrawn pending a corrected edition."},
        headers=tokens[SUPER_ADMIN],
    )
    assert withdrawn.status_code == 200, withdrawn.text
    body = withdrawn.json()
    assert body["is_active"] is False
    assert body["lifecycle_state"] == "retired"
    assert body["deactivation_reason"] == "Withdrawn pending a corrected edition."
    assert body["deactivated_at"]


def test_a_scheduled_ruleset_can_be_unscheduled_before_it_takes_effect(client, tokens, draft_ruleset):
    fixture = draft_ruleset()
    version_id = fixture["version_id"]
    client.post(f"{API}/rulesets/{version_id}/submit-review", json={}, headers=tokens[SUPER_ADMIN])
    review_every_rule(client, tokens, version_id, fixture["rule_version_ids"])
    client.post(
        f"{API}/rulesets/{version_id}/approve",
        json={**REVIEW_FIXTURE, "change_note": "Approved."}, headers=tokens[SUPER_ADMIN],
    )
    scheduled = client.post(
        f"{API}/rulesets/{version_id}/schedule",
        json={"effective_from": "2027-06-01"}, headers=tokens[SUPER_ADMIN],
    )
    assert scheduled.status_code == 200, scheduled.text

    again = client.post(
        f"{API}/rulesets/{version_id}/approve",
        json={**REVIEW_FIXTURE, "change_note": "Approved again."}, headers=tokens[SUPER_ADMIN],
    )
    assert again.status_code == 200, again.text
    assert again.json()["lifecycle_state"] == "approved"
    assert again.json()["is_active"] is False


def test_the_active_state_always_records_how_it_got_there(client, tokens, draft_ruleset):
    fixture = draft_ruleset()
    version_id = fixture["version_id"]
    client.post(f"{API}/rulesets/{version_id}/submit-review", json={}, headers=tokens[SUPER_ADMIN])
    review_every_rule(client, tokens, version_id, fixture["rule_version_ids"])
    body = approve_and_activate(client, tokens, version_id)
    assert body["activation_basis"] == "domain_review"
    assert body["approved_at"]
    assert body["activated_at"]


# ------------------------------------------------------------- audit trail ---
def test_every_lifecycle_step_is_audited(client, tokens, draft_ruleset):
    from app.database import SessionLocal
    from app.models import AuditLog

    fixture = draft_ruleset()
    version_id = fixture["version_id"]
    client.post(f"{API}/rulesets/{version_id}/submit-review", json={}, headers=tokens[SUPER_ADMIN])
    review_every_rule(client, tokens, version_id, fixture["rule_version_ids"])
    approve_and_activate(client, tokens, version_id)

    entity_ids = [version_id, *fixture["rule_version_ids"]]
    db = SessionLocal()
    try:
        rows = db.execute(
            select(AuditLog).where(AuditLog.entity_id.in_(entity_ids))
        ).scalars().all()
        events = {row.event_type for row in rows}
    finally:
        db.close()

    assert {"RULE_SUBMIT", "RULE_REVIEW", "RULE_APPROVAL", "RULE_ACTIVATION"} <= events, sorted(events)
    approval = next(row for row in rows if row.event_type == "RULE_APPROVAL")
    assert approval.actor_email
    assert approval.after_value["approved_by"] == REVIEW_FIXTURE["reviewer_name"]


# ----------------------------------------------------- historical binding ---
def activate_fixture(client, tokens, fixture):
    version_id = fixture["version_id"]
    client.post(f"{API}/rulesets/{version_id}/submit-review", json={}, headers=tokens[SUPER_ADMIN])
    review_every_rule(client, tokens, version_id, fixture["rule_version_ids"])
    return approve_and_activate(client, tokens, version_id)


def test_a_case_keeps_the_ruleset_it_was_created_against(client, tokens, new_case, draft_ruleset):
    """Activating a successor must not move an existing case onto the new rules."""
    first = draft_ruleset()

    # A case opened under the first version, before either is activated, so the
    # scenario matches reality: work starts, then the ruleset changes.
    case = new_case(standard_version_id=first["version_id"])
    assert case["standard_version_id"] == first["version_id"]

    activate_fixture(client, tokens, first)

    # A second version of the same standard, reviewed and activated in turn.
    second = draft_ruleset(standard_id=first["standard_id"])
    activate_fixture(client, tokens, second)

    superseded = client.get(f"{API}/rulesets/{first['version_id']}", headers=tokens[SUPER_ADMIN]).json()
    assert superseded["is_active"] is False
    assert superseded["lifecycle_state"] == "superseded"
    assert superseded["deactivated_at"]
    assert superseded["deactivation_reason"]

    refreshed = client.get(f"{API}/cases/{case['id']}", headers=tokens[ENGINEER]).json()
    assert refreshed["standard_version_id"] == first["version_id"], refreshed
