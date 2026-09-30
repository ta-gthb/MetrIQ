"""Public verification of a printed report (PRD 17.4).

A report carries a verification code and a QR mark. Anyone holding the document
can check it here, without an account, and learn whether the record it names
still hashes to what the document claims.

The endpoint is deliberately narrow: it answers with the report's identity, the
result it records and whether its hash still matches. It does not return
observations, evidence, personnel or anything else from the case, because none
of those are needed to check a document.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import GeneratedReport
from app.security import rate_limit
from app.services import audit_service
from app.services.report_engine.snapshot import content_hash

router = APIRouter(tags=["Verification"])

MAX_CODE_LENGTH = 32


def verification_payload(report: GeneratedReport) -> dict:
    snapshot = report.data_snapshot or {}
    meta = snapshot.get("meta") or {}
    cover = snapshot.get("cover") or {}
    versions = snapshot.get("versions") or {}
    summary = snapshot.get("summary") or {}
    hash_matches = bool(
        report.content_hash
        and snapshot
        and content_hash(snapshot) == report.content_hash
    )
    return {
        "verified": True,
        "code": report.verification_code,
        "report_no": report.report_no,
        "revision_no": report.revision_no,
        "report_status": report.status,
        "is_immutable": report.is_immutable,
        "application_no": meta.get("application_no"),
        "laboratory": cover.get("laboratory_name"),
        "standard": f"{versions.get('standard') or ''} {versions.get('standard_edition') or ''}".strip() or None,
        "ruleset_label": report.ruleset_label,
        "template_label": report.template_label,
        "generated_at": report.generated_at,
        "locked_at": report.locked_at,
        "overall_result": summary.get("overall"),
        "content_hash": report.content_hash,
        "hash_matches": hash_matches,
        "statement": (
            "The stored record still hashes to the value this document carries."
            if hash_matches
            else "The stored record no longer matches the hash this document carries. Treat the document as unverified and contact the issuing laboratory."
        ),
    }


@router.get("/verify/{code}", summary="Check a report against its verification code")
def verify(code: str, request: Request, db: Session = Depends(get_db)) -> dict:
    rate_limit.check(request, rate_limit.VERIFY)
    normalised = (code or "").strip().upper()
    if not normalised or len(normalised) > MAX_CODE_LENGTH or not normalised.isalnum():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No report is registered under that verification code.",
        )
    report = db.execute(
        select(GeneratedReport).where(GeneratedReport.verification_code == normalised)
    ).scalars().first()
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No report is registered under that verification code.",
        )
    payload = verification_payload(report)
    # A scan is an event worth recording: it is how a laboratory learns that a
    # document is being checked, and from where.
    audit_service.record(
        db,
        event_type="VERIFY",
        entity_type="generated_report",
        entity_id=report.id,
        case_id=report.case_id,
        after={"hash_matches": payload["hash_matches"]},
        extra={"verification_code": report.verification_code, "anonymous": True},
    )
    db.commit()
    return payload