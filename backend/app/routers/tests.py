"""Test instance execution, observation entry, validation and calculation (FR-08, PRD 11)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.dependencies.permissions import require_any_permission, require_permission
from app.models import (
    CaseStatus,
    ManualOverride,
    TestInstance,
    TestObservation,
    TestResultStatus,
    User,
    utcnow,
)
from app.routers._helpers import get_case_or_404, require_reason
from app.schemas.tests import (
    CalculationOut,
    ManualOverrideRequest,
    ObservationBatchIn,
    TestInstanceOut,
    TestInstanceUpdate,
)
from app.security.permissions import P
from app.security.scope import case_editable_by
from app.services import audit_service, metrology_service
from app.services.ai_service import get_ai_service
from app.utils.decimals import decimal_str

router = APIRouter(tags=["Test execution"])

TERMINAL_TEST_STATUSES = {"PASS", "FAIL", "NOT_APPLICABLE", "WAIVED"}

# Columns that map onto dedicated TestObservation attributes. Everything else a
# test definition declares (checklist item_code/conforms, instrument-specific
# extras) is preserved verbatim in input_payload.
OBSERVATION_FIELDS = {
    "observation_no",
    "position_label",
    "load",
    "indication",
    "additional_load",
    "elapsed_seconds",
    "temperature_c",
    "value",
    "unit",
}


def _load_test(db: Session, test_id: uuid.UUID, user: User) -> tuple[TestInstance, object]:
    instance = db.get(TestInstance, test_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Test instance not found")
    case = get_case_or_404(db, instance.case_id, user)
    return instance, case


def _serialise_test(instance: TestInstance) -> dict:
    latest = instance.calculation_runs[-1] if instance.calculation_runs else None
    return {
        "id": instance.id,
        "case_id": instance.case_id,
        "test_definition_id": instance.test_definition_id,
        "sequence_no": instance.sequence_no,
        "applicability_status": instance.applicability_status,
        "applicability_reason": instance.applicability_reason,
        "applicability_trace": instance.applicability_trace,
        "status": instance.status,
        "result_status": instance.result_status,
        "remarks": instance.remarks,
        "is_waived": instance.is_waived,
        "waiver_reason": instance.waiver_reason,
        "started_at": instance.started_at,
        "completed_at": instance.completed_at,
        "definition": instance.definition,
        "observations": instance.observations,
        "compliance_result": instance.compliance_result,
        "latest_calculation": latest,
    }


@router.get("/cases/{case_id}/tests", response_model=list[TestInstanceOut], summary="List case tests")
def list_tests(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[dict]:
    case = get_case_or_404(db, case_id, user)
    return [_serialise_test(instance) for instance in case.tests]


@router.get("/tests/{test_id}", response_model=TestInstanceOut, summary="Test instance detail")
def get_test(
    test_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    instance, _case = _load_test(db, test_id, user)
    return _serialise_test(instance)


@router.patch("/tests/{test_id}", response_model=TestInstanceOut, summary="Update a test instance")
def update_test(
    test_id: uuid.UUID,
    payload: TestInstanceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
) -> dict:
    instance, case = _load_test(db, test_id, user)
    if not case_editable_by(user, case):
        raise HTTPException(status_code=409, detail="This case is not editable in its current status.")

    changes = payload.model_dump(exclude_unset=True)
    if "applicability_status" in changes and changes["applicability_status"] is not None:
        new_status = changes["applicability_status"]
        if new_status not in {"APPLICABLE", "NOT_APPLICABLE", "MANUAL_REVIEW"}:
            raise HTTPException(status_code=422, detail="Invalid applicability status.")
        if new_status != instance.applicability_status:
            reason = require_reason(
                changes.get("applicability_reason"),
                minimum=10,
                field="justification for manually changing applicability",
            )
            instance.applicability_reason = reason
            instance.applicability_status = new_status
            if new_status == "NOT_APPLICABLE":
                instance.result_status = TestResultStatus.NOT_APPLICABLE
            audit_service.record(
                db, event_type="EDIT", entity_type="test_instance", entity_id=instance.id, actor=user,
                case_id=case.id, field_changed="applicability_status", after={"status": new_status},
                reason=reason,
            )

    if "is_waived" in changes and changes["is_waived"] is not None:
        if changes["is_waived"]:
            reason = require_reason(changes.get("waiver_reason"), minimum=10, field="waiver justification")
            instance.is_waived = True
            instance.waiver_reason = reason
        else:
            instance.is_waived = False
            instance.waiver_reason = None

    if changes.get("remarks") is not None:
        instance.remarks = changes["remarks"]

    if changes.get("mark_complete"):
        metrology_service.evaluate_test_instance(db, case=case, test_instance=instance, actor=user)
        if instance.result_status not in TERMINAL_TEST_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"The test cannot be completed because its result is {instance.result_status}. "
                    "Resolve the outstanding inputs or calculation first."
                ),
            )
        instance.status = "COMPLETED"
        instance.completed_at = utcnow()
        instance.completed_by = user.id

    db.commit()
    db.refresh(instance)
    return _serialise_test(instance)


@router.put("/tests/{test_id}/observations", response_model=TestInstanceOut, summary="Replace observations")
def put_observations(
    test_id: uuid.UUID,
    payload: ObservationBatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
) -> dict:
    instance, case = _load_test(db, test_id, user)
    if not case_editable_by(user, case):
        raise HTTPException(status_code=409, detail="This case is not editable in its current status.")
    if instance.applicability_status == "NOT_APPLICABLE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This test is marked not applicable. Change applicability with a reason before recording data.",
        )

    before_count = len(instance.observations)
    if payload.replace:
        for existing in list(instance.observations):
            db.delete(existing)
        db.flush()

    for index, row in enumerate(payload.observations, start=1):
        data = row.model_dump()
        input_payload = dict(data.pop("input_payload", None) or {})
        for key in [key for key in data if key not in OBSERVATION_FIELDS]:
            value = data.pop(key)
            if value is not None:
                input_payload[key] = value
        db.add(
            TestObservation(
                test_instance_id=instance.id,
                observation_no=data.get("observation_no") or index,
                created_by=user.id,
                updated_by=user.id,
                input_payload=input_payload,
                **{key: value for key, value in data.items() if key != "observation_no"},
            )
        )
    if payload.remarks is not None:
        instance.remarks = payload.remarks
    if instance.status == "NOT_STARTED":
        instance.status = "IN_PROGRESS"
        instance.started_at = utcnow()

    db.flush()
    # Autosave keeps the record coherent but never decides compliance: the
    # stored result is refreshed on validate/calculate or on completion.
    audit_service.record(
        db, event_type="EDIT", entity_type="test_instance", entity_id=instance.id, actor=user,
        case_id=case.id, field_changed="observations",
        before={"observation_count": before_count},
        after={"observation_count": len(payload.observations)},
    )
    db.commit()
    db.refresh(instance)
    return _serialise_test(instance)


@router.post("/tests/{test_id}/validate", response_model=CalculationOut, summary="Validate without saving")
def validate_test(
    test_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.TESTS_VALIDATE, P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
) -> dict:
    instance, case = _load_test(db, test_id, user)
    evaluation = metrology_service.evaluate_test_instance(
        db, case=case, test_instance=instance, actor=user, persist=False
    )
    outcome = evaluation.outcome
    payload = outcome.as_dict()
    payload.update(
        {
            "test_instance_id": instance.id,
            "test_code": outcome.intermediates.get("test_code", instance.definition.test_code),
            "engine_version": outcome.intermediates.get("engine_version", metrology_service.ENGINE_VERSION),
            "status": evaluation.decision.status,
            "explanation": evaluation.decision.explanation,
        }
    )
    return payload


@router.post("/tests/{test_id}/calculate", response_model=CalculationOut, summary="Calculate and store")
def calculate_test(
    test_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.TESTS_VALIDATE, P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
) -> dict:
    instance, case = _load_test(db, test_id, user)
    if not case_editable_by(user, case) and case.status != CaseStatus.UNDER_REVIEW:
        raise HTTPException(status_code=409, detail="This case is not editable in its current status.")
    evaluation = metrology_service.evaluate_test_instance(db, case=case, test_instance=instance, actor=user)
    db.commit()
    db.refresh(instance)

    outcome = evaluation.outcome
    payload = outcome.as_dict()
    payload.update(
        {
            "test_instance_id": instance.id,
            "test_code": instance.definition.test_code,
            "engine_version": metrology_service.ENGINE_VERSION,
            "status": evaluation.decision.status,
            "measured_value": evaluation.decision.measured_value,
            "limit_value": evaluation.decision.limit_value,
            "margin": evaluation.decision.margin,
            "explanation": evaluation.decision.explanation,
        }
    )
    return payload


@router.post("/tests/{test_id}/anomaly-check", summary="AI statistical review of observations")
def anomaly_check(
    test_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    instance, case = _load_test(db, test_id, user)
    service = get_ai_service(db, actor=user)
    report = service.anomaly_check(case=case, test_instance=instance)
    if report.available and report.findings:
        for finding in report.findings:
            if finding.row <= 0:
                continue
            observation = next(
                (item for item in instance.observations if item.observation_no == finding.row), None
            )
            if observation is not None and observation.anomaly_flag is None:
                observation.anomaly_flag = "ANOMALY"
                observation.anomaly_note = finding.message
    db.commit()
    return report.as_dict()


@router.post("/tests/{test_id}/anomaly-disposition", summary="Confirm or dismiss an anomaly warning")
def anomaly_disposition(
    test_id: uuid.UUID,
    observation_no: int,
    disposition: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
) -> dict:
    instance, case = _load_test(db, test_id, user)
    observation = next(
        (item for item in instance.observations if item.observation_no == observation_no), None
    )
    if observation is None:
        raise HTTPException(status_code=404, detail="Observation row not found")
    if disposition not in {"confirmed", "dismissed"}:
        raise HTTPException(status_code=422, detail="disposition must be 'confirmed' or 'dismissed'")
    observation.anomaly_disposition = disposition
    audit_service.record(
        db, event_type="AI_ACTION", entity_type="test_observation", entity_id=observation.id,
        actor=user, case_id=case.id, after={"observation_no": observation_no, "disposition": disposition},
        extra={"note": "AI anomaly warning disposition; observations and compliance unchanged."},
    )
    db.commit()
    return {"observation_no": observation_no, "disposition": disposition}


@router.post("/tests/{test_id}/override", summary="Record an exceptional manual override (PRD 11.6)")
def request_override(
    test_id: uuid.UUID,
    payload: ManualOverrideRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.OVERRIDE_REQUEST)),
) -> dict:
    instance, case = _load_test(db, test_id, user)
    reason = require_reason(payload.reason, minimum=20, field="override justification")
    if payload.status not in TestResultStatus.ALL:
        raise HTTPException(status_code=422, detail="Invalid target status.")
    if payload.status in {"PASS", "FAIL"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "PASS and FAIL are produced only by the deterministic compliance engine and "
                "cannot be set manually (PRD 27.2)."
            ),
        )

    result = instance.compliance_result
    override = ManualOverride(
        test_instance_id=instance.id,
        previous_status=result.status if result else instance.result_status,
        new_status=payload.status,
        previous_value=decimal_str(result.measured_value) if result else None,
        new_value=payload.new_value,
        reason=reason,
        requested_by=user.id,
    )
    db.add(override)
    instance.is_waived = payload.status == "WAIVED"
    instance.waiver_reason = reason if payload.status == "WAIVED" else None
    instance.result_status = payload.status
    audit_service.record(
        db, event_type="OVERRIDE", entity_type="test_instance", entity_id=instance.id, actor=user,
        case_id=case.id, field_changed="result_status",
        before={"status": override.previous_status, "measured_value": override.previous_value},
        after={"status": payload.status, "new_value": payload.new_value},
        reason=reason,
    )
    audit_service.notify(
        db, user_id=case.approver_id or case.reviewer_id, case_id=case.id,
        title=f"Manual override requested on {case.application_no}",
        body=f"{instance.definition.name}: {override.previous_status} -> {payload.status}",
        severity="warning", category="governance", link_url=f"/evaluation.html?case={case.id}",
    )
    db.commit()
    return {
        "test_instance_id": instance.id,
        "previous_status": override.previous_status,
        "new_status": payload.status,
        "automated_result_recoverable": True,
        "message": "The automated result remains stored and recoverable in the calculation run history.",
    }
