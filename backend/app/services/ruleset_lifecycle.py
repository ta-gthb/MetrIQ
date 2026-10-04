"""Governed lifecycle for standards versions and their rules (audit items 6, 10).

Versioning alone is not governance. A ruleset that decides whether an
instrument passes verification has to move through a controlled sequence -
drafted, reviewed by a competent metrology authority, approved, scheduled for an
effective date, activated - and every step has to leave a record. This module is
the single place that knows the sequence, so the API layer cannot invent a
transition that skips the review.

Two ideas do the real work:

* **Review records are content-bound.** A ``RuleReview`` carries a fingerprint of
  the exact content that was reviewed. Editing a band after sign-off leaves the
  fingerprint unchanged in the database and therefore no longer matching, and
  the rule reverts to "not reviewed" without anyone having to remember to clear
  a flag.
* **Activation is gated on those fingerprints.** A version cannot become active
  while any of its rules lacks a current approval, and the fingerprint captured
  at approval is re-checked at activation so the two cannot drift apart.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Rule, RuleReview, RuleVersion, StandardVersion, TestDefinition, User, utcnow

# draft -> under_review -> approved -> scheduled -> active. `superseded` is where
# a previously active version lands when a newer one takes over; `retired` is a
# deliberate withdrawal.
LIFECYCLE_STATES = (
    "draft",
    "under_review",
    "approved",
    "scheduled",
    "active",
    "superseded",
    "retired",
)

# A predecessor that anything else may follow. `active` is reachable from
# `approved` (effective immediately) and from `scheduled` (effective on its date).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"under_review"}),
    "under_review": frozenset({"approved", "draft"}),
    "approved": frozenset({"scheduled", "active", "draft"}),
    "scheduled": frozenset({"active", "approved"}),
    # `active` -> `under_review` is the recall path: a version activated
    # provisionally by the bootstrap is withdrawn for the metrology review it
    # never had, and comes back through approval as a domain-reviewed set.
    "active": frozenset({"superseded", "retired", "under_review"}),
    "superseded": frozenset(),
    "retired": frozenset(),
}

REVIEW_DECISIONS = ("approved", "rejected", "needs_changes")

# How a version came to be active. "provisional" is only ever set by the
# development/demo bootstrap; direct Super Admin activation is recorded separately.
BASIS_DOMAIN_REVIEW = "domain_review"
BASIS_PROVISIONAL = "provisional"
BASIS_SUPER_ADMIN = "super_admin_direct"
PROPOSED_TEST_CODES = frozenset({"T-TILT", "T-TARE", "T-WARMUP", "T-VOLT", "T-EMC", "T-DAMP"})


class LifecycleError(Exception):
    """A transition was refused. ``status_code`` lets the router answer with it."""

    def __init__(self, message: str, *, status_code: int = 409, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail


def _canonical(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def rule_fingerprint(rule: Rule | None, version: RuleVersion) -> str:
    """A stable digest of everything in a rule that changes what it decides."""
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
    """A digest of a whole ruleset, order-independent and content-bound."""
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


def _latest_review(db: Session, *, rule_version_id: uuid.UUID) -> RuleReview | None:
    return db.execute(
        select(RuleReview)
        .where(RuleReview.rule_version_id == rule_version_id)
        .order_by(RuleReview.reviewed_at.desc(), RuleReview.created_at.desc())
    ).scalars().first()


def _ruleset_review(db: Session, version: StandardVersion) -> RuleReview | None:
    return db.execute(
        select(RuleReview)
        .where(RuleReview.standard_version_id == version.id, RuleReview.scope == "ruleset")
        .order_by(RuleReview.reviewed_at.desc(), RuleReview.created_at.desc())
    ).scalars().first()


def rule_review_state(db: Session, version: RuleVersion, rule: Rule) -> dict:
    """Whether a rule currently carries a valid approval, and if not, why not."""
    current = rule_fingerprint(rule, version)
    review = _latest_review(db, rule_version_id=version.id)
    if review is None:
        return {"approved": False, "reason": "no review recorded", "review": None}
    if review.decision != "approved":
        return {"approved": False, "reason": f"review decision is '{review.decision}'", "review": review}
    if review.fingerprint and review.fingerprint != current:
        return {"approved": False, "reason": "rule changed after it was reviewed", "review": review}
    return {"approved": True, "reason": None, "review": review}


def review_gaps(db: Session, version: StandardVersion) -> list[dict]:
    """Every rule in the version that would block activation, with the reason."""
    gaps: list[dict] = []
    for rule_version, rule in ruleset_rows(db, version):
        state = rule_review_state(db, rule_version, rule)
        if state["approved"]:
            continue
        gaps.append(
            {
                "rule_version_id": str(rule_version.id),
                "rule_code": rule.code,
                "rule_name": rule.name,
                "clause_reference": rule.clause_reference,
                "reason": state["reason"],
            }
        )
    return gaps


def can_activate(db: Session, version: StandardVersion) -> tuple[bool, dict]:
    """The activation gate: a complete, current, domain-approved ruleset only."""
    rows = ruleset_rows(db, version)
    gaps = review_gaps(db, version)
    ruleset_review = _ruleset_review(db, version)
    current = ruleset_fingerprint(rows)
    reasons: dict = {
        "rule_count": len(rows),
        "unreviewed_rules": gaps,
        "ruleset_review": None,
        "fingerprint": current,
    }
    if not rows:
        return False, {**reasons, "summary": "the version has no rules to activate"}
    if gaps:
        return False, {
            **reasons,
            "summary": (
                f"{len(gaps)} of {len(rows)} rules have no current metrology review"
            ),
        }
    if ruleset_review is None or ruleset_review.decision != "approved":
        return False, {**reasons, "summary": "the rule set as a whole has not been approved"}
    if ruleset_review.fingerprint and ruleset_review.fingerprint != current:
        return False, {
            **reasons,
            "summary": "the rule set changed after it was approved; review it again",
        }
    return True, reasons


def _transition(version: StandardVersion, target: str) -> None:
    current = version.status or "draft"
    if target == current:
        return
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise LifecycleError(
            f"A ruleset in state '{current}' cannot move to '{target}'.",
            status_code=409,
            detail={"state": current, "requested": target, "allowed": sorted(allowed)},
        )
    version.status = target


def _reviewer_fields(user: User | None, name: str, credentials: str | None) -> tuple[str, str | None, uuid.UUID | None]:
    if name and name.strip():
        return name.strip(), (credentials or None), (user.id if user else None)
    if user is not None:
        return user.full_name, (credentials or user.designation), user.id
    raise LifecycleError("A named reviewer is required.", status_code=422)


def submit_for_review(
    db: Session, version: StandardVersion, *, actor: User, note: str | None = None
) -> StandardVersion:
    _transition(version, "under_review")
    version.submitted_at = utcnow()
    version.submitted_by = actor.id
    if note:
        version.notes = (version.notes + "\n" if version.notes else "") + f"[submitted for review] {note}"
    db.add(version)
    return version


def review_rule(
    db: Session,
    version: StandardVersion,
    rule_version: RuleVersion,
    *,
    actor: User,
    decision: str,
    reviewer_name: str = "",
    reviewer_credentials: str | None = None,
    source_revision: str | None = None,
    change_note: str | None = None,
    boundary_cases_passed: bool | None = None,
    boundary_case_reference: str | None = None,
) -> RuleReview:
    """Record a metrology reviewer's decision on one rule (audit item 6)."""
    if decision not in REVIEW_DECISIONS:
        raise LifecycleError(
            f"Unknown review decision '{decision}'.", status_code=422
        )
    if decision == "approved" and boundary_cases_passed is False:
        raise LifecycleError(
            "A rule cannot be approved while its boundary cases fail.",
            status_code=422,
        )
    rule = db.get(Rule, rule_version.rule_id)
    resolved_name, resolved_credentials, reviewer_id = _reviewer_fields(
        actor, reviewer_name, reviewer_credentials
    )
    review = RuleReview(
        standard_version_id=version.id,
        rule_version_id=rule_version.id,
        scope="rule",
        decision=decision,
        reviewer_name=resolved_name,
        reviewer_credentials=resolved_credentials,
        reviewer_user_id=reviewer_id,
        source_revision=source_revision,
        clause_reference=(rule.clause_reference if rule else None),
        boundary_cases_passed=boundary_cases_passed,
        boundary_case_reference=boundary_case_reference,
        change_note=change_note,
        reviewed_at=utcnow(),
        fingerprint=rule_fingerprint(rule, rule_version),
    )
    db.add(review)
    rule_version.review_status = {
        "approved": "approved",
        "rejected": "rejected",
        "needs_changes": "needs_changes",
    }[decision]
    rule_version.reviewed_by = resolved_name
    rule_version.reviewed_at = review.reviewed_at
    db.add(rule_version)
    return review


