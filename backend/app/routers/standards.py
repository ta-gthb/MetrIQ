"""Standards, rulesets, test definitions and the calculation engine surface (PRD 10, 16.2)."""

from __future__ import annotations

import uuid
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
from app.security.permissions import P
from app.services import audit_service
from app.services.calculation_engine import ENGINE_VERSION, CalcContext, evaluate
from app.services.calculation_engine.engine import build_observation, supported_test_codes
from app.services.calculation_engine.mpe import MpeResolutionError, resolve_mpe

router = APIRouter(tags=["Standards and rules"])


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
    rule_versions = db.execute(
        select(RuleVersion, Rule)
        .join(Rule, Rule.id == RuleVersion.rule_id)
        .where(RuleVersion.standard_version_id == version.id)
        .order_by(Rule.category, Rule.code)
    ).all()
    ruleset_row = next((row for row, _rule in rule_versions if (row.definition or {}).get("mpe")), None)
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
                "code": rule.code,
                "name": rule.name,
                "category": rule.category,
                "clause_reference": rule.clause_reference,
                "threshold": row.threshold,
                "unit": row.unit,
                "formula": row.formula,
                "review_status": row.review_status,
                "definition": row.definition,
            }
            for row, rule in rule_versions
        ],
    }


@router.post("/rulesets/{standard_version_id}/activate", response_model=RuleSetOut, summary="Activate a rule set")
def activate_ruleset(
    standard_version_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.RULES_MANAGE)),
) -> dict:
    version = db.get(StandardVersion, standard_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Ruleset not found")
    siblings = db.execute(
        select(StandardVersion).where(StandardVersion.standard_id == version.standard_id)
    ).scalars().all()
    for sibling in siblings:
        sibling.is_active = sibling.id == version.id
        if sibling.id == version.id:
            sibling.status = "active"
            sibling.activated_at = utcnow()
            sibling.activated_by = user.id
        elif sibling.status == "active":
            sibling.status = "superseded"

    audit_service.record(
        db, event_type="RULE_ACTIVATION", entity_type="standard_version", entity_id=version.id,
        actor=user, after={"version_label": version.version_label, "activated_at": str(version.activated_at)},
    )
    db.commit()
    return get_ruleset(standard_version_id, db=db, user=user)


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
