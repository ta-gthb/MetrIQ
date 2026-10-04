"""Standards, rulesets, test definitions and the calculation engine surface (PRD 10, 16.2)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.dependencies.permissions import require_permission
from app.models import (
    ReportTemplate,
    Rule,
    RuleVersion,
    Standard,
    StandardVersion,
    TestDefinition,
    User,
    utcnow,
)
from app.schemas.reports import (
    ReportTemplateOut,
    RuleOut,
    RuleSetOut,
    StandardOut,
)
from app.schemas.tests import TestDefinitionOut
from app.security.permissions import SUPER_ADMIN, P
from app.services import audit_service, ruleset_diff, ruleset_lifecycle
from app.services.ruleset_lifecycle import LifecycleError
from app.services.calculation_engine import ENGINE_VERSION, CalcContext, evaluate
from app.services.calculation_engine.engine import build_observation, supported_test_codes
from app.services.calculation_engine.mpe import MpeResolutionError, resolve_mpe

router = APIRouter(tags=["Standards and rules"])


def _require_super_admin(user: User, action: str) -> None:
    """Lifecycle changes to a ruleset are reserved for the platform administrator.

    Every other role - including the Laboratory Admin / Manager - keeps
    view-only access to standards and rulesets.
    """
    if user.role_code != SUPER_ADMIN:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Only the Super Admin may {action} a rule set or standard. "
                "Your role has view-only access to Standards & rules."
            ),
        )


@router.get("/standards", response_model=list[StandardOut], summary="List standards and versions")
def list_standards(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[Standard]:
    return db.execute(select(Standard).order_by(Standard.code)).scalars().all()


@router.get("/standards/{standard_id}/versions", summary="Versions of a standard")
def list_versions(
    standard_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[dict]:
    standard = db.get(Standard, standard_id)
    if standard is None:
        raise HTTPException(status_code=404, detail="Standard not found")
    return [
        {
            "id": version.id,
            "edition": version.edition,
            "version_label": version.version_label,
            "status": version.status,
            "is_active": version.is_active,
            "effective_from": version.effective_from,
            "effective_to": version.effective_to,
            "source_reference": version.source_reference,
            "notes": version.notes,
            "activated_at": version.activated_at,
        }
        for version in standard.versions
    ]


@router.get("/rules", response_model=list[RuleOut], summary="List rules")
def list_rules(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    standard_version_id: uuid.UUID | None = None,
    category: str | None = None,
    include_ruleset: bool = False,
) -> list[dict]:
    statement = (
        select(Rule, RuleVersion, StandardVersion)
        .join(RuleVersion, RuleVersion.rule_id == Rule.id)
        .join(StandardVersion, StandardVersion.id == RuleVersion.standard_version_id)
        .order_by(Rule.code, RuleVersion.created_at.desc())
    )
    if standard_version_id:
        statement = statement.where(RuleVersion.standard_version_id == standard_version_id)
    if category:
        statement = statement.where(Rule.category == category)

    seen: set[str] = set()
    output: list[dict] = []
    for rule, version, standard_version in db.execute(statement).all():
        if rule.code in seen:
            continue
        seen.add(rule.code)
        if rule.code == "R76-RULESET" and not include_ruleset:
            continue
        output.append(
            {
                "id": rule.id,
                "code": rule.code,
                "name": rule.name,
                "category": rule.category,
                "clause_reference": rule.clause_reference,
                "description": rule.description,
                "is_active": rule.is_active,
                "review_status": version.review_status,
                "version_label": version.version_label,
                "standard_version_id": standard_version.id,
                "standard_label": standard_version.version_label,
                "definition": version.definition,
            }
        )
    return output


@router.get("/rulesets", summary="List rule sets (activated standards versions)")
def list_rulesets(db: Session = Depends(get_db), user: User = Depends(get_current_active_user)) -> list[dict]:
    versions = db.execute(
        select(StandardVersion).order_by(StandardVersion.created_at.desc())
    ).scalars().all()
    output = []
    for version in versions:
        counts = db.execute(
            select(RuleVersion).where(RuleVersion.standard_version_id == version.id)
        ).scalars().all()
        ruleset_row = next((row for row in counts if (row.definition or {}).get("mpe")), None)
        gate_count = len(ruleset_lifecycle.review_gaps(db, version))
        can_activate, _gate = ruleset_lifecycle.can_activate(db, version)
        output.append(
            {
                "standard_version_id": version.id,
                "version_label": version.version_label,
                "standard_code": version.standard.code if version.standard else None,
                "edition": version.edition,
                "status": version.status,
                "is_active": version.is_active,
                "review_status": ruleset_row.review_status if ruleset_row else None,
                "source_reference": version.source_reference,
                "notes": version.notes,
                "rule_count": len(counts),
                "rules": [],
                # Governed lifecycle summary (audit items 6 and 10), so a
                # client can show what the next step is without a second call.
                "lifecycle_state": version.status,
                "activation_basis": version.activation_basis,
                "submitted_at": version.submitted_at,
                "approved_at": version.approved_at,
                "approved_by_name": version.approved_by_name,
                "scheduled_for": version.scheduled_for,
                "activated_at": version.activated_at,
                "deactivated_at": version.deactivated_at,
                "deactivation_reason": version.deactivation_reason,
                "reviewed_rule_count": len(counts) - gate_count,
                "unreviewed_rule_count": gate_count,
                "can_activate": can_activate,
            }
        )
    return output


@router.get("/rulesets/{standard_version_id}", response_model=RuleSetOut, summary="Rule set detail")
def get_ruleset(
    standard_version_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    version = db.get(StandardVersion, standard_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Ruleset not found")
    return _ruleset_payload(db, version)


@router.get(
    "/rulesets/{standard_version_id}/diff",
    summary="Rule-by-rule diff and impact against another version",
)
def diff_ruleset(
    standard_version_id: uuid.UUID,
    against: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    """What changes if this version is activated, and which procedures read it.

    Without an explicit baseline the comparison uses the active version of the
    same standard, which is the question a reviewer actually asks: what is
    different from what is running now (audit item 15).
    """
    version = db.get(StandardVersion, standard_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Ruleset not found")
    if against is not None:
        baseline = db.get(StandardVersion, against)
        if baseline is None:
            raise HTTPException(status_code=404, detail="Comparison ruleset not found")
    else:
        baseline = db.execute(
            select(StandardVersion)
            .where(
                StandardVersion.standard_id == version.standard_id,
                StandardVersion.id != version.id,
                StandardVersion.is_active.is_(True),
            )
            .order_by(StandardVersion.effective_from.desc())
        ).scalars().first()
        if baseline is None:
            raise HTTPException(
                status_code=422,
                detail="There is no other version of this standard to compare with; pass ?against=<id>.",
            )
    return ruleset_diff.diff_rulesets(db, version, baseline)


def _ruleset_payload(db: Session, version: StandardVersion) -> dict:
    rule_versions = db.execute(
        select(RuleVersion, Rule)
        .join(Rule, Rule.id == RuleVersion.rule_id)
        .where(RuleVersion.standard_version_id == version.id)
        .order_by(Rule.category, Rule.code)
    ).all()
    ruleset_row = next((row for row, _rule in rule_versions if (row.definition or {}).get("mpe")), None)
    can_activate, gate = ruleset_lifecycle.can_activate(db, version)
    return {
        "standard_version_id": version.id,
        "version_label": version.version_label,
        "standard_code": version.standard.code if version.standard else None,
        "edition": version.edition,
        "status": version.status,
        "is_active": version.is_active,
        "review_status": ruleset_row.review_status if ruleset_row else None,
        "source_reference": version.source_reference,
        "notes": version.notes,
        "rule_count": len(rule_versions),
        "rules": [
            {
                "rule_version_id": row.id,
                "code": rule.code,
                "name": rule.name,
                "category": rule.category,
                "clause_reference": rule.clause_reference,
                "threshold": row.threshold,
                "unit": row.unit,
                "formula": row.formula,
                "review_status": row.review_status,
                "definition": row.definition,
                "fingerprint": ruleset_lifecycle.rule_fingerprint(rule, row),
            }
            for row, rule in rule_versions
        ],
        "lifecycle_state": version.status,
        "activation_basis": version.activation_basis,
        "submitted_at": version.submitted_at,
        "approved_at": version.approved_at,
        "approved_by_name": version.approved_by_name,
        "approval_note": version.approval_note,
        "scheduled_for": version.scheduled_for,
        "activated_at": version.activated_at,
        "deactivated_at": version.deactivated_at,
        "deactivation_reason": version.deactivation_reason,
        "approved_fingerprint": version.approved_fingerprint,
        "can_activate": can_activate,
        "review_gate": gate,
    }


# ------------------------------------------------ governed rule lifecycle ---
# Items 6 and 10 of the audit: draft -> technical review -> approved ->
# scheduled -> active, with the reviewer recorded and activation refused
# while any rule lacks a current metrology sign-off.

class SubmitReviewRequest(BaseModel):
    note: str | None = None


class RuleReviewRequest(BaseModel):
    decision: str = Field(description="approved, rejected or needs_changes")
    reviewer_name: str | None = None
    reviewer_credentials: str | None = None
    source_revision: str | None = None
    change_note: str | None = None
    boundary_cases_passed: bool | None = None
    boundary_case_reference: str | None = None


class RulesetReviewRequest(BaseModel):
    reviewer_name: str | None = None
    reviewer_credentials: str | None = None
    source_revision: str | None = None
    change_note: str | None = None


class ScheduleRequest(BaseModel):
    effective_from: date
    reason: str | None = None


class DeactivateRequest(BaseModel):
    reason: str


class ActivateRequest(BaseModel):
    reason: str | None = None


def _ruleset_or_404(db: Session, standard_version_id: uuid.UUID) -> StandardVersion:
    version = db.get(StandardVersion, standard_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Ruleset not found")
    return version


def _lifecycle_error(exc: LifecycleError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"message": exc.message, **(exc.detail or {})},
    )


@router.get("/rulesets/{standard_version_id}/review-package", summary="Rule review package for a metrology reviewer")
def get_review_package(
    standard_version_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.RULES_VIEW)),
) -> dict:
    """Everything a qualified reviewer needs, and the gate that decides activation."""
    version = _ruleset_or_404(db, standard_version_id)
    return ruleset_lifecycle.review_package(db, version)


@router.post("/rulesets/{standard_version_id}/submit-review", response_model=RuleSetOut, summary="Submit a draft ruleset for technical review")
def submit_ruleset_for_review(
    standard_version_id: uuid.UUID,
    payload: SubmitReviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.RULES_MANAGE)),
) -> dict:
    _require_super_admin(user, "submit a ruleset for review")
    version = _ruleset_or_404(db, standard_version_id)
    try:
        ruleset_lifecycle.submit_for_review(db, version, actor=user, note=payload.note)
    except LifecycleError as exc:
        raise _lifecycle_error(exc) from exc
    audit_service.record(
        db, event_type="RULE_SUBMIT", entity_type="standard_version", entity_id=version.id,
        actor=user, reason=payload.note,
        after={"version_label": version.version_label, "status": version.status},
    )
    db.commit()
    return _ruleset_payload(db, version)


@router.post("/rulesets/{standard_version_id}/rules/{rule_version_id}/review", summary="Record a metrology review of one rule")
def review_rule_version(
    standard_version_id: uuid.UUID,
    rule_version_id: uuid.UUID,
    payload: RuleReviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.RULES_REVIEW)),
) -> dict:
    """A named reviewer validates one rule against the controlled standard (item 6)."""
    _require_super_admin(user, "review a ruleset rule")
    version = _ruleset_or_404(db, standard_version_id)
    rule_version = db.get(RuleVersion, rule_version_id)
    if rule_version is None or rule_version.standard_version_id != version.id:
        raise HTTPException(status_code=404, detail="Rule version not found in this ruleset")
    try:
        review = ruleset_lifecycle.review_rule(
            db, version, rule_version,
            actor=user,
            decision=payload.decision,
            reviewer_name=payload.reviewer_name or "",
            reviewer_credentials=payload.reviewer_credentials,
            source_revision=payload.source_revision,
            change_note=payload.change_note,
            boundary_cases_passed=payload.boundary_cases_passed,
            boundary_case_reference=payload.boundary_case_reference,
        )
    except LifecycleError as exc:
        raise _lifecycle_error(exc) from exc
    rule = db.get(Rule, rule_version.rule_id)
    audit_service.record(
        db, event_type="RULE_REVIEW", entity_type="rule_version", entity_id=rule_version.id,
        actor=user, reason=payload.change_note,
        after={
            "rule_code": rule.code if rule else None,
            "decision": payload.decision,
            "reviewer": review.reviewer_name,
            "source_revision": review.source_revision,
            "fingerprint": review.fingerprint,
        },
    )
    db.commit()
    return {
        "rule_version_id": rule_version.id,
        "rule_code": rule.code if rule else None,
        "decision": review.decision,
        "review_status": rule_version.review_status,
        "reviewer_name": review.reviewer_name,
        "reviewer_credentials": review.reviewer_credentials,
        "source_revision": review.source_revision,
        "clause_reference": review.clause_reference,
        "boundary_cases_passed": review.boundary_cases_passed,
        "reviewed_at": review.reviewed_at,
        "fingerprint": review.fingerprint,
        "can_activate": ruleset_lifecycle.can_activate(db, version)[0],
    }


@router.post("/rulesets/{standard_version_id}/approve", response_model=RuleSetOut, summary="Approve a reviewed ruleset")
def approve_ruleset(
    standard_version_id: uuid.UUID,
    payload: RulesetReviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.RULES_APPROVE)),
) -> dict:
    _require_super_admin(user, "approve a ruleset")
    version = _ruleset_or_404(db, standard_version_id)
    try:
        ruleset_lifecycle.approve_ruleset(
            db, version,
            actor=user,
            reviewer_name=payload.reviewer_name or "",
            reviewer_credentials=payload.reviewer_credentials,
            source_revision=payload.source_revision,
            change_note=payload.change_note,
        )
    except LifecycleError as exc:
        raise _lifecycle_error(exc) from exc
    audit_service.record(
        db, event_type="RULE_APPROVAL", entity_type="standard_version", entity_id=version.id,
        actor=user, reason=payload.change_note,
        after={
            "version_label": version.version_label,
            "approved_by": version.approved_by_name,
            "fingerprint": version.approved_fingerprint,
        },
    )
    db.commit()
    return _ruleset_payload(db, version)


@router.post("/rulesets/{standard_version_id}/reject", response_model=RuleSetOut, summary="Reject a ruleset and send it back to draft")
def reject_ruleset(
    standard_version_id: uuid.UUID,
    payload: RulesetReviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.RULES_APPROVE)),
) -> dict:
    _require_super_admin(user, "reject a ruleset")
    version = _ruleset_or_404(db, standard_version_id)
    if not (payload.change_note or "").strip():
        raise HTTPException(status_code=422, detail="A rejection needs a change note.")
    try:
        ruleset_lifecycle.reject_ruleset(
            db, version, actor=user,
            reviewer_name=payload.reviewer_name or "",
            reviewer_credentials=payload.reviewer_credentials,
            source_revision=payload.source_revision,
            change_note=payload.change_note,
        )
    except LifecycleError as exc:
        raise _lifecycle_error(exc) from exc
    audit_service.record(
        db, event_type="RULE_REJECTION", entity_type="standard_version", entity_id=version.id,
        actor=user, reason=payload.change_note,
        after={"version_label": version.version_label, "status": version.status},
    )
    db.commit()
    return _ruleset_payload(db, version)


@router.post("/rulesets/{standard_version_id}/schedule", response_model=RuleSetOut, summary="Schedule an approved ruleset for an effective date")
def schedule_ruleset(
    standard_version_id: uuid.UUID,
    payload: ScheduleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.RULES_APPROVE)),
) -> dict:
    _require_super_admin(user, "schedule")
    version = _ruleset_or_404(db, standard_version_id)
    try:
        ruleset_lifecycle.schedule(
            db, version, effective_from=payload.effective_from, actor=user, reason=payload.reason
        )
    except LifecycleError as exc:
        raise _lifecycle_error(exc) from exc
    audit_service.record(
        db, event_type="RULE_SCHEDULE", entity_type="standard_version", entity_id=version.id,
        actor=user, reason=payload.reason,
        after={"version_label": version.version_label, "effective_from": str(payload.effective_from)},
    )
    db.commit()
    return _ruleset_payload(db, version)


@router.post("/rulesets/{standard_version_id}/activate", response_model=RuleSetOut, summary="Activate a populated rule set")
def activate_ruleset(
    standard_version_id: uuid.UUID,
    payload: ActivateRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.RULES_APPROVE)),
) -> dict:
    """Activate a populated ruleset directly by Super Admin decision."""
    _require_super_admin(user, "activate")
    version = _ruleset_or_404(db, standard_version_id)
    try:
        ruleset_lifecycle.activate(
            db,
            version,
            actor=user,
            reason=payload.reason if payload else None,
            direct_admin=True,
        )
    except LifecycleError as exc:
        raise _lifecycle_error(exc) from exc
    audit_service.record(
        db, event_type="RULE_ACTIVATION", entity_type="standard_version", entity_id=version.id,
        actor=user, reason=payload.reason if payload else None,
        after={
            "version_label": version.version_label,
            "activated_at": str(version.activated_at),
            "activation_basis": version.activation_basis,
            "fingerprint": version.approved_fingerprint,
        },
    )
    db.commit()
    return _ruleset_payload(db, version)


@router.post("/rulesets/{standard_version_id}/deactivate", response_model=RuleSetOut, summary="Deactivate the active ruleset, with a reason")
def deactivate_ruleset(
    standard_version_id: uuid.UUID,
    payload: DeactivateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.RULES_APPROVE)),
) -> dict:
    _require_super_admin(user, "deactivate")
    version = _ruleset_or_404(db, standard_version_id)
    try:
        ruleset_lifecycle.deactivate(db, version, actor=user, reason=payload.reason)
    except LifecycleError as exc:
        raise _lifecycle_error(exc) from exc
    audit_service.record(
        db, event_type="RULE_DEACTIVATION", entity_type="standard_version", entity_id=version.id,
        actor=user, reason=payload.reason,
        after={"version_label": version.version_label, "deactivated_at": str(version.deactivated_at)},
    )
    db.commit()
    return _ruleset_payload(db, version)


@router.get("/test-definitions", response_model=list[TestDefinitionOut], summary="Test catalogue")
def list_test_definitions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    standard_version_id: uuid.UUID | None = None,
    include_inactive: bool = False,
) -> list[TestDefinition]:
    statement = select(TestDefinition).order_by(TestDefinition.sequence_no)
    if standard_version_id:
        statement = statement.where(TestDefinition.standard_version_id == standard_version_id)
    if not include_inactive:
        statement = statement.where(TestDefinition.is_active.is_(True))
    return db.execute(statement).scalars().all()


@router.get("/report-templates", response_model=list[ReportTemplateOut], summary="Report templates")
def list_report_templates(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[ReportTemplate]:
    return db.execute(select(ReportTemplate).order_by(ReportTemplate.code)).scalars().all()


class ReportSectionSpec(BaseModel):
    number: str
    required: bool


class ReportSectionUpdateRequest(BaseModel):
    sections: list[ReportSectionSpec] = Field(default_factory=list, max_length=100)


@router.put(
    "/report-templates/{template_id}/sections",
    summary="Set which report sections are required and which are optional",
)
def update_report_sections(
    template_id: uuid.UUID,
    payload: ReportSectionUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.RULES_MANAGE)),
) -> dict:
    _require_super_admin(user, "edit report template sections")
    template = db.get(ReportTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Report template not found")
    versions = sorted(template.versions, key=lambda item: item.version_no)
    if not versions:
        raise HTTPException(status_code=409, detail="This template has no versions to edit.")
    version = next((item for item in reversed(versions) if item.is_active), versions[-1])
    section_map = dict(version.section_map or {})
    sections = [dict(section) for section in (section_map.get("sections") or [])]
    known = {str(section.get("number")) for section in sections}
    unknown = sorted({spec.number for spec in payload.sections} - known)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="Unknown section number(s): " + ", ".join(unknown),
        )
    wanted = {spec.number: spec.required for spec in payload.sections}
    before = [
        {"number": str(section.get("number")), "required": bool(section.get("required"))}
        for section in sections
    ]
    for section in sections:
        number = str(section.get("number"))
        if number in wanted:
            section["required"] = bool(wanted[number])
    section_map["sections"] = sections
    version.section_map = section_map
    after = [
        {"number": str(section.get("number")), "required": bool(section.get("required"))}
        for section in sections
    ]
    audit_service.record(
        db, event_type="EDIT", entity_type="report_template_version", entity_id=version.id,
        actor=user, field_changed="sections", before={"sections": before},
        after={"sections": after},
    )
    db.commit()
    return {
        "template_id": str(template.id),
        "version_id": str(version.id),
        "version_label": version.version_label,
        "sections": after,
    }


# --------------------------------------------------------------- engine surface
class MpeQuery(BaseModel):
    instrument_class: str
    load: Decimal
    e: Decimal = Field(gt=0)
    stage: str = "verification"
    standard_version_id: uuid.UUID | None = None


class CalcPreviewRequest(BaseModel):
    test_code: str
    instrument: dict
    observations: list[dict]
    stage: str = "verification"
    standard_version_id: uuid.UUID | None = None


@router.get("/calculations/engine", summary="Calculation engine capabilities and version")
def engine_info(user: User = Depends(get_current_active_user)) -> dict:
    from app.services.calculation_engine.engine import ENGINE_VERSION as version
    from app.services.compliance_engine import COMPARATORS
    from app.utils.decimals import ROUNDING_POLICY_EXACT, ROUNDING_POLICY_R76

    return {
        "engine_version": version,
        "supported_test_codes": supported_test_codes(),
        "comparators": sorted(COMPARATORS),
        "rounding_policies": [ROUNDING_POLICY_EXACT, ROUNDING_POLICY_R76],
        "deterministic": True,
        "uses_ai": False,
        "arithmetic": "decimal.Decimal (28 significant digits)",
    }


@router.post("/calculations/mpe", summary="Resolve the applicable MPE for a load")
def resolve_mpe_endpoint(
    payload: MpeQuery,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    from app.services.metrology_service import active_standard_version, ruleset_for_standard_version

    version_id = payload.standard_version_id
    if version_id is None:
        active = active_standard_version(db)
        version_id = active.id if active else None
    ruleset, label = ruleset_for_standard_version(db, version_id)
    if not ruleset:
        # The case may name a version whose rule set is no longer the active
        # one (or no version at all). Fall back to the active rule set so the
        # lookup keeps working instead of reporting that no bands exist.
        active = active_standard_version(db)
        if active is not None and str(active.id) != str(version_id):
            ruleset, label = ruleset_for_standard_version(db, active.id)
    if not ruleset:
        raise HTTPException(
            status_code=422,
            detail=(
                "No active rule set is available to resolve MPE bands. An administrator "
                "can activate one in Administration > Standards & rules."
            ),
        )
    try:
        resolution = resolve_mpe(
            ruleset=ruleset,
            instrument_class=payload.instrument_class,
            load=payload.load,
            e=payload.e,
            stage=payload.stage,
            rule_version=label,
        )
    except MpeResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return resolution.as_dict()


@router.post("/calculations/preview", summary="Run the engine on an ad-hoc payload")
def preview_calculation(
    payload: CalcPreviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    """Deterministic preview used by the UI's 'How calculated' panel and by tests."""
    from app.services.compliance_engine import evaluate as evaluate_compliance
    from app.services.metrology_service import active_standard_version, ruleset_for_standard_version

    version_id = payload.standard_version_id
    if version_id is None:
        active = active_standard_version(db)
        version_id = active.id if active else None
    ruleset, label = ruleset_for_standard_version(db, version_id)
    instrument = dict(payload.instrument)
    from app.utils.decimals import to_decimal

    for key in ("e", "d", "max_capacity", "min_capacity"):
        if key in instrument:
            instrument[key] = to_decimal(instrument[key], field=key)
    context = CalcContext(
        test_code=payload.test_code,
        instrument=instrument,
        observations=[build_observation(row, index) for index, row in enumerate(payload.observations, 1)],
        ruleset=ruleset,
        test_definition={},
        stage=payload.stage,
        rule_version=label,
    )
    outcome = evaluate(context)
    decision = evaluate_compliance(outcome)
    return {
        "engine_version": ENGINE_VERSION,
        "ruleset_label": label,
        "calculation": outcome.as_dict(),
        "compliance": decision.as_dict(),
    }
