"""Standards, versioned rulesets and report templates (PRD 4, 10.1, 14.1).

A ``StandardVersion`` is the unit of rule activation: activating a version makes
its rule set available for new evaluation cases while historical cases keep the
version they were created with (PRD 6.1, 24.1).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import JSONType
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Standard(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "standards"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(160), default="OIML")
    category: Mapped[str] = mapped_column(String(60), default="oiml")
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    versions: Mapped[list["StandardVersion"]] = relationship(
        back_populates="standard", cascade="all, delete-orphan", order_by="StandardVersion.edition"
    )


class StandardVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "standard_versions"
    __table_args__ = (UniqueConstraint("standard_id", "version_label", name="uq_standard_version"),)

    standard_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standards.id", ondelete="CASCADE"), index=True
    )
    edition: Mapped[str] = mapped_column(String(40), nullable=False)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="inactive")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    source_reference: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    # Legacy lifecycle metadata remains for existing database records only.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    review_reference: Mapped[str | None] = mapped_column(String(120))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    approved_by_name: Mapped[str | None] = mapped_column(String(160))
    approval_note: Mapped[str | None] = mapped_column(Text)
    scheduled_for: Mapped[date | None] = mapped_column(Date)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    deactivation_reason: Mapped[str | None] = mapped_column(Text)
    approved_fingerprint: Mapped[str | None] = mapped_column(String(72))
    activation_basis: Mapped[str | None] = mapped_column(String(32))

    standard: Mapped[Standard] = relationship(back_populates="versions", lazy="joined")
    rule_versions: Mapped[list["RuleVersion"]] = relationship(
        back_populates="standard_version", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["RuleReview"]] = relationship(
        back_populates="standard_version", cascade="all, delete-orphan"
    )


class Rule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "rules"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="mpe", index=True)
    clause_reference: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RuleVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A versioned definition of a rule attached to one standard version."""

    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version_label", name="uq_rule_version"),)

    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"), index=True)
    standard_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_versions.id", ondelete="CASCADE"), index=True
    )
    version_label: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    definition: Mapped[dict] = mapped_column(JSONType, default=dict)
    formula: Mapped[str | None] = mapped_column(Text)
    threshold: Mapped[str | None] = mapped_column(String(120))
    unit: Mapped[str | None] = mapped_column(String(24))
    applicability: Mapped[dict | None] = mapped_column(JSONType)
    # Free-text policy description. The shipped ruleset uses up to 74
    # characters, which PostgreSQL rejects outright at String(60).
    rounding_policy: Mapped[str | None] = mapped_column(String(200))
    review_status: Mapped[str] = mapped_column(String(40), default="not_required")
    reviewed_by: Mapped[str | None] = mapped_column(String(160))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active_from: Mapped[date | None] = mapped_column(Date)
    active_to: Mapped[date | None] = mapped_column(Date)

    rule: Mapped[Rule] = relationship(lazy="joined")
    standard_version: Mapped[StandardVersion] = relationship(back_populates="rule_versions")


class RuleReview(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Historical review record retained for databases created by earlier releases."""

    __tablename__ = "rule_reviews"

    standard_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_versions.id", ondelete="CASCADE"), index=True
    )
    rule_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rule_versions.id", ondelete="CASCADE"), index=True
    )
    # "rule" or "ruleset": a reviewer may sign off a single band or the whole set.
    scope: Mapped[str] = mapped_column(String(16), default="rule", index=True)
    # approved | rejected | needs_changes
    decision: Mapped[str] = mapped_column(String(24), index=True)
    reviewer_name: Mapped[str] = mapped_column(String(160), nullable=False)
    reviewer_credentials: Mapped[str | None] = mapped_column(String(255))
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_revision: Mapped[str | None] = mapped_column(String(255))
    clause_reference: Mapped[str | None] = mapped_column(String(80))
    boundary_cases_passed: Mapped[bool | None] = mapped_column(Boolean)
    boundary_case_reference: Mapped[str | None] = mapped_column(String(255))
    change_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Fingerprint of the reviewed content, so a later edit is detectable.
    fingerprint: Mapped[str | None] = mapped_column(String(72))

    standard_version: Mapped[StandardVersion] = relationship(back_populates="reviews")


class ReportTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "report_templates"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    standard_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("standard_versions.id", ondelete="SET NULL")
    )
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    versions: Mapped[list["ReportTemplateVersion"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="ReportTemplateVersion.version_no"
    )


class ReportTemplateVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "report_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version_label", name="uq_template_version"),)

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("report_templates.id", ondelete="CASCADE"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    section_map: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    template: Mapped[ReportTemplate] = relationship(back_populates="versions", lazy="joined")
