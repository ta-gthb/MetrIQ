"""Report generation, download and repository search (PRD 17, 15.1)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.dependencies.permissions import require_any_permission, require_permission
from app.models import CaseStatus, EvaluationCase, GeneratedReport, ReportRevision, User
from app.routers._helpers import get_case_or_404, paginate
from app.schemas.common import Paginated
from app.schemas.reports import (
    GeneratedReportOut,
    ReportGenerateRequest,
    ReportListItemOut,
    ReportRevisionOut,
)
from app.security.permissions import P
from app.security.scope import laboratory_filter
from app.services import audit_service
from app.services.report_engine import generate_report
from app.services.report_engine.service import read_artefact

router = APIRouter(tags=["Reports"])

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _load_report(db: Session, report_id: uuid.UUID, user: User) -> GeneratedReport:
    report = db.get(GeneratedReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    get_case_or_404(db, report.case_id, user)
    return report


@router.post(
    "/cases/{case_id}/reports/generate",
    response_model=GeneratedReportOut,
    status_code=status.HTTP_201_CREATED,
    summary="Generate PDF and DOCX reports",
)
def generate(
    case_id: uuid.UUID,
    payload: ReportGenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.REPORTS_GENERATE)),
) -> GeneratedReport:
    case = get_case_or_404(db, case_id, user)
    if case.status in {CaseStatus.DRAFT, CaseStatus.CANCELLED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A report can only be generated once the case has entered the review workflow.",
        )
    formats = tuple(dict.fromkeys(fmt.lower() for fmt in payload.formats))
    if not formats:
        raise HTTPException(status_code=422, detail="At least one format is required.")
    report, _artefacts = generate_report(
        db, case=case, actor=user, formats=formats, lock=payload.lock
    )
    db.commit()
    db.refresh(report)
    return report


@router.get(
    "/cases/{case_id}/reports",
    response_model=list[GeneratedReportOut],
    summary="Reports for a case",
)
def list_case_reports(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[GeneratedReport]:
    case = get_case_or_404(db, case_id, user)
    return db.execute(
        select(GeneratedReport)
        .where(GeneratedReport.case_id == case.id)
        .order_by(GeneratedReport.revision_no.desc())
    ).scalars().all()


@router.get("/reports", response_model=Paginated[ReportListItemOut], summary="Search the repository")
def search_reports(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    search: str | None = Query(None, description="Application or report number"),
    only_final: bool = False,
    page: int = 1,
    page_size: int = Query(25, le=200),
) -> Paginated[ReportListItemOut]:
    statement = (
        select(GeneratedReport)
        .join(EvaluationCase, EvaluationCase.id == GeneratedReport.case_id)
        .order_by(GeneratedReport.created_at.desc())
    )
    laboratory_id = laboratory_filter(user)
    if laboratory_id is not None:
        statement = statement.where(EvaluationCase.laboratory_id == laboratory_id)
    if user.role_code == "ENGINEER":
        statement = statement.where(EvaluationCase.engineer_id == user.id)
    if only_final:
        statement = statement.where(GeneratedReport.is_immutable.is_(True))
    if search:
        pattern = f"%{search}%"
        statement = statement.where(
            GeneratedReport.report_no.ilike(pattern) | EvaluationCase.application_no.ilike(pattern)
        )

    rows, meta = paginate(db, statement, page=page, page_size=page_size)
    items = []
    for report in rows:
        items.append(
            ReportListItemOut(
                id=report.id,
                report_no=report.report_no,
                application_no=report.case.application_no if report.case else None,
                case_id=report.case_id,
                status=report.status,
                revision_no=report.revision_no,
                is_immutable=report.is_immutable,
                ruleset_label=report.ruleset_label,
                template_label=report.template_label,
                verification_code=report.verification_code,
                generated_at=report.generated_at,
                overall_result=(report.data_snapshot or {}).get("summary", {}).get("overall"),
            )
        )
    return Paginated[ReportListItemOut](items=items, meta=meta)


@router.get("/reports/{report_id}", response_model=GeneratedReportOut, summary="Report metadata")
def get_report(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    report = _load_report(db, report_id, user)
    payload = GeneratedReportOut.model_validate(report).model_dump()
    payload["application_no"] = report.case.application_no if report.case else None
    payload["revision_count"] = len(report.revisions)
    return payload


@router.get("/reports/{report_id}/revisions", response_model=list[ReportRevisionOut], summary="Report revisions")
def report_revisions(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[ReportRevision]:
    report = _load_report(db, report_id, user)
    return report.revisions


@router.get("/reports/{report_id}/snapshot", summary="Structured report data snapshot")
def report_snapshot(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    report = _load_report(db, report_id, user)
    return report.data_snapshot or {}


@router.get("/reports/{report_id}/download", summary="Download the latest PDF or DOCX")
def download_report(
    report_id: uuid.UUID,
    fmt: str = Query("pdf"),
    revision: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.REPORTS_DOWNLOAD)),
) -> Response:
    report = _load_report(db, report_id, user)
    fmt = fmt.lower()
    if fmt not in CONTENT_TYPES:
        raise HTTPException(status_code=422, detail="fmt must be 'pdf' or 'docx'")

    statement = select(ReportRevision).where(
        ReportRevision.report_id == report.id, ReportRevision.format == fmt
    )
    if revision is not None:
        statement = statement.where(ReportRevision.revision_no == revision)
    revision_row = db.execute(
        statement.order_by(ReportRevision.revision_no.desc())
    ).scalars().first()
    if revision_row is None:
        raise HTTPException(status_code=404, detail=f"No {fmt.upper()} revision has been generated yet.")

    payload_bytes = read_artefact(revision_row.storage_key)
    if payload_bytes is None:
        raise HTTPException(status_code=410, detail="The stored report artefact is no longer available.")

    audit_service.record(
        db, event_type="DOWNLOAD", entity_type="generated_report", entity_id=report.id, actor=user,
        case_id=report.case_id, extra={"format": fmt, "revision": revision_row.revision_no},
    )
    db.commit()
    filename = f"{report.report_no}-R{revision_row.revision_no}.{fmt}"
    return Response(
        content=payload_bytes,
        media_type=CONTENT_TYPES[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-SHA256": revision_row.sha256 or "",
        },
    )


@router.get("/reports/{report_id}/pdf", summary="Download the latest PDF")
def download_pdf(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.REPORTS_DOWNLOAD)),
) -> Response:
    return download_report(report_id=report_id, fmt="pdf", db=db, user=user)
