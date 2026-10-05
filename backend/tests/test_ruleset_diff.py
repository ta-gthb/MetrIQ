"""Rule-set diff and impact analysis (audit item 15).

A reviewer has to see what a rule-set change touches before it takes effect:
the rules that moved field by field, the procedures that read them, and how
much history each version carries. The impact map is derived from the test
catalogue, not from a hand-maintained list that would drift.
"""

from __future__ import annotations

import copy
import uuid

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import GeneratedReport, Rule, RuleVersion, StandardVersion
from app.security.permissions import SUPER_ADMIN
from tests.test_api_workflow import run_lifecycle

API = "/api/v1"
SEEDED_LABEL = "r76-1-2006-v1"


@pytest.fixture
def db(database):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def seeded(db) -> StandardVersion:
    version = db.execute(
        select(StandardVersion).where(StandardVersion.version_label == SEEDED_LABEL)
    ).scalars().one()
    return version


def _clone_as_draft(db, source: StandardVersion, factor_for: dict[str, str]) -> StandardVersion:
    """A draft copy of a ruleset, with named tolerances changed."""
    draft = StandardVersion(
        standard_id=source.standard_id,
        edition="test draft",
        version_label=f"test-draft-{uuid.uuid4().hex[:8]}",
        status="draft",
        is_active=False,
        source_reference=source.source_reference,
    )
    db.add(draft)
    db.flush()
    rows = db.execute(
        select(RuleVersion, Rule)
        .join(Rule, Rule.id == RuleVersion.rule_id)
        .where(RuleVersion.standard_version_id == source.id)
    ).all()
    for row, rule in rows:
        definition = copy.deepcopy(row.definition or {})
        threshold = row.threshold
        replacement = factor_for.get(rule.code)
        if replacement:
            if "factor" in definition:
                definition["factor"] = replacement
            threshold = replacement
        db.add(
            RuleVersion(
                rule_id=row.rule_id,
                standard_version_id=draft.id,
                version_label=draft.version_label,
                definition=definition,
                formula=row.formula,
                threshold=threshold,
                unit=row.unit,
                applicability=copy.deepcopy(row.applicability),
                rounding_policy=row.rounding_policy,
                review_status=row.review_status,
            )
        )
    db.commit()
    return draft


def test_the_diff_names_the_changed_rule_and_the_procedures_that_read_it(
    client, tokens, db, seeded
):
    draft = _clone_as_draft(db, seeded, factor_for={"R76-ZERO-RETURN": "0.9"})

    response = client.get(
        f"{API}/rulesets/{draft.id}/diff",
        params={"against": str(seeded.id)},
        headers=tokens[SUPER_ADMIN],
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["summary"]["added"] == 0
    assert body["summary"]["removed"] == 0
    assert body["summary"]["changed"] == 1
    assert body["summary"]["change_count"] == 1
    assert body["summary"]["unchanged"] == body["ruleset"]["rule_count"] - 1
    changed = next(rule for rule in body["rules"] if rule["change"] == "changed")
    assert changed["code"] == "R76-ZERO-RETURN"
    fields = {field["field"]: field for field in changed["fields"]}
    assert fields["definition.factor"]["before"] == "0.5"
    assert fields["definition.factor"]["after"] == "0.9"
    assert fields["threshold"]["after"] == "0.9"
    assert changed["impacted_tests"] == ["T-ZR"]

    assert body["impact"]["catalogue_source"] == "baseline"
    impacted = {item["test_code"]: item for item in body["impact"]["tests"]}
    assert "T-ZR" in impacted
    assert any("R76-ZERO-RETURN" in reason for reason in impacted["T-ZR"]["reasons"])
    assert body["ruleset"]["is_active"] is False
    assert body["against"]["version_label"] == SEEDED_LABEL
    assert body["ruleset"]["rule_count"] == body["against"]["rule_count"]


def test_an_unchanged_clone_diffs_clean_and_counts_the_history_each_version_carries(
    client, tokens, case_factory, db, seeded
):
    run_lifecycle(client, tokens, case_factory())
    draft = _clone_as_draft(db, seeded, factor_for={})

    body = client.get(
        f"{API}/rulesets/{draft.id}/diff",
        params={"against": str(seeded.id)},
        headers=tokens[SUPER_ADMIN],
    ).json()
    assert body["summary"]["change_count"] == 0
    assert body["summary"]["unchanged"] == body["ruleset"]["rule_count"]
    assert body["impact"]["tests"] == []

    expected = db.execute(
        select(func.count())
        .select_from(GeneratedReport)
        .where(GeneratedReport.standard_version_id == seeded.id)
    ).scalar_one()
    assert body["impact"]["under_compared_ruleset"]["reports"] == expected
    assert body["impact"]["under_this_ruleset"] == {
        "reports": 0,
        "finalized_reports": 0,
        "open_cases": 0,
    }


def test_the_diff_refuses_an_unknown_baseline(client, tokens, db, seeded):
    draft = _clone_as_draft(db, seeded, factor_for={})
    response = client.get(
        f"{API}/rulesets/{draft.id}/diff",
        params={"against": str(uuid.uuid4())},
        headers=tokens[SUPER_ADMIN],
    )
    assert response.status_code == 404
