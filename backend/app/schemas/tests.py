"""Test instance, observation, calculation and compliance schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel


class ObservationIn(BaseModel):
    # Test definitions declare their own columns (for example a checklist row
    # carries item_code/conforms). Unknown keys are accepted here and preserved
    # by the router in `input_payload` so a rule can use them without a schema
    # migration for every new test type (PRD 10.2).
    model_config = ConfigDict(extra="allow")

    observation_no: int | None = None
    position_label: str | None = None
    load: Decimal | None = None
    indication: Decimal | None = None
    additional_load: Decimal | None = None
    elapsed_seconds: Decimal | None = None
    temperature_c: Decimal | None = None
    value: Decimal | None = None
    unit: str | None = None
    input_payload: dict | None = None


class ObservationBatchIn(BaseModel):
    observations: list[ObservationIn]
    replace: bool = True
    remarks: str | None = None


class ObservationOut(ORMModel):
    id: uuid.UUID
    observation_no: int
    position_label: str | None = None
    load: Decimal | None = None
    indication: Decimal | None = None
    additional_load: Decimal | None = None
    elapsed_seconds: Decimal | None = None
    temperature_c: Decimal | None = None
    value: Decimal | None = None
    unit: str | None = None
    input_payload: dict = Field(default_factory=dict)
    anomaly_flag: str | None = None
    anomaly_note: str | None = None
    anomaly_disposition: str | None = None


class ComplianceResultOut(ORMModel):
    id: uuid.UUID
    status: str
    measured_value: Decimal | None = None
    limit_value: Decimal | None = None
    margin: Decimal | None = None
    unit: str | None = None
    rule_id: str | None = None
    rule_version: str | None = None
    clause_reference: str | None = None
    explanation: str | None = None
    details: dict | None = None
    evaluated_at: datetime | None = None


class CalculationRunOut(ORMModel):
    id: uuid.UUID
    test_code: str
    engine_version: str
    rule_version_label: str | None = None
    rounding_policy: str | None = None
    is_valid: bool
    errors: list | None = None
    outputs: dict | None = None
    intermediates: dict | None = None
    input_snapshot: dict | None = None
    calculated_at: datetime | None = None


class TestDefinitionOut(ORMModel):
    id: uuid.UUID
    test_code: str
    name: str
    clause_reference: str | None = None
    category: str
    phase: str
    description: str | None = None
    sequence_no: int
    is_active: bool
    implementation_status: str = "implemented"
    unsupported_reason: str | None = None
    input_schema: dict = Field(default_factory=dict)
    validation_rules: dict = Field(default_factory=dict)
    calculation_rules: dict = Field(default_factory=dict)
    compliance_rules: dict = Field(default_factory=dict)
    applicability_expression: dict = Field(default_factory=dict)
    report_section_mapping: dict = Field(default_factory=dict)
    evidence_requirements: dict = Field(default_factory=dict)


class TestInstanceOut(ORMModel):
    id: uuid.UUID
    case_id: uuid.UUID
    test_definition_id: uuid.UUID
    sequence_no: int
    revision_no: int = 1
    supersedes_test_instance_id: uuid.UUID | None = None
    superseded_by_test_instance_id: uuid.UUID | None = None
    retest_reason: str | None = None
    superseded_at: datetime | None = None
    applicability_status: str
    applicability_reason: str | None = None
    applicability_trace: dict | None = None
    status: str
    result_status: str
    remarks: str | None = None
    is_waived: bool
    waiver_reason: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    definition: TestDefinitionOut | None = None
    observations: list[ObservationOut] = Field(default_factory=list)
    compliance_result: ComplianceResultOut | None = None
    latest_calculation: CalculationRunOut | None = None


class TestPlanItemOut(BaseModel):
    test_code: str
    name: str
    clause_reference: str | None = None
    category: str
    phase: str
    sequence_no: int
    applicable: bool
    reason: str
    trace: list[dict] = Field(default_factory=list)
    definition_id: str | None = None
    manual_override: bool = False
    implementation_status: str = "implemented"
    unsupported_reason: str | None = None
    supported: bool = True


class TestPlanOut(BaseModel):
    case_id: uuid.UUID
    generated_at: datetime | None = None
    ruleset_label: str | None = None
    items: list[TestPlanItemOut] = Field(default_factory=list)
    preserved: bool = False


class TestInstanceUpdate(BaseModel):
    remarks: str | None = None
    applicability_status: str | None = None
    applicability_reason: str | None = None
    is_waived: bool | None = None
    waiver_reason: str | None = None
    mark_complete: bool | None = None


class CalculationOut(BaseModel):
    test_instance_id: uuid.UUID
    test_code: str
    engine_version: str
    status: str
    is_valid: bool
    measured_value: Decimal | None = None
    limit_value: Decimal | None = None
    margin: Decimal | None = None
    unit: str | None = None
    comparator: str
    rule_id: str | None = None
    rule_version: str | None = None
    clause_reference: str | None = None
    explanation: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    intermediates: dict = Field(default_factory=dict)
    rows: list[dict] = Field(default_factory=list)
    rounding_policy: str | None = None
    calculated_at: datetime | None = None


class RetestRequest(BaseModel):
    """A re-test has to say why: the reason is part of the permanent record."""

    reason: str = Field(min_length=5, max_length=2000)


class ManualOverrideRequest(BaseModel):
    status: str
    reason: str = Field(min_length=10)
    new_value: str | None = None
