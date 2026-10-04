"""Report generation, download and repository search (PRD 17, 15.1, audit item 13)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.dependencies.permissions import require_any_permission, require_permission
from app.models import (
    CaseStatus,
    EvaluationCase,
    GeneratedReport,
    Instrument,
    ReportRevision,
    User,
)
from app.routers._helpers import get_case_or_404, paginate
from app.schemas.common import Paginated
from app.schemas.reports import (
    GeneratedReportOut,
    ReportGenerateRequest,
    ReportListItemOut,
    ReportRevisionOut,
)
from app.security.permissions import P
from app.security.scope import case_scope_clause
from app.services import audit_service
from app.services.report_engine import generate_report
from app.services.report_engine.comparison import compare_snapshots
from app.services.report_engine.service import read_artefact

router = APIRouter(tags=["Reports"])

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


APPROVED_STATUSES = {CaseStatus.APPROVED, CaseStatus.FINALIZED}


def _load_report(
    db: Session, report_id: uuid.UUID, user: User, *, require_approved: bool = False
) -> GeneratedReport:
    report = db.get(GeneratedReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    case = get_case_or_404(db, report.case_id, user)
    if require_approved and case.status not in APPROVED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This evaluation has not been approved by the Approving Authority, so its "
                "report cannot be downloaded. A report is released only after final approval."
            ),
        )
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
    if case.status not in APPROVED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A report can only be generated after the Approving Authority has approved "
                f"the evaluation. The case is currently in status {case.status}."
            ),
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


def _repository_statement(
    user: User,
    *,
    search: str | None = None,
    only_final: bool = False,
    instrument_id: uuid.UUID | None = None,
    result: str | None = None,
    ruleset: str | None = None,
    generated_from: datetime | None = None,
    generated_to: datetime | None = None,
):
    """The repository query, shared by search, export and the UI counters.

    Record scope comes first: a laboratory-scoped user only sees its own
    laboratory, and an engineer only the cases assigned to them. The remaining
    filters narrow that set (audit item 13).
    """
    statement = (
        select(GeneratedReport)
        .join(EvaluationCase, EvaluationCase.id == GeneratedReport.case_id)
        .join(Instrument, Instrument.id == EvaluationCase.instrument_id)
        .order_by(GeneratedReport.created_at.desc())
    )
    scope = case_scope_clause(user)
    if scope is not None:
        statement = statement.where(scope)
    if only_final:
        statement = statement.where(GeneratedReport.is_immutable.is_(True))
    if instrument_id is not None:
        statement = statement.where(EvaluationCase.instrument_id == instrument_id)
    if ruleset:
        statement = statement.where(GeneratedReport.ruleset_label.ilike(f"%{ruleset}%"))
    if result:
        statement = statement.where(
            GeneratedReport.data_snapshot["summary"]["overall"].as_string() == result.upper()
        )
    if generated_from is not None:
        statement = statement.where(GeneratedReport.generated_at >= generated_from)
    if generated_to is not None:
        statement = statement.where(GeneratedReport.generated_at <= generated_to)
    if search:
        pattern = f"%{search}%"
        statement = statement.where(
            GeneratedReport.report_no.ilike(pattern)
            | EvaluationCase.application_no.ilike(pattern)
            | EvaluationCase.title.ilike(pattern)
            | Instrument.model.ilike(pattern)
            | Instrument.serial_number.ilike(pattern)
        )
    return statement


def _repository_row(report: GeneratedReport) -> dict:
    case = report.case
    instrument = case.instrument if case else None
    return {
        "id": report.id,
        "report_no": report.report_no,
        "application_no": case.application_no if case else None,
        "case_id": report.case_id,
        "status": report.status,
        "revision_no": report.revision_no,
        "is_immutable": report.is_immutable,
        "ruleset_label": report.ruleset_label,
        "template_label": report.template_label,
        "verification_code": report.verification_code,
        "generated_at": report.generated_at,
        "overall_result": (report.data_snapshot or {}).get("summary", {}).get("overall"),
        "instrument_id": instrument.id if instrument else None,
        "instrument_model": instrument.model if instrument else None,
        "instrument_serial_number": instrument.serial_number if instrument else None,
    }


@router.get("/reports", response_model=Paginated[ReportListItemOut], summary="Search the repository")
def search_reports(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    search: str | None = Query(None, description="Number, title, instrument model or serial"),
    only_final: bool = False,
    instrument_id: uuid.UUID | None = Query(None, description="Limit to one instrument"),
    result: str | None = Query(None, description="Overall result: PASS or FAIL"),
    ruleset: str | None = Query(None, description="Rule-set version label"),
    generated_from: datetime | None = Query(None, description="Generated on or after this instant"),
    generated_to: datetime | None = Query(None, description="Generated on or before this instant"),
    page: int = 1,
    page_size: int = Query(25, le=200),
) -> Paginated[ReportListItemOut]:
    statement = _repository_statement(
        user,
        search=search,
        only_final=only_final,
        instrument_id=instrument_id,
        result=result,
        ruleset=ruleset,
        generated_from=generated_from,
        generated_to=generated_to,
    )
    rows, meta = paginate(db, statement, page=page, page_size=page_size)
    return Paginated[ReportListItemOut](
        items=[ReportListItemOut(**_repository_row(report)) for report in rows], meta=meta
    )


CSV_COLUMNS = (
    "report_no",
    "application_no",
    "case_id",
    "instrument_model",
    "instrument_serial_number",
    "status",
    "overall_result",
    "revision_no",
    "ruleset_label",
    "template_label",
    "generated_at",
    "is_immutable",
    "verification_code",
)


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@router.get("/reports/export.csv", summary="Export the repository as CSV")
def export_reports_csv(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    search: str | None = None,
    only_final: bool = False,
    instrument_id: uuid.UUID | None = None,
    result: str | None = None,
    ruleset: str | None = None,
    generated_from: datetime | None = None,
    generated_to: datetime | None = None,
    limit: int = Query(5000, le=20000),
) -> Response:
    """The same filtered list a reviewer sees, as a file they can archive.

    The export answers the questions the repository asks in the same scope as
    the screen: no filter combination can widen what the caller may read.
    """
    statement = _repository_statement(
        user,
        search=search,
        only_final=only_final,
        instrument_id=instrument_id,
        result=result,
        ruleset=ruleset,
        generated_from=generated_from,
        generated_to=generated_to,
    ).limit(limit)
    reports = db.execute(statement).scalars().all()

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS))
    writer.writeheader()
    for report in reports:
        row = _repository_row(report)
        writer.writerow({column: _csv_cell(row.get(column)) for column in CSV_COLUMNS})

    audit_service.record(
        db,
        event_type="EXPORT",
        entity_type="generated_report",
        actor=user,
        extra={"format": "csv", "rows": len(reports), "filters": {
            "search": search,
            "only_final": only_final,
            "instrument_id": str(instrument_id) if instrument_id else None,
            "result": result,
            "ruleset": ruleset,
        }},
    )
    db.commit()
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="metriq-repository.csv"'},
    )


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


def _revision_snapshot(db: Session, report: GeneratedReport, number: int) -> dict:
    """The snapshot one revision printed, or a clear refusal for old rows."""
    row = db.execute(
        select(ReportRevision)
        .where(ReportRevision.report_id == report.id, ReportRevision.revision_no == number)
        .order_by(ReportRevision.format)
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Revision {number} does not exist.")
    snapshot = row.data_snapshot
    if snapshot is None and number == report.revision_no:
        snapshot = report.data_snapshot
    if snapshot is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Revision {number} was generated before snapshots were retained per "
                "revision; its printed document can still be downloaded."
            ),
        )
    return snapshot


@router.get(
    "/reports/{report_id}/revisions/{revision_no}/snapshot",
    summary="The snapshot one report revision printed",
)
def report_revision_snapshot(
    report_id: uuid.UUID,
    revision_no: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    report = _load_report(db, report_id, user)
    return _revision_snapshot(db, report, revision_no)


@router.get("/reports/{report_id}/compare", summary="Compare two report revisions")
def compare_report_revisions(
    report_id: uuid.UUID,
    left: int = Query(..., ge=1, description="One revision number"),
    right: int = Query(..., ge=1, description="The other revision number"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    """What changed between two revisions: rules, results, readings, evidence."""
    report = _load_report(db, report_id, user)
    if left == right:
        raise HTTPException(status_code=422, detail="Choose two different revisions to compare.")
    first, second = sorted((left, right))
    return compare_snapshots(
        _revision_snapshot(db, report, first), _revision_snapshot(db, report, second)
    )


@router.get("/instruments/{instrument_id}/history", summary="Every evaluation of one instrument")
def instrument_history(
    instrument_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    """The instrument's evaluations, newest first, with the latest report on each.

    This is the repository view of one instrument: what was evaluated, how it
    ended, and where the report is (audit item 13).
    """
    instrument = db.get(Instrument, instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="Instrument not found")

    statement = (
        select(EvaluationCase)
        .where(EvaluationCase.instrument_id == instrument.id)
        .order_by(EvaluationCase.created_at.desc())
    )
    scope = case_scope_clause(user)
    if scope is not None:
        statement = statement.where(scope)
    cases = db.execute(statement).scalars().all()

    latest: dict[uuid.UUID, GeneratedReport] = {}
    if cases:
        reports = db.execute(
            select(GeneratedReport)
            .where(GeneratedReport.case_id.in_([case.id for case in cases]))
            .order_by(GeneratedReport.generated_at.desc())
        ).scalars().all()
        for report in reports:
            latest.setdefault(report.case_id, report)

    return {
        "instrument": {
            "id": instrument.id,
            "model": instrument.model,
            "type_designation": instrument.type_designation,
            "serial_number": instrument.serial_number,
            "instrument_class": instrument.instrument_class,
            "manufacturer": instrument.manufacturer.name if instrument.manufacturer else None,
        },
        "cases": [
            {
                "case_id": case.id,
                "application_no": case.application_no,
                "title": case.title,
                "status": case.status,
                "created_at": case.created_at,
                "finalized_at": case.finalized_at,
                "report": {
                    "id": report.id,
                    "report_no": report.report_no,
                    "revision_no": report.revision_no,
                    "is_immutable": report.is_immutable,
                    "overall_result": (report.data_snapshot or {})
                    .get("summary", {})
                    .get("overall"),
                    "verification_code": report.verification_code,
                    "generated_at": report.generated_at,
                }
                if (report := latest.get(case.id))
                else None,
            }
            for case in cases
        ],
    }


@router.get("/reports/{report_id}/download", summary="Download the latest PDF or DOCX")
def download_report(
    report_id: uuid.UUID,
    fmt: str = Query("pdf"),
    revision: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.REPORTS_DOWNLOAD)),
) -> Response:
    report = _load_report(db, report_id, user, require_approved=True)
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
