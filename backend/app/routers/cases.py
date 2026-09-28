"""Evaluation case management (FR-05, FR-06, FR-07)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.dependencies.permissions import require_any_permission, require_permission
from app.models import (
    Applicant,
    CaseStatus,
    EnvironmentalCondition,
    EvaluationCase,
    Instrument,
    InstrumentRange,
    Manufacturer,
    ReportTemplate,
    ReportTemplateVersion,
    Standard,
    StandardVersion,
    TestDefinition,
    TestInstance,
    User,
    utcnow,
)
from app.routers._helpers import get_case_or_404, next_application_number, paginate, serialise_case
from app.schemas.cases import (
    CaseAssignmentRequest,
    CaseCreateRequest,
    CaseDetailOut,
    CaseListOut,
    CaseUpdateRequest,
    EnvironmentalConditionIn,
    EnvironmentalConditionOut,
)
from app.schemas.common import Paginated
from app.schemas.masters import InstrumentCreate
from app.security.permissions import P, SUPER_ADMIN
from app.security.scope import case_editable_by, laboratory_filter
from app.services import audit_service
from app.services.test_engine import generate_and_persist_plan

router = APIRouter(tags=["Evaluation cases"])


def _active_standard_version(db: Session, standard_code: str = "OIML R 76-1") -> StandardVersion | None:
    statement = (
        select(StandardVersion)
        .join(Standard, Standard.id == StandardVersion.standard_id)
        .where(Standard.code == standard_code, StandardVersion.is_active.is_(True))
        .order_by(StandardVersion.created_at.desc())
    )
    return db.execute(statement).scalars().first()


def _active_template_version(db: Session, code: str = "R76-2-TYPE-EVAL") -> ReportTemplateVersion | None:
    statement = (
        select(ReportTemplateVersion)
        .join(ReportTemplate, ReportTemplate.id == ReportTemplateVersion.template_id)
        .where(ReportTemplate.code == code, ReportTemplateVersion.is_active.is_(True))
        .order_by(ReportTemplateVersion.version_no.desc())
    )
    return db.execute(statement).scalars().first()


def _ensure_instrument(db: Session, payload: dict, user: User) -> Instrument:
    data = dict(payload)
    ranges = data.pop("ranges", None) or []
    validated = InstrumentCreate(**{**data, "ranges": ranges})
    instrument = Instrument(
        **validated.model_dump(exclude={"ranges"}),
        created_by=user.id,
    )
    for index, item in enumerate(validated.ranges, start=1):
        instrument.ranges.append(
            InstrumentRange(**{**item.model_dump(), "range_no": item.range_no or index})
        )
    db.add(instrument)
    db.flush()
    return instrument


@router.post(
    "/cases",
    response_model=CaseDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an evaluation case",
)
def create_case(
    payload: CaseCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.CASES_CREATE)),
) -> dict:
    laboratory_id = payload.laboratory_id or user.laboratory_id
    if laboratory_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A laboratory is required. Assign the user to a laboratory or pass laboratory_id.",
        )
    if user.role_code != SUPER_ADMIN and user.laboratory_id and laboratory_id != user.laboratory_id:
        raise HTTPException(status_code=403, detail="You cannot create cases outside your laboratory.")

    instrument = db.get(Instrument, payload.instrument_id) if payload.instrument_id else None
    if instrument is None and payload.instrument:
        instrument = _ensure_instrument(db, payload.instrument, user)
    if instrument is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide instrument_id of an existing record or an inline instrument payload.",
        )

    applicant = db.get(Applicant, payload.applicant_id) if payload.applicant_id else None
    if applicant is None and payload.applicant:
        applicant = Applicant(**payload.applicant)
        db.add(applicant)
        db.flush()

    manufacturer = db.get(Manufacturer, payload.manufacturer_id) if payload.manufacturer_id else None
    if manufacturer is None and payload.manufacturer:
        manufacturer = Manufacturer(**payload.manufacturer)
        db.add(manufacturer)
        db.flush()
    if manufacturer is None:
        manufacturer = instrument.manufacturer

    standard_version = (
        db.get(StandardVersion, payload.standard_version_id)
        if payload.standard_version_id
        else _active_standard_version(db)
    )
    if standard_version is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No active standards version is configured. Run the ruleset seed first.",
        )
    template_version = _active_template_version(db)

    case = EvaluationCase(
        application_no=next_application_number(db),
        title=payload.title or f"{instrument.model} type evaluation",
        instrument_id=instrument.id,
        applicant_id=applicant.id if applicant else None,
        manufacturer_id=manufacturer.id if manufacturer else None,
        laboratory_id=laboratory_id,
        engineer_id=payload.engineer_id or (user.id if user.role_code in {"ENGINEER", "LAB_ADMIN"} else None),
        reviewer_id=payload.reviewer_id,
        approver_id=payload.approver_id,
        standard_version_id=standard_version.id,
        template_version_id=template_version.id if template_version else None,
        status=CaseStatus.DRAFT,
        priority=payload.priority,
        purpose=payload.purpose,
        scope_notes=payload.scope_notes,
        application_date=utcnow(),
        created_by=user.id,
    )
    db.add(case)
    db.flush()

    generate_and_persist_plan(db, case=case, user=user)
    audit_service.record(
        db, event_type="CREATE", entity_type="evaluation_case", entity_id=case.id, actor=user,
        case_id=case.id, after={"application_no": case.application_no, "status": case.status},
    )
    audit_service.log_workflow(
        db, case=case, action="CREATE", actor=user, from_status=None, to_status=case.status,
        payload={"application_no": case.application_no},
    )
    db.commit()
    db.refresh(case)
    return serialise_case(case, detail=True)


@router.get("/cases", response_model=Paginated[CaseListOut], summary="List evaluation cases")
def list_cases(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = None,
    mine: bool = False,
    page: int = 1,
    page_size: int = Query(25, le=200),
) -> Paginated[CaseListOut]:
    statement = select(EvaluationCase).order_by(EvaluationCase.created_at.desc())
    laboratory_id = laboratory_filter(user)
    if laboratory_id is not None:
        statement = statement.where(EvaluationCase.laboratory_id == laboratory_id)
    if user.role_code == "ENGINEER":
        statement = statement.where(
            (EvaluationCase.engineer_id == user.id) | (EvaluationCase.created_by == user.id)
        )
    if mine:
        statement = statement.where(
            (EvaluationCase.engineer_id == user.id)
            | (EvaluationCase.reviewer_id == user.id)
            | (EvaluationCase.approver_id == user.id)
        )
    if status_filter:
        statement = statement.where(EvaluationCase.status == status_filter)
    if search:
        pattern = f"%{search}%"
        statement = statement.where(EvaluationCase.application_no.ilike(pattern))
    rows, meta = paginate(db, statement, page=page, page_size=page_size)
    return Paginated[CaseListOut](items=[serialise_case(case) for case in rows], meta=meta)


@router.get("/cases/{case_id}", response_model=CaseDetailOut, summary="Evaluation case detail")
def get_case(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    case = get_case_or_404(db, case_id, user)
    payload = serialise_case(case, detail=True)
    payload["tests"] = [
        {
            "id": instance.id,
            "test_code": instance.definition.test_code,
            "name": instance.definition.name,
            "clause_reference": instance.definition.clause_reference,
            "phase": instance.definition.phase,
            "sequence_no": instance.sequence_no,
            "applicability_status": instance.applicability_status,
            "applicability_reason": instance.applicability_reason,
            "status": instance.status,
            "result_status": instance.result_status,
            "is_waived": instance.is_waived,
            "measured_value": instance.compliance_result.measured_value if instance.compliance_result else None,
            "limit_value": instance.compliance_result.limit_value if instance.compliance_result else None,
            "unit": instance.compliance_result.unit if instance.compliance_result else None,
            "observation_count": len(instance.observations),
        }
        for instance in case.tests
    ]
    payload["test_plan"] = (case.test_plan_snapshot or {}).get("items", [])
    return payload


@router.patch("/cases/{case_id}", response_model=CaseDetailOut, summary="Update an evaluation case")
def update_case(
    case_id: uuid.UUID,
    payload: CaseUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.CASES_EDIT, P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
) -> dict:
    case = get_case_or_404(db, case_id, user)
    if not case_editable_by(user, case):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A case in status {case.status} cannot be edited, or you are not its assigned engineer.",
        )
    changes = payload.model_dump(exclude_unset=True)
    before = {key: getattr(case, key) for key in changes}
    for key, value in changes.items():
        setattr(case, key, value)
    if "standard_version_id" in changes and changes["standard_version_id"]:
        generate_and_persist_plan(db, case=case, user=user)
    audit_service.record(
        db, event_type="EDIT", entity_type="evaluation_case", entity_id=case.id, actor=user,
        case_id=case.id, before=before, after=changes,
    )
    db.commit()
    db.refresh(case)
    return serialise_case(case, detail=True)


@router.post("/cases/{case_id}/assignments", response_model=CaseDetailOut, summary="Assign personnel")
def assign_case(
    case_id: uuid.UUID,
    payload: CaseAssignmentRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.CASES_ASSIGN)),
) -> dict:
    case = get_case_or_404(db, case_id, user)
    if case.status in CaseStatus.IMMUTABLE:
        raise HTTPException(status_code=409, detail="A finalized case cannot be reassigned.")
    changes: dict[str, uuid.UUID | None] = {}
    for field in ("engineer_id", "reviewer_id", "approver_id"):
        value = getattr(payload, field)
        if value is not None:
            target = db.get(User, value)
            if target is None or not target.is_active:
                raise HTTPException(status_code=422, detail=f"{field} must reference an active user")
            if user.role_code != SUPER_ADMIN and target.laboratory_id != case.laboratory_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} must reference a user in the case laboratory.",
                )
            setattr(case, field, value)
            changes[field] = value

    if case.status == CaseStatus.DRAFT and (case.engineer_id or case.reviewer_id or case.approver_id):
        case.status = CaseStatus.ASSIGNED
    audit_service.record(
        db, event_type="EDIT", entity_type="evaluation_case", entity_id=case.id, actor=user,
        case_id=case.id, field_changed="assignments", after={k: str(v) for k, v in changes.items()},
        reason=payload.reason,
    )
    audit_service.log_workflow(
        db, case=case, action="ASSIGN", actor=user,
        from_status=CaseStatus.DRAFT, to_status=case.status, reason=payload.reason, payload=changes,
    )
    for field, target_id in changes.items():
        audit_service.notify(
            db, user_id=target_id, case_id=case.id,
            title=f"You have been assigned as {field.replace('_id', '')} on {case.application_no}",
            body=case.title, link_url=f"/evaluation.html?case={case.id}",
        )
    db.commit()
    db.refresh(case)
    return serialise_case(case, detail=True)


@router.post("/cases/{case_id}/test-plan", response_model=dict, summary="Regenerate the test plan")
def regenerate_plan(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.CASES_EDIT, P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
) -> dict:
    case = get_case_or_404(db, case_id, user)
    if not case_editable_by(user, case):
        raise HTTPException(status_code=409, detail="The test plan can only change before submission.")
    generate_and_persist_plan(db, case=case, user=user)
    db.commit()
    db.refresh(case)
    return {
        "case_id": case.id,
        "generated_at": case.test_plan_generated_at,
        "items": (case.test_plan_snapshot or {}).get("items", []),
    }


@router.get("/cases/{case_id}/test-plan", response_model=dict, summary="Read the preserved test plan")
def read_plan(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    case = get_case_or_404(db, case_id, user)
    return {
        "case_id": case.id,
        "generated_at": case.test_plan_generated_at,
        "ruleset_label": case.standard_version.version_label if case.standard_version else None,
        "preserved": case.test_plan_snapshot is not None,
        "items": (case.test_plan_snapshot or {}).get("items", []),
    }


@router.post(
    "/cases/{case_id}/conditions",
    response_model=EnvironmentalConditionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record environmental conditions",
)
def add_condition(
    case_id: uuid.UUID,
    payload: EnvironmentalConditionIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.CASES_EDIT, P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
) -> EnvironmentalCondition:
    case = get_case_or_404(db, case_id, user)
    if not case_editable_by(user, case):
        raise HTTPException(status_code=409, detail="Conditions cannot be added in the current status.")
    record = EnvironmentalCondition(
        case_id=case.id, recorded_by=user.id, **payload.model_dump()
    )
    db.add(record)
    audit_service.record(
        db, event_type="CREATE", entity_type="environmental_condition", entity_id=record.id,
        actor=user, case_id=case.id, after=payload.model_dump(),
    )
    db.commit()
    db.refresh(record)
    return record


@router.get(
    "/cases/{case_id}/conditions",
    response_model=list[EnvironmentalConditionOut],
    summary="List environmental conditions",
)
def list_conditions(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[EnvironmentalCondition]:
    case = get_case_or_404(db, case_id, user)
    return case.conditions
