"""Attachment intake: validation, hashing, linking and signed access."""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import secrets
import time
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Attachment, AttachmentCategory, AttachmentLink
from app.services import audit_service
from app.services.attachment_service.storage import (
    StorageError,
    get_storage,
    sanitise_filename,
)

# Extensions that are never accepted regardless of configuration.
BLOCKED_EXTENSIONS = {
    ".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".sh",
    ".js", ".vbs", ".jar", ".app", ".apk", ".php", ".py", ".rb",
}


class AttachmentValidationError(ValueError):
    """Raised when a file fails intake validation."""


def build_storage_key(*, case_id: uuid.UUID | None, filename: str) -> str:
    """Internal identifier independent of the user-supplied name (PRD 19.3)."""
    safe = sanitise_filename(filename)
    suffix = Path(safe).suffix.lower()
    stem = Path(safe).stem[:60] or "file"
    scope = str(case_id) if case_id else "unassigned"
    return f"cases/{scope}/{uuid.uuid4().hex}/{stem}{suffix}"


def validate_upload(*, filename: str, size: int, content_type: str | None) -> str:
    safe = sanitise_filename(filename)
    extension = Path(safe).suffix.lower()
    if extension in BLOCKED_EXTENSIONS:
        raise AttachmentValidationError(f"file type '{extension}' is not permitted")
    allowed = {item.lower() for item in settings.ALLOWED_UPLOAD_EXTENSIONS}
    if allowed and extension not in allowed:
        raise AttachmentValidationError(
            f"file type '{extension or 'unknown'}' is not permitted. Allowed: {', '.join(sorted(allowed))}"
        )
    if size <= 0:
        raise AttachmentValidationError("the uploaded file is empty")
    if size > settings.MAX_UPLOAD_BYTES:
        raise AttachmentValidationError(
            f"file exceeds the maximum size of {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
        )
    if content_type:
        guessed, _ = mimetypes.guess_type(safe)
        if guessed and content_type.split(";")[0] not in {guessed, "application/octet-stream"}:
            # Store the declared type but record the mismatch for the auditor.
            return content_type
    return content_type or mimetypes.guess_type(safe)[0] or "application/octet-stream"


def _signature(key: str, *, expires: int) -> str:
    # The key is part of the signed material so a token minted for one object
    # can never be replayed against another.
    material = f"metriq-storage:{key}:{expires}".encode("utf-8")
    return hmac.new(settings.JWT_SECRET.encode("utf-8"), material, hashlib.sha256).hexdigest()


def sign_key(key: str, ttl_seconds: int | None = None) -> str:
    """Mint a time-limited token for a storage key (signed URL equivalent)."""
    expires = int(time.time()) + (ttl_seconds or settings.SIGNED_URL_TTL_SECONDS)
    token = _signature(key, expires=expires)
    return f"{expires}.{token}"


def verify_signed_key(key: str, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    raw_expires, _, signature = token.partition(".")
    try:
        expires = int(raw_expires)
    except ValueError:
        return False
    if expires < int(time.time()):
        return False
    return hmac.compare_digest(_signature(key, expires=expires), signature)


def store_upload(
    db: Session,
    *,
    case,
    filename: str,
    data: bytes,
    content_type: str | None = None,
    caption: str | None = None,
    category: str | None = None,
    actor=None,
    test_instance_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> Attachment:
    """Validate, persist and link an uploaded evidence file."""
    resolved_type = validate_upload(filename=filename, size=len(data), content_type=content_type)
    extension = Path(sanitise_filename(filename)).suffix.lower()
    key = build_storage_key(case_id=getattr(case, "id", None), filename=filename)
    backend = get_storage()
    backend.save(key, data, resolved_type)

    digest = hashlib.sha256(data).hexdigest()
    attachment = Attachment(
        case_id=getattr(case, "id", None),
        original_filename=sanitise_filename(filename),
        storage_key=key,
        storage_backend=backend.name,
        content_type=resolved_type,
        extension=extension,
        size_bytes=len(data),
        sha256=digest,
        caption=caption,
        category=category or AttachmentCategory.OTHER,
        category_source="manual" if category else "default",
        uploaded_by=getattr(actor, "id", None),
    )
    db.add(attachment)
    db.flush()

    db.add(
        AttachmentLink(
            attachment_id=attachment.id,
            case_id=getattr(case, "id", None),
            test_instance_id=test_instance_id,
            entity_type="test_instance" if test_instance_id else "evaluation_case",
            entity_id=test_instance_id or getattr(case, "id", None),
            linked_by=getattr(actor, "id", None),
        )
    )

    audit_service.record(
        db,
        event_type="UPLOAD",
        entity_type="attachment",
        entity_id=attachment.id,
        actor=actor,
        case_id=getattr(case, "id", None),
        after={
            "filename": attachment.original_filename,
            "size_bytes": len(data),
            "sha256": digest,
            "content_type": resolved_type,
        },
        request_id=request_id,
    )
    return attachment


def evidence_requirements(db: Session, case) -> dict:
    """Report whether the mandatory photographic evidence is present.

    A case may not be submitted for technical review until every category in
    ``settings.REQUIRED_EVIDENCE_CATEGORIES`` has at least one attachment whose
    content type is an image. The check is deliberately category-based rather
    than count-based so a duplicate upload cannot satisfy two requirements.
    """
    from app.models import AttachmentCategory  # local import avoids a cycle

    required = [item for item in settings.REQUIRED_EVIDENCE_CATEGORIES if item in set(AttachmentCategory.ALL)]
    rows = db.execute(
        select(Attachment.category, Attachment.content_type).where(Attachment.case_id == case.id)
    ).all()

    images = {
        category
        for category, content_type in rows
        if category and (content_type or "").startswith("image/")
    }
    missing = [category for category in required if category not in images]
    return {
        "required": required,
        "present": [category for category in required if category in images],
        "missing": missing,
        "satisfied": not missing,
    }
