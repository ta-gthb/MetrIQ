"""Engineer -> reviewer -> approver workflow (PRD 18.1, FR-05)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.permissions import require_permission
from app.models import CaseStatus, TestInstance, User, utcnow
from app.routers._helpers import get_case_or_404, require_reason
from app.schemas.cases import CaseDetailOut, WorkflowActionRequest
from app.security.permissions import P
from app.services import audit_service, metrology_service
from app.services.report_engine import generate_report

router = APIRouter(tags=["Workflow"])

UNRESOLVED = {"PENDING", "INCOMPLETE", "INVALID"}


def _assert_status(case, allowed: set[str], action: str) -> None:
    if case.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{action}' is not available while the case is in status {case.status}. "
                f"Allowed from: {', '.join(sorted(allowed))}."
            ),
        )


def _assert_participant(case, user: User, field: str, action: str) -> None:
    from app.security.permissions import SUPER_ADMIN

    expected = getattr(case, f"{field}_id")
    if user.role_code == SUPER_ADMIN:
        return
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No {field} is assigned to this case; assign one before performing '{action}'.",
        )
    if expected != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only the assigned {field} may perform '{action}' on this case.",
        )


@router.post("/cases/{case_id}/submit", response_model=CaseDetailOut, summary="Submit for technical review")
def submit_case(
    case_id: uuid.UUID,
    payload: WorkflowActionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.CASES_SUBMIT)),
) -> dict:
    from app.routers._helpers import serialise_case

    case = get_case_or_404(db, case_id, user)
    _assert_status(
        case,
        {CaseStatus.DRAFT, CaseStatus.ASSIGNED, CaseStatus.IN_PROGRESS,
         CaseStatus.CORRECTION_REQUIRED, CaseStatus.TESTING_COMPLETED},
        "submit",
    )
    _assert_participant(case, user, "engineer", "submit")

    from app.services.readiness import live_tests

    incomplete: list[str] = []
    failed: list[str] = []
    # A superseded record is history: the test that has to be resolved is the
    # live revision of it (audit item 16).
    for test_instance in live_tests(case):
        if test_instance.applicability_status == "NOT_APPLICABLE":
            if not test_instance.applicability_reason:
                incomplete.append(f"{test_instance.definition.test_code} (not applicable without a reason)")
            continue
        if test_instance.status != "COMPLETED":
            incomplete.append(f"{test_instance.definition.test_code} (not completed)")
            continue
        if test_instance.result_status in UNRESOLVED:
            incomplete.append(f"{test_instance.definition.test_code} ({test_instance.result_status})")
        if test_instance.result_status == "FAIL":
            failed.append(test_instance.definition.test_code)

    if incomplete:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "The case cannot be submitted until every applicable test is resolved.",
                "blocking_tests": incomplete,
            },
        )
    if failed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": (
                    "One or more tests failed. A failed type evaluation cannot be submitted as a "
                    "conforming report; correct the instrument or record an approved waiver with a reason."
                ),
                "failed_tests": failed,
            },
        )

    from app.services.attachment_service.service import evidence_requirements

    evidence = evidence_requirements(db, case)
    if not evidence["satisfied"]:
        missing_labels = [category.replace("_", " ") for category in evidence["missing"]]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": (
                    "At least two clear photographs are required before submission: "
                    + " and ".join(missing_labels)
                    + ". Attach them in the instrument or execution step of the evaluation."
                ),
                "missing_evidence": evidence["missing"],
                "evidence_requirements": evidence,
            },
        )

    previous = case.status
    case.status = CaseStatus.TESTING_COMPLETED
    case.submitted_at = utcnow()
    case.last_correction_reason = None
    audit_service.log_workflow(
        db, case=case, action="SUBMIT", actor=user, from_status=previous,
        to_status=case.status, reason=payload.reason,
    )
    audit_service.record(
        db, event_type="SUBMIT", entity_type="evaluation_case", entity_id=case.id, actor=user,
        case_id=case.id, before={"status": previous}, after={"status": case.status},
    )
    audit_service.notify(
        db, user_id=case.reviewer_id, case_id=case.id,
        title=f"{case.application_no} is ready for technical review",
        body="All applicable tests are complete.", link_url=f"/evaluation.html?case={case.id}",
    )
    db.commit()
    db.refresh(case)
    return serialise_case(case, detail=True)


@router.post("/cases/{case_id}/review", response_model=CaseDetailOut, summary="Technical review decision")
def review_case(
    case_id: uuid.UUID,
    payload: WorkflowActionRequest,
    decision: str = "verify",
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.CASES_REVIEW)),
) -> dict:
    from app.routers._helpers import serialise_case

    case = get_case_or_404(db, case_id, user)
    _assert_status(
        case,
        {CaseStatus.TESTING_COMPLETED, CaseStatus.UNDER_REVIEW, CaseStatus.CORRECTION_REQUIRED},
        "review",
    )
    _assert_participant(case, user, "reviewer", "review")

    if decision not in {"verify", "request_correction"}:
        raise HTTPException(status_code=422, detail="decision must be 'verify' or 'request_correction'")

    previous = case.status
    if decision == "verify":
        case.status = CaseStatus.VERIFIED
        case.verified_at = utcnow()
        event = "VERIFY"
        note = f"{case.application_no} has been technically verified."
    else:
        reason = require_reason(payload.reason, minimum=10, field="correction reason")
        case.status = CaseStatus.CORRECTION_REQUIRED
        case.last_correction_reason = reason
        case.revision_no += 1
        event = "CORRECTION_REQUEST"
        note = f"Corrections requested on {case.application_no}: {reason}"

    audit_service.log_workflow(
        db, case=case, action=event, actor=user, from_status=previous, to_status=case.status,
        reason=payload.reason, target_test_instance_id=payload.target_test_instance_id,
    )
    audit_service.record(
        db, event_type=event, entity_type="evaluation_case", entity_id=case.id, actor=user,
        case_id=case.id, before={"status": previous}, after={"status": case.status},
        reason=payload.reason,
    )
    target = case.engineer_id if decision == "request_correction" else case.approver_id
    audit_service.notify(
        db, user_id=target, case_id=case.id, title=note,
        severity="warning" if decision == "request_correction" else "info",
        link_url=f"/evaluation.html?case={case.id}",
    )
    db.commit()
    db.refresh(case)
    return serialise_case(case, detail=True)


@router.post("/cases/{case_id}/verify", response_model=CaseDetailOut, summary="Verify (reviewer)")
def verify_case(
    case_id: uuid.UUID,
    payload: WorkflowActionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.CASES_REVIEW)),
) -> dict:
    return review_case(case_id=case_id, payload=payload, decision="verify", db=db, user=user)


@router.post("/cases/{case_id}/request-correction", response_model=CaseDetailOut, summary="Request corrections")
def request_correction(
    case_id: uuid.UUID,
    payload: WorkflowActionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.CASES_REQUEST_CORRECTION)),
) -> dict:
    return review_case(case_id=case_id, payload=payload, decision="request_correction", db=db, user=user)


@router.post("/cases/{case_id}/approve", response_model=CaseDetailOut, summary="Approve the report")
def approve_case(
    case_id: uuid.UUID,
    payload: WorkflowActionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.CASES_APPROVE)),
) -> dict:
    from app.routers._helpers import serialise_case

    case = get_case_or_404(db, case_id, user)
    _assert_status(case, {CaseStatus.VERIFIED, CaseStatus.UNDER_APPROVAL}, "approve")
    _assert_participant(case, user, "approver", "approve")

    previous = case.status
    case.status = CaseStatus.APPROVED
    case.approved_at = utcnow()
    audit_service.log_workflow(
        db, case=case, action="APPROVE", actor=user, from_status=previous,
        to_status=case.status, reason=payload.reason,
    )
    audit_service.record(
        db, event_type="APPROVE", entity_type="evaluation_case", entity_id=case.id, actor=user,
        case_id=case.id, before={"status": previous}, after={"status": case.status},
        reason=payload.reason,
    )
    db.commit()
    db.refresh(case)
    return serialise_case(case, detail=True)


@router.post("/cases/{case_id}/reject", response_model=CaseDetailOut, summary="Reject at approval")
def reject_case(
    case_id: uuid.UUID,
    payload: WorkflowActionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.CASES_APPROVE)),
) -> dict:
    from app.routers._helpers import serialise_case

    case = get_case_or_404(db, case_id, user)
    _assert_status(case, {CaseStatus.VERIFIED, CaseStatus.UNDER_APPROVAL}, "reject")
    _assert_participant(case, user, "approver", "reject")
    reason = require_reason(payload.reason, minimum=10, field="rejection reason")

    previous = case.status
    case.status = CaseStatus.REJECTED
    case.revision_no += 1
    audit_service.log_workflow(
        db, case=case, action="REJECT", actor=user, from_status=previous,
        to_status=case.status, reason=reason,
    )
    audit_service.record(
        db, event_type="REJECT", entity_type="evaluation_case", entity_id=case.id, actor=user,
        case_id=case.id, before={"status": previous}, after={"status": case.status}, reason=reason,
    )
    audit_service.notify(
        db, user_id=case.engineer_id, case_id=case.id,
        title=f"{case.application_no} was rejected at approval",
        body=reason, severity="warning", link_url=f"/evaluation.html?case={case.id}",
    )
    db.commit()
    db.refresh(case)
    return serialise_case(case, detail=True)


@router.post("/cases/{case_id}/finalize", response_model=CaseDetailOut, summary="Finalize and lock")
def finalize_case(
    case_id: uuid.UUID,
    payload: WorkflowActionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.CASES_FINALIZE)),
) -> dict:
    from app.routers._helpers import serialise_case

    case = get_case_or_404(db, case_id, user)
    _assert_status(case, {CaseStatus.APPROVED, CaseStatus.FINALIZED}, "finalize")
    _assert_participant(case, user, "approver", "finalize")

    report, artefacts = generate_report(
        db, case=case, actor=user, formats=("pdf", "docx"), lock=True
    )
    previous = case.status
    if case.status != CaseStatus.FINALIZED:
        case.status = CaseStatus.FINALIZED
        case.finalized_at = utcnow()
    audit_service.log_workflow(
        db, case=case, action="FINALIZE", actor=user, from_status=previous, to_status=case.status,
        reason=payload.reason,
        payload={"report_no": report.report_no, "revision_no": report.revision_no,
                 "formats": sorted(artefacts)},
    )
    audit_service.record(
        db, event_type="FINALIZE", entity_type="evaluation_case", entity_id=case.id, actor=user,
        case_id=case.id, before={"status": previous}, after={"status": case.status},
        extra={"report_no": report.report_no, "content_hash": report.content_hash},
    )
    db.commit()
    db.refresh(case)
    payload_out = serialise_case(case, detail=True)
    payload_out["report"] = {
        "id": report.id,
        "report_no": report.report_no,
        "revision_no": report.revision_no,
        "content_hash": report.content_hash,
        "verification_code": report.verification_code,
        "formats": sorted(artefacts),
    }
    return payload_out


@router.post("/cases/{case_id}/cancel", response_model=CaseDetailOut, summary="Cancel a case")
def cancel_case(
    case_id: uuid.UUID,
    payload: WorkflowActionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.CASES_ASSIGN)),
) -> dict:
    from app.routers._helpers import serialise_case

    case = get_case_or_404(db, case_id, user)
    if case.status in {CaseStatus.FINALIZED, CaseStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail="A finalized or cancelled case cannot be cancelled.")
    reason = require_reason(payload.reason, minimum=10, field="cancellation reason")
    previous = case.status
    case.status = CaseStatus.CANCELLED
    case.cancelled_at = utcnow()
    case.scope_notes = f"{case.scope_notes or ''}\nCancelled: {reason}".strip()
    audit_service.log_workflow(
        db, case=case, action="CANCEL", actor=user, from_status=previous,
        to_status=case.status, reason=reason,
    )
    db.commit()
    db.refresh(case)
    return serialise_case(case, detail=True)
