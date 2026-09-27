"""AI assistance endpoints (PRD 12, 16.2)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.dependencies.permissions import require_any_permission, require_permission
from app.models import (
    AIEvent,
    Attachment,
    ReportTemplateVersion,
    Rule,
    RuleVersion,
    StandardVersion,
    SystemSetting,
    TestDefinition,
    User,
    utcnow,
)
from app.routers._helpers import get_case_or_404
from app.schemas.ai import (
    AIAnomalyCheckRequest,
    AIClassifyRequest,
    AIDispositionRequest,
    AIKnowledgeRequest,
)
from app.security.permissions import P
from app.services import audit_service
from app.services.ai_service import (
    FEATURE_ANOMALY,
    FEATURE_ASSISTANT,
    FEATURE_CLASSIFY,
    FEATURE_NAMEPLATE,
    get_ai_service,
)
from app.services.attachment_service.storage import get_storage

router = APIRouter(tags=["AI assistance"])

# The setting key must match the key the AI service reads (see
# AIService.feature_enabled); otherwise an administrator's toggle has no effect.
FEATURE_TOGGLES = [
    (FEATURE_NAMEPLATE, "ai.nameplate_extract", "Nameplate and instrument-photo extraction"),
    (FEATURE_ANOMALY, "ai.anomaly_detection", "Observation anomaly detection"),
    (FEATURE_CLASSIFY, "ai.document_classification", "Document classification"),
    (FEATURE_ASSISTANT, "ai.knowledge_assistant", "Retrieval-grounded R 76 assistant"),
]


@router.get("/ai/features", summary="AI feature availability and governance state")
def ai_features(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    service = get_ai_service(db, actor=user)
    return {
        "provider": settings.AI_PROVIDER,
        "enabled": settings.AI_ENABLED,
        "human_confirmation_required": True,
        "can_decide_compliance": False,
        "features": [
            {
                "code": feature_code,
                "setting_key": setting_key,
                "label": label,
                "enabled": service.feature_enabled(feature_code),
            }
            for feature_code, setting_key, label in FEATURE_TOGGLES
        ],
        "governance": {
            "advisory_only": True,
            "no_ai_compliance_decision": True,
            "traceability": "Every AI action is stored in ai_events with provider, model and disposition.",
            "failure_behaviour": "Calculation, workflow and reporting remain fully usable without AI.",
        },
    }


@router.patch("/ai/features/{feature_code}", summary="Enable or disable an AI feature")
def set_ai_feature(
    feature_code: str,
    enabled: bool,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.AI_MANAGE)),
) -> dict:
    match = next((item for item in FEATURE_TOGGLES if item[0] == feature_code), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Unknown AI feature '{feature_code}'")
    _feature, setting_key, label = match
    row = db.execute(select(SystemSetting).where(SystemSetting.key == setting_key)).scalars().first()
    if row is None:
        row = SystemSetting(key=setting_key, category="ai", description=label)
        db.add(row)
        db.flush()
    before = row.value
    row.value = {"enabled": bool(enabled)}
    row.updated_by = user.id
    audit_service.record(
        db, event_type="EDIT", entity_type="system_setting", entity_id=row.id, actor=user,
        field_changed=setting_key, before=before, after=row.value,
    )
    db.commit()
    return {"feature_code": feature_code, "enabled": bool(enabled), "label": label}


@router.post("/ai/nameplate-extract", summary="Extract instrument fields from a nameplate")
async def nameplate_extract(
    file: UploadFile | None = File(None),
    case_id: uuid.UUID | None = Form(None),
    test_instance_id: uuid.UUID | None = Form(None),
    attachment_id: uuid.UUID | None = Form(None),
    text_hint: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.AI_USE, P.AI_VIEW)),
) -> dict:
    case = None
    if case_id:
        case = get_case_or_404(db, case_id, user)

    image_bytes: bytes | None = None
    filename = "nameplate"
    if file is not None:
        image_bytes = await file.read()
        filename = file.filename or filename
    elif attachment_id:
        attachment = db.get(Attachment, attachment_id)
        if attachment is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
        if attachment.case_id:
            get_case_or_404(db, attachment.case_id, user)
        try:
            image_bytes = get_storage().read(attachment.storage_key)
        except Exception as exc:
            raise HTTPException(status_code=410, detail=f"Stored file unavailable: {exc}") from exc
        filename = attachment.original_filename
    elif text_hint:
        image_bytes = text_hint.encode("utf-8")
        filename = "nameplate.txt"
    else:
        raise HTTPException(
            status_code=422,
            detail="Supply a photograph, an existing attachment_id, or a text_hint.",
        )

    service = get_ai_service(db, actor=user)
    result = service.nameplate_extract(
        image_bytes=image_bytes,
        filename=filename,
        case=case,
        test_instance_id=test_instance_id,
        attachment_id=attachment_id,
    )
    db.commit()
    payload = result.as_dict()
    payload["notice"] = (
        "Extracted values are suggestions only. Confirm or correct every field before it is "
        "saved to the authoritative instrument record."
    )
    return payload


@router.post("/ai/anomaly-check", summary="Statistical review of an observation table")
def anomaly_check(
    payload: AIAnomalyCheckRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.AI_USE, P.AI_VIEW)),
) -> dict:
    from app.models import TestInstance

    case = get_case_or_404(db, payload.case_id, user)
    instance = db.get(TestInstance, payload.test_instance_id)
    if instance is None or instance.case_id != case.id:
        raise HTTPException(status_code=404, detail="Test instance not found on this case")
    service = get_ai_service(db, actor=user)
    report = service.anomaly_check(case=case, test_instance=instance)
    db.commit()
    return report.as_dict()


@router.post("/ai/classify", summary="Classify an uploaded document")
def classify(
    payload: AIClassifyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.AI_USE, P.AI_VIEW)),
) -> dict:
    attachment = db.get(Attachment, payload.attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if attachment.case_id:
        get_case_or_404(db, attachment.case_id, user)
    service = get_ai_service(db, actor=user)
    result = service.classify_document(attachment=attachment)
    db.commit()
    return result.as_dict()


def _build_assistant_sources(db: Session, standard_version_id: uuid.UUID | None) -> list[dict]:
    sources: list[dict] = []
    statement = (
        select(RuleVersion, Rule, StandardVersion)
        .join(Rule, Rule.id == RuleVersion.rule_id)
        .join(StandardVersion, StandardVersion.id == RuleVersion.standard_version_id)
        .where(RuleVersion.is_active.is_(True))
    )
    if standard_version_id:
        statement = statement.where(RuleVersion.standard_version_id == standard_version_id)
    for version, rule, standard_version in db.execute(statement).all():
        definition = version.definition or {}
        sources.append(
            {
                "code": rule.code,
                "title": rule.name,
                "category": rule.category,
                "clause_reference": rule.clause_reference,
                "text": (
                    f"{rule.description or ''} Formula: {version.formula or 'n/a'}. "
                    f"Threshold: {version.threshold or 'n/a'} {version.unit or ''}. "
                    f"Definition: {definition}"
                ),
                "version_label": version.version_label,
                "standard": standard_version.version_label,
                "review_status": version.review_status,
            }
        )

    definition_statement = select(TestDefinition).where(TestDefinition.is_active.is_(True))
    if standard_version_id:
        definition_statement = definition_statement.where(
            TestDefinition.standard_version_id == standard_version_id
        )
    for definition in db.execute(definition_statement).scalars().all():
        sources.append(
            {
                "code": definition.test_code,
                "title": definition.name,
                "category": "test_definition",
                "clause_reference": definition.clause_reference,
                "text": (
                    f"{definition.description or ''} Applicability: {definition.applicability_expression}. "
                    f"Calculation: {definition.calculation_rules}. Compliance: {definition.compliance_rules}."
                ),
                "version_label": None,
                "standard": "test catalogue",
            }
        )

    template_statement = select(ReportTemplateVersion).where(ReportTemplateVersion.is_active.is_(True))
    for version in db.execute(template_statement).scalars().all():
        for section in (version.section_map or {}).get("sections", []):
            sources.append(
                {
                    "code": f"{version.version_label}-{section.get('key')}",
                    "title": f"Report section {section.get('number')}: {section.get('title')}",
                    "category": "report_template",
                    "clause_reference": "OIML R 76-2:2007",
                    "text": (
                        f"{section.get('title')} "
                        f"({'required' if section.get('required') else 'optional'})."
                    ),
                    "version_label": version.version_label,
                    "standard": "OIML R 76-2",
                }
            )
    return sources


@router.post("/ai/knowledge", summary="Ask the retrieval-grounded R 76 assistant")
def knowledge(
    payload: AIKnowledgeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.AI_USE, P.AI_VIEW)),
) -> dict:
    sources = _build_assistant_sources(db, payload.standard_version_id)
    service = get_ai_service(db, actor=user)
    answer = service.answer_r76(question=payload.question, sources=sources)
    db.commit()
    result = answer.as_dict()
    result["source_count"] = len(sources)
    return result


@router.post("/ai/disposition", summary="Record the human disposition of an AI output")
def disposition(
    payload: AIDispositionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.AI_USE, P.TESTS_EDIT, P.TESTS_EDIT_OWN)),
) -> dict:
    event = db.get(AIEvent, payload.event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="AI event not found")
    event.user_disposition = payload.disposition
    event.disposition_at = utcnow()
    audit_service.record(
        db, event_type="AI_ACTION", entity_type="ai_event", entity_id=event.id, actor=user,
        case_id=event.case_id, after={"disposition": payload.disposition, "note": payload.note},
        extra={"feature_code": event.feature_code, "provider": event.provider},
    )
    db.commit()
    return {
        "event_id": event.id,
        "disposition": event.user_disposition,
        "recorded_at": event.disposition_at,
        "note": payload.note,
    }