def approve_ruleset(
    db: Session,
    version: StandardVersion,
    *,
    actor: User,
    reviewer_name: str = "",
    reviewer_credentials: str | None = None,
    source_revision: str | None = None,
    change_note: str | None = None,
) -> RuleReview:
    """Approve the ruleset as a whole. Refuses while any rule is unreviewed."""
    _transition(version, "approved")
    gaps = review_gaps(db, version)
    if gaps:
        raise LifecycleError(
            "The rule set cannot be approved while rules have no current metrology review.",
            status_code=422,
            detail={"unreviewed_rules": gaps},
        )
    resolved_name, resolved_credentials, reviewer_id = _reviewer_fields(
        actor, reviewer_name, reviewer_credentials
    )
    rows = ruleset_rows(db, version)
    now = utcnow()
    review = RuleReview(
        standard_version_id=version.id,
        rule_version_id=None,
        scope="ruleset",
        decision="approved",
        reviewer_name=resolved_name,
        reviewer_credentials=resolved_credentials,
        reviewer_user_id=reviewer_id,
        source_revision=source_revision or version.source_reference,
        clause_reference=None,
        boundary_cases_passed=True,
        change_note=change_note,
        reviewed_at=now,
        fingerprint=ruleset_fingerprint(rows),
    )
    db.add(review)
    version.approved_at = now
    version.approved_by = actor.id
    version.approved_by_name = resolved_name
    version.approval_note = change_note
    version.approved_fingerprint = review.fingerprint
    version.review_reference = source_revision or version.source_reference
    db.add(version)
    return review


