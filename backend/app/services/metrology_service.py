"""Orchestration between stored records and the deterministic engines.

This service performs I/O; the engines stay pure. Every run stores the input
snapshot, the intermediates and the rule version so the result can be replayed
and explained (PRD 11.3, 11.5, 27.2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CalculationRun,
    ComplianceResult,
    EvaluationCase,
    Rule,
    RuleVersion,
    TestInstance,
    TestResultStatus,
)
from app.services import audit_service
from app.services.calculation_engine.engine import ENGINE_VERSION, build_observation, evaluate
from app.services.calculation_engine.types import CalcContext, CalcOutcome
from app.services.compliance_engine import evaluate as evaluate_compliance
from app.services.compliance_engine.engine import ComplianceDecision
from app.utils.decimals import decimal_str

RULESET_RULE_CODE = "R76-RULESET"


@dataclass(slots=True)
class TestEvaluation:
    outcome: CalcOutcome
    decision: ComplianceDecision
    calculation_run: CalculationRun | None = None
    compliance_result: ComplianceResult | None = None


def ruleset_for_standard_version(
    db: Session, standard_version_id: uuid.UUID | None
) -> tuple[dict[str, Any], str | None]:
    """Return (ruleset payload, rule version label) for a standard version."""
    if standard_version_id is None:
        return {}, None
    statement = (
        select(RuleVersion)
        .join(Rule, Rule.id == RuleVersion.rule_id)
        .where(
            RuleVersion.standard_version_id == standard_version_id,
            Rule.code == RULESET_RULE_CODE,
            RuleVersion.is_active.is_(True),
        )
        .order_by(RuleVersion.created_at.desc())
    )
    rule_version = db.execute(statement).scalars().first()
    if rule_version is None:
        return {}, None
    return dict(rule_version.definition or {}), rule_version.version_label


def active_standard_version(db: Session):
    """Return the activated OIML R 76-1 standard version, if one exists.

    Preview and MPE lookups should not require the caller to know the version
    identifier; when none is supplied the active ruleset is the sensible
    default and keeps the "How calculated" panel working out of the box.
    """
    from app.models import Standard, StandardVersion

    statement = (
        select(StandardVersion)
        .join(Standard, Standard.id == StandardVersion.standard_id)
        .where(Standard.code == "OIML R 76-1", StandardVersion.is_active.is_(True))
        .order_by(StandardVersion.created_at.desc())
    )
    return db.execute(statement).scalars().first()


def build_calc_context(
    *,
    test_instance: TestInstance,
    case: EvaluationCase,
    ruleset: dict[str, Any],
    rule_version: str | None,
    environment: dict[str, Any] | None = None,
    stage: str | None = None,
) -> CalcContext:
    instrument = case.instrument
    definition = test_instance.definition
    calculation_rules = definition.calculation_rules or {}
    definition_payload = {
        "test_code": definition.test_code,
        "name": definition.name,
        "clause_reference": definition.clause_reference,
        "input_schema": definition.input_schema or {},
        "validation_rules": definition.validation_rules or {},
        "calculation_rules": calculation_rules,
        "compliance_rules": definition.compliance_rules or {},
        "report_section_mapping": definition.report_section_mapping or {},
    }
    effective_stage = stage or calculation_rules.get("stage") or "verification"

    raw_rows = [
        {
            "observation_no": obs.observation_no,
            "position_label": obs.position_label,
            "load": obs.load,
            "indication": obs.indication,
            "additional_load": obs.additional_load,
            "elapsed_seconds": obs.elapsed_seconds,
            "temperature_c": obs.temperature_c,
            "value": obs.value,
            "unit": obs.unit,
            "input_payload": obs.input_payload or {},
        }
        for obs in sorted(test_instance.observations, key=lambda item: item.observation_no)
    ]
    observations = [build_observation(row, index) for index, row in enumerate(raw_rows, start=1)]

    instrument_payload = {
        "instrument_class": instrument.instrument_class,
        "e": instrument.verification_scale_interval,
        "d": instrument.actual_scale_interval,
        "unit": instrument.unit,
        "max_capacity": instrument.max_capacity,
        "min_capacity": instrument.min_capacity,
        "is_electronic": instrument.is_electronic,
        "is_multi_range": instrument.is_multi_range,
        "is_multi_interval": instrument.is_multi_interval,
        "has_tare_device": instrument.has_tare_device,
        "has_zero_device": instrument.has_zero_device,
        "model": instrument.model,
        "ranges": [
            {
                "range_no": item.range_no,
                "min_capacity": item.min_capacity,
                "max_capacity": item.max_capacity,
                "e": item.verification_scale_interval,
                "d": item.actual_scale_interval,
                "unit": item.unit,
            }
            for item in sorted(instrument.ranges, key=lambda item: item.range_no or 0)
        ],
    }

    return CalcContext(
        test_code=definition.test_code,
        instrument=instrument_payload,
        observations=observations,
        ruleset=ruleset or {},
        test_definition=definition_payload,
        environment=environment or {},
        stage=effective_stage,
        rule_version=rule_version,
        ranges=instrument_payload["ranges"],
    )


def _jsonable(value: Any) -> Any:
    """Keep nested instrument data (the range table) JSON-serialisable."""
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return decimal_str(value)
    return value


def _input_snapshot(ctx: CalcContext) -> dict[str, Any]:
    return {
        "test_code": ctx.test_code,
        "stage": ctx.stage,
        "rule_version": ctx.rule_version,
        "instrument": {
            key: decimal_str(value)
            if isinstance(value, Decimal)
            else _jsonable(value)
            for key, value in ctx.instrument.items()
        },
        "observations": [row.as_dict() for row in ctx.observations],
        "test_definition_rules": {
            "calculation_rules": ctx.test_definition.get("calculation_rules"),
            "validation_rules": ctx.test_definition.get("validation_rules"),
            "compliance_rules": ctx.test_definition.get("compliance_rules"),
        },
    }


def evaluate_test_instance(
    db: Session,
    *,
    case: EvaluationCase,
    test_instance: TestInstance,
    actor=None,
    stage: str | None = None,
    persist: bool = True,
    request_id: str | None = None,
) -> TestEvaluation:
    ruleset, rule_version = ruleset_for_standard_version(db, case.standard_version_id)
    ctx = build_calc_context(
        test_instance=test_instance,
        case=case,
        ruleset=ruleset,
        rule_version=rule_version,
        stage=stage,
    )
    outcome = evaluate(ctx)

    decision = evaluate_compliance(
        outcome,
        compliance_rules=test_instance.definition.compliance_rules or {},
        applicability_status=test_instance.applicability_status,
        is_waived=bool(test_instance.is_waived),
    )
    evaluation = TestEvaluation(outcome=outcome, decision=decision)
    if not persist:
        return evaluation

    now = datetime.now(timezone.utc)
    run = CalculationRun(
        test_instance_id=test_instance.id,
        test_code=ctx.test_code,
        rule_version_label=rule_version,
        standard_version_id=case.standard_version_id,
        engine_version=ENGINE_VERSION,
        input_snapshot=_input_snapshot(ctx),
        intermediates=outcome.intermediates,
        outputs=outcome.as_dict(),
        units={"instrument": ctx.unit, "mpe": "instrument unit"},
        rounding_policy=outcome.rounding_policy,
        is_valid=outcome.is_valid,
        errors=list(outcome.errors),
        calculated_at=now,
        calculated_by=getattr(actor, "id", None),
    )
    db.add(run)
    db.flush()

    result = db.execute(
        select(ComplianceResult).where(ComplianceResult.test_instance_id == test_instance.id)
    ).scalars().first()
    if result is None:
        result = ComplianceResult(test_instance_id=test_instance.id)
        db.add(result)

    result.calculation_run_id = run.id
    result.status = decision.status
    result.measured_value = decision.measured_value
    result.limit_value = decision.limit_value
    result.margin = decision.margin
    result.unit = outcome.unit or None
    result.rule_id = outcome.rule_id
    result.rule_version = outcome.rule_version or rule_version
    result.clause_reference = outcome.clause_reference
    result.explanation = decision.explanation
    result.details = {
        "intermediates": outcome.intermediates,
        "rows": [row.as_dict() for row in outcome.rows],
        "warnings": outcome.warnings,
        "errors": outcome.errors,
        "engine_version": ENGINE_VERSION,
    }
    result.evaluated_at = now

    test_instance.result_status = decision.status
    if test_instance.status != "COMPLETED":
        test_instance.status = "IN_PROGRESS"

    evaluation.calculation_run = run
    evaluation.compliance_result = result

    audit_service.record(
        db,
        event_type="CALCULATE",
        entity_type="test_instance",
        entity_id=test_instance.id,
        actor=actor,
        case_id=case.id,
        after={
            "status": decision.status,
            "measured_value": decimal_str(decision.measured_value),
            "limit_value": decimal_str(decision.limit_value),
            "rule_version": rule_version,
        },
        request_id=request_id,
        extra={"test_code": ctx.test_code, "engine_version": ENGINE_VERSION},
    )
    return evaluation


def recalculate_case(db: Session, *, case: EvaluationCase, actor=None) -> list[TestEvaluation]:
    """Re-run every applicable test on a case (used after a correction cycle)."""
    results: list[TestEvaluation] = []
    for test_instance in case.tests:
        if test_instance.applicability_status == "NOT_APPLICABLE":
            continue
        results.append(evaluate_test_instance(db, case=case, test_instance=test_instance, actor=actor))
    return results


def summarise_case(case: EvaluationCase) -> dict[str, Any]:
    """Aggregate case-level test metrics for the dashboard and report."""
    counts: dict[str, int] = {}
    for test_instance in case.tests:
        status = test_instance.result_status or "PENDING"
        counts[status] = counts.get(status, 0) + 1
    applicable = [t for t in case.tests if t.applicability_status != "NOT_APPLICABLE"]
    completed = [t for t in applicable if t.status == "COMPLETED"]
    unresolved = any(
        t.result_status in {TestResultStatus.PENDING, TestResultStatus.INCOMPLETE, TestResultStatus.INVALID}
        for t in applicable
    )
    if counts.get(TestResultStatus.FAIL):
        overall = "FAIL"
    elif unresolved:
        overall = "INCOMPLETE"
    else:
        overall = "PASS"
    return {
        "total": len(case.tests),
        "applicable": len(applicable),
        "completed": len(completed),
        "pending": len(applicable) - len(completed),
        "by_status": counts,
        "overall": overall,
    }
