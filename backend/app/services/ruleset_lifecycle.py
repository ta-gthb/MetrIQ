"""Activation and deactivation operations for versioned rulesets."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Rule, RuleVersion, StandardVersion, TestDefinition, User, utcnow


class LifecycleError(Exception):
    """A ruleset operation was refused."""

    def __init__(self, message: str, *, status_code: int = 409, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail


def _canonical(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def rule_fingerprint(rule: Rule | None, version: RuleVersion) -> str:
    """Digest the rule fields that affect a calculation."""
    material = {
        "code": rule.code if rule else None,
        "clause_reference": rule.clause_reference if rule else None,
        "version_label": version.version_label,
        "definition": version.definition,
        "formula": version.formula,
        "threshold": version.threshold,
        "unit": version.unit,
        "applicability": version.applicability,
        "rounding_policy": version.rounding_policy,
    }
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


def ruleset_fingerprint(rows: list[tuple[RuleVersion, Rule]]) -> str:
    """Digest a whole ruleset independent of row ordering."""
    material = sorted(
        (
            {
                "code": rule.code,
                "clause_reference": rule.clause_reference,
                "version_label": version.version_label,
                "fingerprint": rule_fingerprint(rule, version),
            }
            for version, rule in rows
        ),
        key=lambda item: item["code"],
    )
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


def ruleset_rows(db: Session, version: StandardVersion) -> list[tuple[RuleVersion, Rule]]:
    return list(
        db.execute(
            select(RuleVersion, Rule)
            .join(Rule, Rule.id == RuleVersion.rule_id)
            .where(RuleVersion.standard_version_id == version.id)
            .order_by(Rule.code)
        ).all()
    )


def activate(
    db: Session,
    version: StandardVersion,
    *,
    actor: User,
    reason: str | None = None,
) -> StandardVersion:
    """Activate a populated version and deactivate any active sibling."""
    if not ruleset_rows(db, version):
        raise LifecycleError(
            "This ruleset has no rule definitions. Add its rules before activation.",
            status_code=422,
            detail={"rule_count": 0},
        )
    if version.is_active:
        return version

    now = utcnow()
    siblings = db.execute(
        select(StandardVersion).where(StandardVersion.standard_id == version.standard_id)
    ).scalars().all()
    for sibling in siblings:
        if sibling.id != version.id and sibling.is_active:
            sibling.is_active = False
            sibling.status = "inactive"
            sibling.deactivated_at = now
            sibling.deactivated_by = actor.id
            sibling.deactivation_reason = reason or "superseded by a newly activated version"
            db.add(sibling)

    version.is_active = True
    version.status = "active"
    version.activated_at = now
    version.activated_by = actor.id
    definitions = db.execute(
        select(TestDefinition).where(
            TestDefinition.standard_version_id == version.id,
        )
    ).scalars().all()
    for definition in definitions:
        definition.is_active = True
        db.add(definition)
    if reason:
        version.notes = (version.notes + "\n" if version.notes else "") + f"[activated] {reason}"
    db.add(version)
    return version


def deactivate(
    db: Session, version: StandardVersion, *, actor: User, reason: str
) -> StandardVersion:
    """Deactivate a version so it is not selected for new evaluations."""
    if not version.is_active:
        raise LifecycleError("This ruleset is not active.", status_code=409)
    if not (reason or "").strip():
        raise LifecycleError("A deactivation reason is required.", status_code=422)
    version.is_active = False
    version.status = "inactive"
    version.deactivated_at = utcnow()
    version.deactivated_by = actor.id
    version.deactivation_reason = reason.strip()
    db.add(version)
    return version