def reject_ruleset(
    db: Session,
    version: StandardVersion,
    *,
    actor: User,
    reviewer_name: str = "",
    reviewer_credentials: str | None = None,
    source_revision: str | None = None,
    change_note: str | None = None,
) -> RuleReview:
    """Send a ruleset back to draft with a recorded reason."""
    if (version.status or "draft") not in {"under_review", "approved", "scheduled"}:
        raise LifecycleError(
            f"A ruleset in state '{version.status}' cannot be rejected.", status_code=409
        )
    resolved_name, resolved_credentials, reviewer_id = _reviewer_fields(
        actor, reviewer_name, reviewer_credentials
    )
    rows = ruleset_rows(db, version)
    review = RuleReview(
        standard_version_id=version.id,
        rule_version_id=None,
        scope="ruleset",
        decision="rejected",
        reviewer_name=resolved_name,
        reviewer_credentials=resolved_credentials,
        reviewer_user_id=reviewer_id,
        source_revision=source_revision or version.source_reference,
        change_note=change_note,
        reviewed_at=utcnow(),
        fingerprint=ruleset_fingerprint(rows),
    )
    db.add(review)
    version.status = "draft"
    version.approved_at = None
    version.approved_by = None
    version.approved_by_name = None
    version.approved_fingerprint = None
    db.add(version)
    return review


def schedule(
    db: Session, version: StandardVersion, *, effective_from: date, actor: User, reason: str | None = None
) -> StandardVersion:
    _transition(version, "scheduled")
    version.scheduled_for = effective_from
    version.scheduled_at = utcnow()
    version.effective_from = effective_from
    if reason:
        version.approval_note = (version.approval_note or "") + f" [scheduled] {reason}"
    db.add(version)
    return version


