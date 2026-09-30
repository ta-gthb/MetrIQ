"""Evaluation case schemas (FR-05, FR-06, FR-07)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel
from app.schemas.identity import UserOut
from app.schemas.masters import ApplicantOut, InstrumentOut, ManufacturerOut


class CaseCreateRequest(BaseModel):
    instrument_id: uuid.UUID | None = None
    instrument: dict | None = Field(
        default=None, description="Inline instrument payload when not reusing a master record"
    )
    applicant_id: uuid.UUID | None = None
    applicant: dict | None = None
    manufacturer_id: uuid.UUID | None = None
    manufacturer: dict | None = None
    laboratory_id: uuid.UUID | None = None
    engineer_id: uuid.UUID | None = None
    reviewer_id: uuid.UUID | None = None
    approver_id: uuid.UUID | None = None
    standard_version_id: uuid.UUID | None = None
    title: str | None = None
    purpose: str | None = None
    scope_notes: str | None = None
    priority: str = "normal"


class CaseUpdateRequest(BaseModel):
    title: str | None = None
    purpose: str | None = None
    scope_notes: str | None = None
    priority: str | None = None
    engineer_id: uuid.UUID | None = None
    reviewer_id: uuid.UUID | None = None
    approver_id: uuid.UUID | None = None
    standard_version_id: uuid.UUID | None = None
    template_version_id: uuid.UUID | None = None
    instrument_id: uuid.UUID | None = None
    applicant_id: uuid.UUID | None = None
    manufacturer_id: uuid.UUID | None = None


class CaseAssignmentRequest(BaseModel):
    engineer_id: uuid.UUID | None = None
    reviewer_id: uuid.UUID | None = None
    approver_id: uuid.UUID | None = None
    reason: str | None = None


class WorkflowActionRequest(BaseModel):
    reason: str | None = None
    target_test_instance_id: uuid.UUID | None = None


class EnvironmentalConditionIn(BaseModel):
    label: str = "Ambient"
    temperature_c: Decimal | None = None
    relative_humidity_pct: Decimal | None = None
    barometric_pressure_kpa: Decimal | None = None
    air_density_kg_m3: Decimal | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    notes: str | None = None
    test_instance_id: uuid.UUID | None = None


class EnvironmentalConditionOut(ORMModel):
    id: uuid.UUID
    label: str
    temperature_c: Decimal | None = None
    relative_humidity_pct: Decimal | None = None
    barometric_pressure_kpa: Decimal | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    notes: str | None = None


class CaseListOut(ORMModel):
    id: uuid.UUID
    application_no: str
    title: str | None = None
    status: str
    revision_no: int
    priority: str
    laboratory_id: uuid.UUID
    instrument_id: uuid.UUID
    standard_version_id: uuid.UUID | None = None
    engineer_id: uuid.UUID | None = None
    reviewer_id: uuid.UUID | None = None
    approver_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    finalized_at: datetime | None = None
    instrument_model: str | None = None
    applicant_name: str | None = None
    overall_result: str | None = None
    test_counts: dict | None = None


class CaseDetailOut(CaseListOut):
    # Populated by the finalize action so the caller immediately learns the
    # report number, hash and verification code. Without an explicit field the
    # response model would silently discard them.
    report: dict | None = None
    purpose: str | None = None
    scope_notes: str | None = None
    submitted_at: datetime | None = None
    verified_at: datetime | None = None
    approved_at: datetime | None = None
    last_correction_reason: str | None = None
    instrument: InstrumentOut | None = None
    applicant: ApplicantOut | None = None
    manufacturer: ManufacturerOut | None = None
    engineer: UserOut | None = None
    reviewer: UserOut | None = None
    approver: UserOut | None = None
    standard_version_label: str | None = None
    template_version_label: str | None = None
    conditions: list[EnvironmentalConditionOut] = Field(default_factory=list)
    tests: list[dict] = Field(default_factory=list)
    superseded_tests: list[dict] = Field(default_factory=list)
    test_plan: list[dict] = Field(default_factory=list)
