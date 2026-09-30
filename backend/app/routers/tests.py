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
    AttachmentLink,
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
    RetestRequest,
    TestInstanceOut,
    TestInstanceUpdate,
)
from app.security.permissions import P
from app.security.scope import case_editable_by
from app.services import audit_service, equipment_service, metrology_service, readiness
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
        "revision_no": instance.revision_no,
        "supersedes_test_instance_id": instance.supersedes_test_instance_id,
        "superseded_by_test_instance_id": instance.superseded_by_test_instance_id,
        "retest_reason": instance.retest_reason,
        "superseded_at": instance.superseded_at,
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
    include_superseded: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[dict]:
    """The live revision of every test, or the whole history on request.

    A superseded record is history. It is listed only when it is asked for, so
    no execution view has to remember to filter it out (audit item 16).
    """
    case = get_case_or_404(db, case_id, user)
    if include_superseded:
        return [_serialise_test(instance) for instance in case.tests]
    return [_serialise_test(instance) for instance in readiness.live_tests(case)]


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
        # Evidence gate (audit item 12): a procedure that declares mandatory
        # evidence is not complete until that evidence is linked to this test.
        requirements = instance.definition.evidence_requirements or {}
        if requirements.get("required"):
            linked = db.execute(
                select(AttachmentLink).where(
                    AttachmentLink.test_instance_id == instance.id,
                    AttachmentLink.case_id == case.id,
                )
            ).scalars().first()
            if linked is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"{instance.definition.test_code} cannot be completed: this procedure "
                        "requires evidence, and no attachment is linked to it. Link the "
                        "photograph or record to this test, then mark it complete."
                    ),
                )
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
    # Calibration gate (audit item 11): a measurement supported by a standard
    # whose calibration has expired is not calculated at all, rather than
    # calculated and then questioned.
    unusable = equipment_service.blocking_equipment_for_test(db, instance)
    if unusable:
        raise HTTPException(
            status_code=409,
            detail=(
                "This test cannot be calculated while the equipment recorded against it "
                "may not be used: "
                + "; ".join(equipment_service.equipment_state_line(state) for state in unusable)
            ),
        )
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


@router.get(
    "/tests/{test_id}/explanation",
    summary="Why this test has the result it has, and what to do next",
)
def explain_test(
    test_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    """The stored evidence for the result: rule, clause, governing row, steps.

    Read-only. A test that has not been calculated yet is dry-run so the answer
    names what is missing instead of saying "no result" (audit item 16).
    """
    instance, case = _load_test(db, test_id, user)
    payload = readiness.test_explanation(db, case, instance)
    payload["equipment"] = [
        item
        for item in equipment_service.case_equipment(db, case)["items"]
        if item["test_instance_id"] == str(instance.id)
    ]
    payload["calibration_blocking"] = [
        state for state in equipment_service.blocking_equipment_for_test(db, instance)
    ]
    return payload


@router.post(
    "/tests/{test_id}/retest",
    response_model=TestInstanceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Supersede this test record with a re-test",
)
def retest(
    test_id: uuid.UUID,
    payload: RetestRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
) -> dict:
    """Start a new revision of this test, keeping the record it replaces.

    Nothing is overwritten: the superseded row keeps its observations, its
    calculation runs and its result, and the replacement points back at it, so
    what was measured before the correction stays readable.
    """
    instance, case = _load_test(db, test_id, user)
    try:
        replacement = readiness.start_retest(
            db, case=case, test=instance, actor=user, reason=payload.reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(replacement)
    return _serialise_test(replacement)


def _revision_summary(instance: TestInstance) -> dict:
    result = instance.compliance_result
    return {
        "id": instance.id,
        "revision_no": instance.revision_no,
        "status": instance.status,
        "result_status": instance.result_status,
        "applicability_status": instance.applicability_status,
        "is_waived": instance.is_waived,
        "observations": len(instance.observations),
        "measured_value": decimal_str(result.measured_value) if result else None,
        "limit_value": decimal_str(result.limit_value) if result else None,
        "margin": decimal_str(result.margin) if result else None,
        "rule_id": result.rule_id if result else None,
        "completed_at": instance.completed_at,
        "superseded_at": instance.superseded_at,
        "retest_reason": instance.retest_reason,
        "live": instance.superseded_at is None,
    }


@router.get("/cases/{case_id}/tests/{test_id}/history", summary="Every revision of one test")
def test_history(
    case_id: uuid.UUID,
    test_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    """The revision chain of one test, oldest first (audit item 13).

    A controlled re-test keeps the record it replaces, so the chain answers
    what was measured before the correction and why it was repeated.
    """
    case = get_case_or_404(db, case_id, user)
    instance = db.get(TestInstance, test_id)
    if instance is None or instance.case_id != case.id:
        raise HTTPException(status_code=404, detail="Test instance not found")

    seen: set[uuid.UUID] = set()

    def load(identifier: uuid.UUID) -> TestInstance | None:
        if identifier is None or identifier in seen:
            return None
        seen.add(identifier)
        row = db.get(TestInstance, identifier)
        return row if row is not None and row.case_id == case.id else None

    chain = [instance]
    seen.add(instance.id)
    current = instance
    while current.supersedes_test_instance_id:
        parent = load(current.supersedes_test_instance_id)
        if parent is None:
            break
        chain.insert(0, parent)
        current = parent
    current = chain[-1]
    while current.superseded_by_test_instance_id:
        child = load(current.superseded_by_test_instance_id)
        if child is None:
            break
        chain.append(child)
        current = child

    return {
        "case_id": case.id,
        "test_id": chain[-1].id,
        "test_code": chain[-1].definition.test_code if chain[-1].definition else None,
        "name": chain[-1].definition.name if chain[-1].definition else None,
        "revision_count": len(chain),
        "revisions": [_revision_summary(item) for item in chain],
    }


@router.post("/tests/{test_id}/anomaly-check", summary="AI statistical review of observations")
def anomaly_check(
    test_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.AI_USE, P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
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
