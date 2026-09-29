"""Test definitions, instances, observations, calculation runs and compliance."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import JSONType
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TestResultStatus:
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"
    WAIVED = "WAIVED"
    PENDING = "PENDING"

    ALL = [PASS, FAIL, NOT_APPLICABLE, INCOMPLETE, INVALID, WAIVED, PENDING]


class TestInstanceStatus:
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"

    ALL = [NOT_STARTED, IN_PROGRESS, COMPLETED]


class TestImplementationStatus:
    """Whether the deterministic engine can execute a catalogue entry.

    Audit item 7: every prescribed test has a catalogue entry, and each entry
    says plainly whether it is executable. ``not_implemented`` entries are
    surfaced by the test plan instead of being hidden.
    """

    IMPLEMENTED = "implemented"
    NOT_IMPLEMENTED = "not_implemented"

    ALL = [IMPLEMENTED, NOT_IMPLEMENTED]


class ApplicabilityStatus:
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    MANUAL_REVIEW = "MANUAL_REVIEW"

    ALL = [APPLICABLE, NOT_APPLICABLE, MANUAL_REVIEW]


class TestDefinition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Versioned test metadata (PRD 10.1)."""

    __tablename__ = "test_definitions"

    test_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    standard_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_versions.id", ondelete="CASCADE"), index=True
    )
    clause_reference: Mapped[str | None] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(40), default="metrological")
    phase: Mapped[str] = mapped_column(String(16), default="MVP")
    implementation_status: Mapped[str] = mapped_column(
        String(24),
        default=TestImplementationStatus.IMPLEMENTED,
        server_default=TestImplementationStatus.IMPLEMENTED,
        nullable=False,
    )
    unsupported_reason: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    applicability_expression: Mapped[dict] = mapped_column(JSONType, default=dict)
    input_schema: Mapped[dict] = mapped_column(JSONType, default=dict)
    validation_rules: Mapped[dict] = mapped_column(JSONType, default=dict)
    calculation_rules: Mapped[dict] = mapped_column(JSONType, default=dict)
    compliance_rules: Mapped[dict] = mapped_column(JSONType, default=dict)
    report_section_mapping: Mapped[dict] = mapped_column(JSONType, default=dict)
    evidence_requirements: Mapped[dict] = mapped_column(JSONType, default=dict)
    sequence_no: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active_from: Mapped[date | None] = mapped_column(Date)
    active_to: Mapped[date | None] = mapped_column(Date)

    standard_version = relationship("StandardVersion", lazy="joined")


class TestInstance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "test_instances"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="CASCADE"), index=True
    )
    test_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_definitions.id", ondelete="RESTRICT"), index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, default=100)
    applicability_status: Mapped[str] = mapped_column(
        String(24), default=ApplicabilityStatus.APPLICABLE
    )
    applicability_reason: Mapped[str | None] = mapped_column(Text)
    applicability_trace: Mapped[dict | None] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(24), default=TestInstanceStatus.NOT_STARTED, index=True)
    result_status: Mapped[str] = mapped_column(String(24), default=TestResultStatus.PENDING, index=True)
    remarks: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    is_waived: Mapped[bool] = mapped_column(Boolean, default=False)
    waiver_reason: Mapped[str | None] = mapped_column(Text)

    case = relationship("EvaluationCase", back_populates="tests")
    definition = relationship("TestDefinition", lazy="joined")
    observations: Mapped[list["TestObservation"]] = relationship(
        back_populates="test_instance",
        cascade="all, delete-orphan",
        order_by="TestObservation.observation_no",
    )
    calculation_runs: Mapped[list["CalculationRun"]] = relationship(
        back_populates="test_instance", cascade="all, delete-orphan"
    )
    compliance_result: Mapped["ComplianceResult | None"] = relationship(
        back_populates="test_instance", cascade="all, delete-orphan", uselist=False
    )


class TestObservation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "test_observations"

    test_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_instances.id", ondelete="CASCADE"), index=True
    )
    observation_no: Mapped[int] = mapped_column(Integer, default=1)
    position_label: Mapped[str | None] = mapped_column(String(60))
    load: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    indication: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    additional_load: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    elapsed_seconds: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    unit: Mapped[str | None] = mapped_column(String(16))
    value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    input_payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    anomaly_flag: Mapped[str | None] = mapped_column(String(24))
    anomaly_note: Mapped[str | None] = mapped_column(Text)
    anomaly_disposition: Mapped[str | None] = mapped_column(String(24))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    test_instance: Mapped[TestInstance] = relationship(back_populates="observations")


class CalculationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Deterministic computation snapshot (PRD 11.3, 11.5)."""

    __tablename__ = "calculation_runs"

    test_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_instances.id", ondelete="CASCADE"), index=True
    )
    test_code: Mapped[str] = mapped_column(String(64), index=True)
    rule_version_label: Mapped[str | None] = mapped_column(String(80))
    standard_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("standard_versions.id", ondelete="SET NULL")
    )
    engine_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    input_snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)
    intermediates: Mapped[dict] = mapped_column(JSONType, default=dict)
    outputs: Mapped[dict] = mapped_column(JSONType, default=dict)
    units: Mapped[dict | None] = mapped_column(JSONType)
    rounding_policy: Mapped[str | None] = mapped_column(String(80))
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    errors: Mapped[list | None] = mapped_column(JSONType)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calculated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    test_instance: Mapped[TestInstance] = relationship(back_populates="calculation_runs")


class ComplianceResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "compliance_results"

    test_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_instances.id", ondelete="CASCADE"), unique=True, index=True
    )
    calculation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calculation_runs.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(24), default=TestResultStatus.PENDING, index=True)
    measured_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    limit_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    margin: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    unit: Mapped[str | None] = mapped_column(String(16))
    rule_id: Mapped[str | None] = mapped_column(String(80))
    rule_version: Mapped[str | None] = mapped_column(String(80))
    clause_reference: Mapped[str | None] = mapped_column(String(80))
    explanation: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONType)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    test_instance: Mapped[TestInstance] = relationship(back_populates="compliance_result")


class ManualOverride(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Exceptional override record (PRD 11.6). The automated result is preserved."""

    __tablename__ = "manual_overrides"

    test_instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_instances.id", ondelete="CASCADE"), index=True
    )
    previous_status: Mapped[str] = mapped_column(String(24))
    new_status: Mapped[str] = mapped_column(String(24))
    previous_value: Mapped[str | None] = mapped_column(String(120))
    new_value: Mapped[str | None] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
