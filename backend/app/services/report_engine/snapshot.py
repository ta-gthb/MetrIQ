"""Build the immutable report data snapshot from structured records (PRD 17.3)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Attachment, EvaluationCase, ReportTemplateVersion, TestEquipmentUsage, TestInstance
from app.services.metrology_service import summarise_case
from app.utils.decimals import decimal_str


def _fmt(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _decimal(value) -> str | None:
    return decimal_str(value)


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def snapshot_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


#: Metadata that must not take part in the content hash: it records when a
#: snapshot was built, not what it says. Excluding it is what makes the
#: verification code reproducible from the report content alone.
VOLATILE_META_FIELDS = frozenset({"content_hash", "verification_code", "generated_at"})


def content_hash(snapshot: dict[str, Any]) -> str:
    """Hash the report content, ignoring when it was generated."""
    meta = {
        key: value
        for key, value in (snapshot.get("meta") or {}).items()
        if key not in VOLATILE_META_FIELDS
    }
    return snapshot_hash({**snapshot, "meta": meta})


def verification_code(digest: str, length: int = 12) -> str:
    return digest[:length].upper()


def _test_snapshot(test_instance: TestInstance) -> dict[str, Any]:
    result = test_instance.compliance_result
    run = test_instance.calculation_runs[-1] if test_instance.calculation_runs else None
    definition = test_instance.definition
    schema = definition.input_schema or {}
    rows: list[dict[str, Any]] = []
    if result and result.details:
        rows = list(result.details.get("rows") or [])
    if not rows:
        rows = [
            {
                "observation_no": obs.observation_no,
                "label": obs.position_label,
                "load": _decimal(obs.load),
                "indication": _decimal(obs.indication),
                "additional_load": _decimal(obs.additional_load),
                "value": _decimal(obs.value),
                "unit": obs.unit,
            }
            for obs in sorted(test_instance.observations, key=lambda item: item.observation_no)
        ]

    return {
        "test_code": definition.test_code,
        "name": definition.name,
        "clause_reference": definition.clause_reference,
        "category": definition.category,
        "applicability": {
            "status": test_instance.applicability_status,
            "reason": test_instance.applicability_reason,
            "trace": test_instance.applicability_trace or [],
        },
        "status": test_instance.status,
        "result_status": test_instance.result_status,
        "remarks": test_instance.remarks,
        "columns": list(schema.get("columns") or []),
        "layout": schema.get("layout", "table"),
        "rows": rows,
        "result": {
            "status": result.status if result else None,
            "measured_value": _decimal(result.measured_value) if result else None,
            "limit_value": _decimal(result.limit_value) if result else None,
            "margin": _decimal(result.margin) if result else None,
            "unit": result.unit if result else None,
            "rule_id": result.rule_id if result else None,
            "rule_version": result.rule_version if result else None,
            "clause_reference": result.clause_reference if result else None,
            "explanation": result.explanation if result else None,
        },
        "calculation": {
            "engine_version": run.engine_version if run else None,
            "rounding_policy": run.rounding_policy if run else None,
            "intermediates": (run.intermediates if run else None) or {},
            "inputs": (run.input_snapshot if run else None) or {},
            "calculated_at": _fmt(run.calculated_at) if run else None,
        },
        "report_section": definition.report_section_mapping or {},
    }


def build_report_snapshot(
    db: Session,
    *,
    case: EvaluationCase,
    revision_no: int = 1,
    report_no: str | None = None,
    generated_by: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Return (snapshot, sha256 digest)."""
    template_version: ReportTemplateVersion | None = case.template_version
    standard_version = case.standard_version
    equipment_rows = db.execute(
        select(TestEquipmentUsage).where(TestEquipmentUsage.case_id == case.id)
    ).scalars().all()
    attachments = db.execute(select(Attachment).where(Attachment.case_id == case.id)).scalars().all()

    snapshot: dict[str, Any] = {
        "meta": {
            "application_no": case.application_no,
            "report_no": report_no,
            "revision_no": revision_no,
            "case_revision_no": case.revision_no,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "generated_by": generated_by,
            "platform": f"{settings.APP_NAME} {settings.APP_SUBTITLE}",
        },
        "versions": {
            "ruleset_label": standard_version.version_label if standard_version else None,
            "ruleset_status": standard_version.status if standard_version else None,
            "standard": standard_version.standard.code if standard_version and standard_version.standard else None,
            "standard_edition": standard_version.edition if standard_version else None,
            "source_reference": standard_version.source_reference if standard_version else None,
            "template_label": template_version.version_label if template_version else None,
            "template_sections": (template_version.section_map or {}).get("sections", []) if template_version else [],
        },
        "cover": {
            "laboratory_name": settings.REPORT_LABORATORY_NAME,
            "laboratory_code": settings.REPORT_LABORATORY_CODE,
            "laboratory_location": case.laboratory.location if case.laboratory else None,
            "report_title": "Type Evaluation Test Report",
            "subtitle": "Non-automatic weighing instruments - OIML R 76",
            "case_title": case.title,
            "purpose": case.purpose,
        },
        "applicant": {
            "name": case.applicant.name if case.applicant else None,
            "contact_person": case.applicant.contact_person if case.applicant else None,
            "email": case.applicant.email if case.applicant else None,
            "phone": case.applicant.phone if case.applicant else None,
            "address": case.applicant.address if case.applicant else None,
            "city": case.applicant.city if case.applicant else None,
            "country": case.applicant.country if case.applicant else None,
        },
        "manufacturer": {
            "name": case.manufacturer.name if case.manufacturer else None,
            "contact_person": case.manufacturer.contact_person if case.manufacturer else None,
            "address": case.manufacturer.address if case.manufacturer else None,
            "city": case.manufacturer.city if case.manufacturer else None,
            "country": case.manufacturer.country if case.manufacturer else None,
        },
        "instrument": {
            "model": case.instrument.model,
            "type_designation": case.instrument.type_designation,
            "serial_number": case.instrument.serial_number,
            "instrument_class": case.instrument.instrument_class,
            "max_capacity": _decimal(case.instrument.max_capacity),
            "min_capacity": _decimal(case.instrument.min_capacity),
            "e": _decimal(case.instrument.verification_scale_interval),
            "d": _decimal(case.instrument.actual_scale_interval),
            "unit": case.instrument.unit,
            "is_electronic": case.instrument.is_electronic,
            "is_multi_range": case.instrument.is_multi_range,
            "is_multi_interval": case.instrument.is_multi_interval,
            "has_tare_device": case.instrument.has_tare_device,
            "temperature_range": case.instrument.temperature_range,
            "power_supply": case.instrument.power_supply,
            "ranges": [
                {
                    "range_no": item.range_no,
                    "min_capacity": _decimal(item.min_capacity),
                    "max_capacity": _decimal(item.max_capacity),
                    "e": _decimal(item.verification_scale_interval),
                    "d": _decimal(item.actual_scale_interval),
                    "unit": item.unit,
                }
                for item in case.instrument.ranges
            ],
        },
        "conditions": [
            {
                "label": item.label,
                "temperature_c": _decimal(item.temperature_c),
                "relative_humidity_pct": _decimal(item.relative_humidity_pct),
                "barometric_pressure_kpa": _decimal(item.barometric_pressure_kpa),
                "started_at": _fmt(item.started_at),
                "ended_at": _fmt(item.ended_at),
                "notes": item.notes,
            }
            for item in case.conditions
        ],
        "equipment": [
            {
                "code": item.equipment.code,
                "name": item.equipment.name,
                "type": item.equipment.equipment_type,
                "serial_no": item.equipment.serial_no,
                "accuracy_class": item.equipment.accuracy_class,
                "role": item.role,
            }
            for item in equipment_rows
        ],
        "summary": summarise_case(case),
        "tests": [_test_snapshot(item) for item in case.tests],
        "evidence": [
            {
                "filename": item.original_filename,
                "category": item.category,
                "caption": item.caption,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            }
            for item in attachments
        ],
        "review": {
            "engineer": case.engineer.full_name if case.engineer else None,
            "reviewer": case.reviewer.full_name if case.reviewer else None,
            "approver": case.approver.full_name if case.approver else None,
            "status": case.status,
            "submitted_at": _fmt(case.submitted_at),
            "verified_at": _fmt(case.verified_at),
            "approved_at": _fmt(case.approved_at),
            "finalized_at": _fmt(case.finalized_at),
        },
        "revisions": [
            {
                "revision_no": case.revision_no,
                "status": case.status,
                "updated_at": _fmt(case.updated_at),
            }
        ],
        "disclaimer": settings.REPORT_DISCLAIMER,
    }
    digest = content_hash(snapshot)
    snapshot["meta"]["content_hash"] = digest
    snapshot["meta"]["verification_code"] = verification_code(digest)
    return snapshot, digest
