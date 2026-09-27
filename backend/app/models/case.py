"""Evaluation cases, assignments, environment and equipment usage (FR-05, FR-06)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import JSONType
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CaseStatus:
    DRAFT = "DRAFT"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    TESTING_COMPLETED = "TESTING_COMPLETED"
    UNDER_REVIEW = "UNDER_REVIEW"
    CORRECTION_REQUIRED = "CORRECTION_REQUIRED"
    VERIFIED = "VERIFIED"
    UNDER_APPROVAL = "UNDER_APPROVAL"
    APPROVED = "APPROVED"
    FINALIZED = "FINALIZED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"

    ALL = [DRAFT, ASSIGNED, IN_PROGRESS, TESTING_COMPLETED, UNDER_REVIEW, CORRECTION_REQUIRED,
           VERIFIED, UNDER_APPROVAL, APPROVED, FINALIZED, REJECTED, CANCELLED]
    EDITABLE = {DRAFT, ASSIGNED, IN_PROGRESS, CORRECTION_REQUIRED}
    IMMUTABLE = {FINALIZED, CANCELLED}


class EvaluationCase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "evaluation_cases"

    application_no: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("instruments.id", ondelete="RESTRICT"), index=True
    )
    applicant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("applicants.id", ondelete="SET NULL"), index=True
    )
    manufacturer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("manufacturers.id", ondelete="SET NULL"), index=True
    )
    laboratory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("laboratories.id", ondelete="RESTRICT"), index=True
    )
    engineer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    approver_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    standard_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("standard_versions.id", ondelete="SET NULL"), index=True
    )
    template_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("report_template_versions.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), default=CaseStatus.DRAFT, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    application_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_completion_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purpose: Mapped[str | None] = mapped_column(Text)
    scope_notes: Mapped[str | None] = mapped_column(Text)
    test_plan_snapshot: Mapped[dict | None] = mapped_column(JSONType)
    test_plan_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_correction_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    instrument = relationship("Instrument", lazy="joined")
    applicant = relationship("Applicant", lazy="joined")
    manufacturer = relationship("Manufacturer", lazy="joined")
    laboratory = relationship("Laboratory", lazy="joined")
    standard_version = relationship("StandardVersion", lazy="joined")
    template_version = relationship("ReportTemplateVersion", lazy="joined")
    engineer = relationship("User", foreign_keys=[engineer_id], lazy="joined")
    reviewer = relationship("User", foreign_keys=[reviewer_id], lazy="joined")
    approver = relationship("User", foreign_keys=[approver_id], lazy="joined")
    tests: Mapped[list["TestInstance"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="TestInstance.sequence_no"
    )
    conditions: Mapped[list["EnvironmentalCondition"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class CaseAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "case_assignments"
    __table_args__ = (UniqueConstraint("case_id", "user_id", "role_code", name="uq_case_assignment"),)

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role_code: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user = relationship("User", lazy="joined")


class EnvironmentalCondition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "environmental_conditions"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="CASCADE"), index=True
    )
    test_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_instances.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(120), default="Ambient")
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    relative_humidity_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    barometric_pressure_kpa: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    air_density_kg_m3: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    case: Mapped[EvaluationCase] = relationship(back_populates="conditions")


class TestEquipmentUsage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "test_equipment_usage"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="CASCADE"), index=True
    )
    test_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_instances.id", ondelete="CASCADE"), index=True
    )
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_equipment.id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)

    equipment = relationship("TestEquipment", lazy="joined")
