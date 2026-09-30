"""Evidence upload, listing, download and AI classification (FR-09, PRD 19.3)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.dependencies.permissions import require_any_permission
from app.models import Attachment, AttachmentCategory, AttachmentLink, TestInstance, User
from app.routers._helpers import get_case_or_404
from app.security.permissions import P
from app.security.scope import case_editable_by
from app.schemas.evidence import AttachmentLinkIn
from app.services import audit_service
from app.services.attachment_service.service import evidence_requirements
from app.services.attachment_service import (
    AttachmentValidationError,
    sign_key,
    store_upload,
    verify_signed_key,
)
from app.services.attachment_service.storage import get_storage
from app.services.ai_service import get_ai_service

router = APIRouter(tags=["Attachments"])


def _serialise(attachment: Attachment) -> dict:
    links = []
    session = object_session(attachment)
    for link in attachment.links or []:
        test = (
            session.get(TestInstance, link.test_instance_id)
            if session is not None and link.test_instance_id
            else None
        )
        links.append(
            {
                "id": link.id,
                "test_instance_id": link.test_instance_id,
                "test_code": test.definition.test_code
                if test is not None and test.definition
                else None,
                "revision_no": test.revision_no if test is not None else None,
                "entity_type": link.entity_type,
                "entity_id": link.entity_id,
            }
        )
    return {
        "id": attachment.id,
        "case_id": attachment.case_id,
        "original_filename": attachment.original_filename,
        "content_type": attachment.content_type,
        "extension": attachment.extension,
        "size_bytes": attachment.size_bytes,
        "sha256": attachment.sha256,
        "caption": attachment.caption,
        "category": attachment.category,
        "category_source": attachment.category_source,
        "classification_confidence": attachment.classification_confidence,
        "uploaded_by": attachment.uploaded_by,
        "created_at": attachment.created_at,
        "download_url": f"/api/v1/attachments/{attachment.id}/download?token={sign_key(attachment.storage_key)}",
        "advisory_category": attachment.category_source == "ai",
        "links": links,
    }





@router.post(
    "/cases/{case_id}/attachments",
    status_code=status.HTTP_201_CREATED,
    summary="Upload evidence",
)
async def upload_attachment(
    case_id: uuid.UUID,
    file: UploadFile = File(...),
    caption: str | None = Form(None),
    category: str | None = Form(None),
    test_instance_id: uuid.UUID | None = Form(None),
    auto_classify: bool = Form(True),
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.TESTS_EDIT, P.TESTS_EDIT_OWN, P.CASES_CREATE)),
) -> dict:
    case = get_case_or_404(db, case_id, user)
    if not case_editable_by(user, case) and case.status not in {"UNDER_REVIEW"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Evidence can only be added while the case is editable or under review.",
        )
    if category and category not in set(AttachmentCategory.ALL):
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of: {', '.join(AttachmentCategory.ALL)}",
        )
    if test_instance_id is not None:
        test = db.get(TestInstance, test_instance_id)
        if test is None or test.case_id != case.id:
            raise HTTPException(
                status_code=422,
                detail="That test belongs to a different evaluation case.",
            )
        if test.superseded_at is not None:
            raise HTTPException(
                status_code=409,
                detail="That test record has been superseded; link the evidence to its live revision.",
            )

    data = await file.read()
    try:
        attachment = store_upload(
            db,
            case=case,
            filename=file.filename or "upload",
            data=data,
            content_type=file.content_type,
            caption=caption,
            category=category,
            actor=user,
            test_instance_id=test_instance_id,
        )
    except AttachmentValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    classification = None
    if auto_classify and not category:
        service = get_ai_service(db, actor=user)
        result = service.classify_document(attachment=attachment)
        classification = result.as_dict()

    db.commit()
    db.refresh(attachment)
    payload = _serialise(attachment)
    payload["classification"] = classification
    return payload


@router.get("/cases/{case_id}/attachments", summary="List evidence for a case")
def list_attachments(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[dict]:
    case = get_case_or_404(db, case_id, user)
    rows = db.execute(
        select(Attachment).where(Attachment.case_id == case.id).order_by(Attachment.created_at.desc())
    ).scalars().all()
    return [_serialise(row) for row in rows]


@router.post(
    "/attachments/{attachment_id}/links",
    status_code=status.HTTP_201_CREATED,
    summary="Link a piece of evidence to a test",
)
def link_attachment(
    attachment_id: uuid.UUID,
    payload: AttachmentLinkIn,
    db: Session = Depends(get_db),
    user: User = Depends(
        require_any_permission(P.TESTS_EDIT, P.TESTS_EDIT_OWN, P.CASES_EDIT)
    ),
) -> dict:
    """Attach evidence to the test it supports (audit item 12).

    The link is what the evidence gate reads: evidence a test procedure requires
    is satisfied by a link to that test, not by an attachment somewhere on the
    case. A superseded test cannot receive new evidence - its replacement is the
    live record.
    """
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if attachment.case_id is None:
        raise HTTPException(status_code=409, detail="This attachment is not filed against a case.")
    case = get_case_or_404(db, attachment.case_id, user)
    if not case_editable_by(user, case):
        raise HTTPException(
            status_code=409, detail="Evidence cannot be linked in the current status."
        )

    test = db.get(TestInstance, payload.test_instance_id)
    if test is None:
        raise HTTPException(status_code=404, detail="Test instance not found")
    if test.case_id != case.id:
        raise HTTPException(
            status_code=422, detail="That test belongs to a different evaluation case."
        )
    if test.superseded_at is not None:
        raise HTTPException(
            status_code=409,
            detail="That test record has been superseded; link the evidence to its live revision.",
        )
    duplicate = db.execute(
        select(AttachmentLink).where(
            AttachmentLink.attachment_id == attachment.id,
            AttachmentLink.test_instance_id == test.id,
        )
    ).scalars().first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="That evidence is already linked to this test.")

    link = AttachmentLink(
        attachment_id=attachment.id,
        case_id=case.id,
        test_instance_id=test.id,
        entity_type="test_instance",
        linked_by=user.id,
    )
    db.add(link)
    db.flush()
    audit_service.record(
        db,
        event_type="CREATE",
        entity_type="attachment_link",
        entity_id=link.id,
        actor=user,
        case_id=case.id,
        after={
            "attachment_id": str(attachment.id),
            "test_instance_id": str(test.id),
            "test_code": test.definition.test_code if test.definition else None,
        },
    )
    db.commit()
    db.refresh(attachment)
    return _serialise(attachment)


@router.delete(
    "/attachments/{attachment_id}/links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Remove a link between evidence and a test",
)
def unlink_attachment(
    attachment_id: uuid.UUID,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(
        require_any_permission(P.TESTS_EDIT, P.TESTS_EDIT_OWN, P.CASES_EDIT)
    ),
) -> None:
    link = db.get(AttachmentLink, link_id)
    if link is None or link.attachment_id != attachment_id:
        raise HTTPException(status_code=404, detail="Link not found")
    attachment = db.get(Attachment, attachment_id)
    case = get_case_or_404(db, attachment.case_id, user)
    if not case_editable_by(user, case):
        raise HTTPException(
            status_code=409, detail="Evidence cannot be unlinked in the current status."
        )
    db.delete(link)
    db.flush()
    audit_service.record(
        db,
        event_type="DELETE",
        entity_type="attachment_link",
        entity_id=link_id,
        actor=user,
        case_id=case.id,
        before={"attachment_id": str(attachment_id), "test_instance_id": str(link.test_instance_id)},
    )
    db.commit()


@router.get(
    "/cases/{case_id}/evidence-requirements",
    summary="Mandatory photographic evidence still outstanding for a case",
)
def case_evidence_requirements(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    case = get_case_or_404(db, case_id, user)
    return evidence_requirements(db, case)


@router.get("/attachments/{attachment_id}/download", summary="Download evidence via a signed URL")
def download_attachment(
    attachment_id: uuid.UUID,
    token: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Response:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if attachment.case_id:
        get_case_or_404(db, attachment.case_id, user)
    if not verify_signed_key(attachment.storage_key, token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A valid, unexpired signed URL is required to read this file.",
        )
    try:
        payload = get_storage().read(attachment.storage_key)
    except Exception as exc:
        raise HTTPException(status_code=410, detail=f"The stored file is unavailable: {exc}") from exc

    audit_service.record(
        db, event_type="DOWNLOAD", entity_type="attachment", entity_id=attachment.id, actor=user,
        case_id=attachment.case_id, extra={"filename": attachment.original_filename},
    )
    db.commit()
    return Response(
        content=payload,
        media_type=attachment.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{attachment.original_filename}"',
            "X-Content-SHA256": attachment.sha256 or "",
        },
    )


@router.post("/attachments/{attachment_id}/classify", summary="Classify evidence with AI")
def classify_attachment(
    attachment_id: uuid.UUID,
    apply: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.AI_USE, P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
) -> dict:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if attachment.case_id:
        get_case_or_404(db, attachment.case_id, user)
    service = get_ai_service(db, actor=user)
    result = service.classify_document(attachment=attachment)
    if not apply:
        attachment.category_source = attachment.category_source or "manual"
    db.commit()
    return result.as_dict()


@router.patch("/attachments/{attachment_id}", summary="Correct the caption or category")
def update_attachment(
    attachment_id: uuid.UUID,
    caption: str | None = Form(None),
    category: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
) -> dict:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if attachment.case_id:
        get_case_or_404(db, attachment.case_id, user)
    before = {"caption": attachment.caption, "category": attachment.category}
    if caption is not None:
        attachment.caption = caption
    if category is not None:
        if category not in set(AttachmentCategory.ALL):
            raise HTTPException(status_code=422, detail="Unsupported category.")
        attachment.category = category
        attachment.category_source = "manual"
    audit_service.record(
        db, event_type="EDIT", entity_type="attachment", entity_id=attachment.id, actor=user,
        case_id=attachment.case_id, before=before,
        after={"caption": attachment.caption, "category": attachment.category},
    )
    db.commit()
    return _serialise(attachment)
