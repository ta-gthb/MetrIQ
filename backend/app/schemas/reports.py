"""Standards, rules, report template and generated report schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class StandardVersionOut(ORMModel):
    id: uuid.UUID
    edition: str
    version_label: str
    status: str
    is_active: bool
    effective_from: date | None = None
    effective_to: date | None = None
    source_reference: str | None = None
    notes: str | None = None


class StandardOut(ORMModel):
    id: uuid.UUID
    code: str
    title: str
    publisher: str | None = None
    category: str
    description: str | None = None
    is_active: bool
    versions: list[StandardVersionOut] = Field(default_factory=list)


class RuleVersionOut(ORMModel):
    id: uuid.UUID
    version_label: str
    definition: dict = Field(default_factory=dict)
    formula: str | None = None
    threshold: str | None = None
    unit: str | None = None
    review_status: str
    is_active: bool
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    active_from: date | None = None
    active_to: date | None = None


class RuleOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    category: str
    clause_reference: str | None = None
    description: str | None = None
    is_active: bool
    review_status: str | None = None
    version_label: str | None = None
    standard_version_id: uuid.UUID | None = None
    standard_label: str | None = None
    definition: dict | None = None


class RuleSetOut(BaseModel):
    standard_version_id: uuid.UUID
    version_label: str
    standard_code: str | None = None
    edition: str | None = None
    status: str
    is_active: bool
    review_status: str | None = None
    source_reference: str | None = None
    notes: str | None = None
    rule_count: int = 0
    rules: list[dict] = Field(default_factory=list)
    # Governed lifecycle (audit items 6 and 10).
    lifecycle_state: str | None = None
    activation_basis: str | None = None
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by_name: str | None = None
    approval_note: str | None = None
    scheduled_for: date | None = None
    activated_at: datetime | None = None
    deactivated_at: datetime | None = None
    deactivation_reason: str | None = None
    approved_fingerprint: str | None = None
    can_activate: bool = False
    review_gate: dict = Field(default_factory=dict)


class ReportTemplateVersionOut(ORMModel):
    id: uuid.UUID
    version_no: int
    version_label: str
    section_map: dict = Field(default_factory=dict)
    is_active: bool


class ReportTemplateOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    is_active: bool
    versions: list[ReportTemplateVersionOut] = Field(default_factory=list)


class ReportRevisionOut(ORMModel):
    id: uuid.UUID
    revision_no: int
    format: str
    size_bytes: int | None = None
    sha256: str | None = None
    case_revision_no: int | None = None
    change_summary: str | None = None
    created_at: datetime


class GeneratedReportOut(ORMModel):
    id: uuid.UUID
    case_id: uuid.UUID
    report_no: str
    revision_no: int
    case_revision_no: int
    status: str
    is_immutable: bool
    ruleset_label: str | None = None
    template_label: str | None = None
    content_hash: str | None = None
    verification_code: str | None = None
    generated_at: datetime | None = None
    locked_at: datetime | None = None
    application_no: str | None = None
    revision_count: int | None = None


class ReportGenerateRequest(BaseModel):
    formats: list[str] = Field(default_factory=lambda: ["pdf", "docx"])
    lock: bool = False


class ReportListItemOut(BaseModel):
    id: uuid.UUID
    report_no: str
    application_no: str | None = None
    case_id: uuid.UUID
    status: str
    revision_no: int
    is_immutable: bool
    ruleset_label: str | None = None
    template_label: str | None = None
    verification_code: str | None = None
    generated_at: datetime | None = None
    overall_result: str | None = None
    instrument_id: uuid.UUID | None = None
    instrument_model: str | None = None
    instrument_serial_number: str | None = None
