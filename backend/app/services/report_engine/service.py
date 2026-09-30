"""Report generation orchestration: snapshot -> renders -> storage -> record."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EvaluationCase, GeneratedReport, ReportRevision
from app.services import audit_service
from app.services.report_engine.docx import render_docx
from app.services.report_engine.pdf import render_pdf
from app.services.report_engine.snapshot import (
    build_report_snapshot,
    canonical_json,
    content_hash,
    verification_code,
)

RENDERERS = {"pdf": render_pdf, "docx": render_docx}

# Report artefacts live in the same object store as the evidence files, under a
# reserved prefix, so a deployed instance holds no durable state on local disk
# (Render's filesystem is ephemeral). See docs/deployment/README.md.
ARTEFACT_PREFIX = "reports"
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def artefact_key(report_no: str, revision_no: int, fmt: str) -> str:
    return f"{ARTEFACT_PREFIX}/{report_no}-R{revision_no}.{fmt.lower()}"


def read_artefact(storage_key: str | None) -> bytes | None:
    """Read a stored report artefact, or ``None`` if it is no longer available.

    Keys written since deployment are object-store keys. Rows created before the
    artefacts moved off local disk recorded an absolute path instead, so both
    shapes are accepted on read.
    """
    from app.services.attachment_service.storage import StorageError, get_storage

    if not storage_key:
        return None
    if not storage_key.startswith(f"{ARTEFACT_PREFIX}/"):
        legacy = Path(storage_key)
        if legacy.is_file():
            return legacy.read_bytes()
    try:
        return get_storage().read(storage_key)
    except StorageError:
        return None


def render_document(snapshot: dict, fmt: str) -> bytes:
    renderer = RENDERERS.get(fmt.lower())
    if renderer is None:
        raise ValueError(f"unsupported report format '{fmt}'. Supported: {sorted(RENDERERS)}")
    return renderer(snapshot)


def next_report_number(db: Session, case: EvaluationCase) -> str:
    year = (case.created_at or datetime.now(timezone.utc)).year
    count = db.execute(
        select(func.count(GeneratedReport.id)).where(
            GeneratedReport.report_no.like(f"RPT-{year}-%")
        )
    ).scalar_one()
    return f"RPT-{year}-{count + 1:05d}"


def _stored_artefacts(db: Session, report: GeneratedReport, formats: tuple[str, ...]) -> dict[str, bytes]:
    """Return the already-rendered bytes for a locked report.

    A finalized report is immutable, so its artefacts are read back from storage
    rather than re-rendered; re-rendering would let a timestamp or an upgraded
    renderer silently change the document behind a stable hash.
    """
    rows = db.execute(
        select(ReportRevision).where(ReportRevision.report_id == report.id)
    ).scalars().all()
    artefacts: dict[str, bytes] = {}
    for row in rows:
        if row.format not in formats:
            continue
        payload = read_artefact(row.storage_key)
        if payload is not None:
            artefacts[row.format] = payload
        elif report.data_snapshot:
            artefacts[row.format] = render_document(report.data_snapshot, row.format)
    return artefacts


def generate_report(
    db: Session,
    *,
    case: EvaluationCase,
    actor=None,
    formats: tuple[str, ...] = ("pdf", "docx"),
    lock: bool = False,
    request_id: str | None = None,
) -> tuple[GeneratedReport, dict[str, bytes]]:
    """Generate report artefacts for a case and record the revisions.

    Locking is idempotent: finalizing a case that already has an immutable
    report for the current case revision returns that report unchanged, so the
    content hash and verification code stay stable (PRD 17.4).
    """
    if lock:
        locked = db.execute(
            select(GeneratedReport)
            .where(
                GeneratedReport.case_id == case.id,
                GeneratedReport.is_immutable.is_(True),
            )
            .order_by(GeneratedReport.revision_no.desc())
        ).scalars().first()
        if locked is not None and locked.case_revision_no == case.revision_no:
            return locked, _stored_artefacts(db, locked, formats)

    report = db.execute(
        select(GeneratedReport)
        .where(GeneratedReport.case_id == case.id)
        .order_by(GeneratedReport.revision_no.desc())
    ).scalars().first()
    revision_no = 1 if report is None else report.revision_no + 1
    if report is None:
        report = GeneratedReport(
            case_id=case.id,
            report_no=next_report_number(db, case),
            revision_no=revision_no,
            generated_by=getattr(actor, "id", None),
            generated_at=datetime.now(timezone.utc),
        )
        db.add(report)
    report.revision_no = revision_no
    report.case_revision_no = case.revision_no
    report.standard_version_id = case.standard_version_id
    report.template_version_id = case.template_version_id
    report.ruleset_label = case.standard_version.version_label if case.standard_version else None
    report.template_label = case.template_version.version_label if case.template_version else None
    report.status = "FINAL" if lock else "DRAFT"
    report.generated_by = getattr(actor, "id", None)
    report.generated_at = datetime.now(timezone.utc)
    db.flush()

    snapshot, digest = build_report_snapshot(
        db,
        case=case,
        revision_no=revision_no,
        report_no=report.report_no,
        generated_by=getattr(actor, "full_name", None),
    )
    # Recompute after injecting identifiers so the hash covers the final
    # document. Volatile generation metadata is excluded (content_hash() owns
    # that rule): the hash must identify the content, not the moment it was
    # rendered, so the same records always produce the same verification code.
    digest = content_hash(snapshot)
    snapshot["meta"]["content_hash"] = digest
    snapshot["meta"]["verification_code"] = verification_code(digest)
    report.data_snapshot = snapshot
    report.content_hash = digest
    report.verification_code = snapshot["meta"]["verification_code"]
    if lock:
        report.is_immutable = True
        report.locked_at = datetime.now(timezone.utc)
        report.locked_by = getattr(actor, "id", None)

    from app.services.attachment_service.storage import get_storage

    artefacts: dict[str, bytes] = {}
    storage = get_storage()
    for fmt in formats:
        payload = render_document(snapshot, fmt)
        artefacts[fmt] = payload
        checksum = hashlib.sha256(payload).hexdigest()
        key = artefact_key(report.report_no, revision_no, fmt)
        storage.save(key, payload, CONTENT_TYPES.get(fmt))
        db.add(
            ReportRevision(
                report_id=report.id,
                revision_no=revision_no,
                format=fmt,
                storage_key=key,
                size_bytes=len(payload),
                sha256=checksum,
                case_revision_no=case.revision_no,
                change_summary=f"Generated from case revision {case.revision_no}",
                generated_by=getattr(actor, "id", None),
                data_snapshot=snapshot,
            )
        )

    audit_service.record(
        db,
        event_type="REPORT_GENERATE",
        entity_type="generated_report",
        entity_id=report.id,
        actor=actor,
        case_id=case.id,
        after={
            "report_no": report.report_no,
            "revision_no": revision_no,
            "formats": list(formats),
            "content_hash": digest,
            "locked": lock,
        },
        request_id=request_id,
    )
    return report, artefacts