def activate(
    db: Session,
    version: StandardVersion,
    *,
    actor: User | None = None,
    basis: str = BASIS_DOMAIN_REVIEW,
    reason: str | None = None,
    force_provisional: bool = False,
    direct_admin: bool = False,
) -> StandardVersion:
    """Make a populated version active, optionally by direct Super Admin action.

    Deactivating the siblings happens in the same transaction so there is never
    a moment with two active versions of one standard.
    """
    current = version.status or "draft"
    if not direct_admin and current not in {"approved", "scheduled", "draft", "active"}:
        raise LifecycleError(
            f"A ruleset in state '{current}' cannot be activated.", status_code=409
        )

    rows = ruleset_rows(db, version)
    if direct_admin:
        if not rows:
            raise LifecycleError(
                "This ruleset has no rule definitions. Add its rules before activation.",
                status_code=422,
                detail={"rule_count": 0},
            )
        allowed, _gate = can_activate(db, version)
        basis = BASIS_DOMAIN_REVIEW if allowed else BASIS_SUPER_ADMIN

    if force_provisional and basis != BASIS_PROVISIONAL:
        raise LifecycleError("Only the bootstrap may force a provisional activation.", status_code=409)

    if direct_admin:
        pass
    elif basis == BASIS_PROVISIONAL:
        if not force_provisional:
            raise LifecycleError(
                "A provisional activation must be requested explicitly.", status_code=409
            )
        gate: dict = {"summary": "provisional activation by the bootstrap", "rule_count": len(rows)}
    else:
        allowed, gate = can_activate(db, version)
        if not allowed:
            raise LifecycleError(
                f"This rule set cannot be activated: {gate.get('summary')}.",
                status_code=422,
                detail=gate,
            )

    now = utcnow()
    siblings = db.execute(
        select(StandardVersion).where(StandardVersion.standard_id == version.standard_id)
    ).scalars().all()
    for sibling in siblings:
        if sibling.id == version.id:
            continue
        if sibling.is_active:
            sibling.is_active = False
            sibling.status = "superseded"
            sibling.deactivated_at = now
            sibling.deactivated_by = actor.id if actor else None
            sibling.deactivation_reason = reason or "superseded by a newly activated version"
            db.add(sibling)

    version.is_active = True
    version.status = "active"
    version.activated_at = now
    version.activated_by = actor.id if actor else None
    version.activation_basis = basis
    if basis == BASIS_DOMAIN_REVIEW:
        version.approved_fingerprint = ruleset_fingerprint(rows)
    elif basis == BASIS_SUPER_ADMIN:
        version.approved_fingerprint = None
    if basis in {BASIS_DOMAIN_REVIEW, BASIS_SUPER_ADMIN}:
        # These procedures are seeded as proposals. Activation by a Super
        # Admin makes them eligible for generated evaluation plans.
        definitions = db.execute(
            select(TestDefinition).where(
                TestDefinition.standard_version_id == version.id,
                TestDefinition.test_code.in_(PROPOSED_TEST_CODES),
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
    if not version.is_active:
        raise LifecycleError("This ruleset is not active.", status_code=409)
    if not (reason or "").strip():
        raise LifecycleError("A deactivation reason is required.", status_code=422)
    version.is_active = False
    version.status = "retired"
    version.deactivated_at = utcnow()
    version.deactivated_by = actor.id
    version.deactivation_reason = reason.strip()
    db.add(version)
    return version


def review_package(db: Session, version: StandardVersion) -> dict:
    """The rule-review package handed to a metrology reviewer (audit item 6).

    One row per rule with the clause it implements, the formula, the limit and
    the current review state - enough for a competent reviewer to validate the
    rule against the controlled standard without reading the application source.
    """
    rules = []
    for rule_version, rule in ruleset_rows(db, version):
        state = rule_review_state(db, rule_version, rule)
        review = state["review"]
        rules.append(
            {
                "rule_code": rule.code,
                "rule_name": rule.name,
                "category": rule.category,
                "clause_reference": rule.clause_reference,
                "formula": rule_version.formula,
                "threshold": rule_version.threshold,
                "unit": rule_version.unit,
                "definition": rule_version.definition,
                "applicability": rule_version.applicability,
                "rounding_policy": rule_version.rounding_policy,
                "fingerprint": rule_fingerprint(rule, rule_version),
                "review_status": "approved" if state["approved"] else "pending",
                "pending_reason": state["reason"],
                "reviewed_by": review.reviewer_name if review else None,
                "reviewed_at": review.reviewed_at.isoformat() if review and review.reviewed_at else None,
                "source_revision": review.source_revision if review else None,
                "change_note": review.change_note if review else None,
                "boundary_cases_passed": review.boundary_cases_passed if review else None,
                "boundary_case_reference": review.boundary_case_reference if review else None,
            }
        )
    approved, gate = can_activate(db, version)
    return {
        "standard_version_id": str(version.id),
        "version_label": version.version_label,
        "standard_code": version.standard.code if version.standard else None,
        "edition": version.edition,
        "status": version.status,
        "is_active": version.is_active,
        "activation_basis": version.activation_basis,
        "source_reference": version.source_reference,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rule_count": len(rules),
        "reviewed_rule_count": sum(1 for row in rules if row["review_status"] == "approved"),
        "can_activate": approved,
        "activation_gate": gate,
        "rules": rules,
    }
