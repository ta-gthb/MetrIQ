"""Generated reports, immutable revisions and repository indexing (PRD 17)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import JSONType
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class GeneratedReport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "generated_reports"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="CASCADE"), index=True
    )
    report_no: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, default=1)
    case_revision_no: Mapped[int] = mapped_column(Integer, default=1)
    standard_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("standard_versions.id", ondelete="SET NULL")
    )
    template_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("report_template_versions.id", ondelete="SET NULL")
    )
    ruleset_label: Mapped[str | None] = mapped_column(String(80))
    template_label: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True)
    is_immutable: Mapped[bool] = mapped_column(Boolean, default=False)
    data_snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    verification_code: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    notes: Mapped[str | None] = mapped_column(Text)
    # The state of the *optional* signature layer. Approval is a separate
    # concept and is recorded on the case (approver_id / approved_at); this
    # column never implies a signature exists, and nothing sets it today.
    signature_status: Mapped[str] = mapped_column(String(24), default="NOT_SIGNED")

    case = relationship("EvaluationCase", lazy="joined")
    revisions: Mapped[list["ReportRevision"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", order_by="ReportRevision.revision_no"
    )


class ReportRevision(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "report_revisions"

    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generated_reports.id", ondelete="CASCADE"), index=True
    )
    revision_no: Mapped[int] = mapped_column(Integer, default=1)
    format: Mapped[str] = mapped_column(String(12), default="pdf")
    storage_key: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    case_revision_no: Mapped[int | None] = mapped_column(Integer)
    change_summary: Mapped[str | None] = mapped_column(Text)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    report: Mapped[GeneratedReport] = relationship(back_populates="revisions")


class ReportSignature(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An optional cryptographic signature over a report revision.

    Nothing writes this table. MetrIQ's assurance is that a finalized report's
    content is frozen and can be re-hashed to the recorded SHA-256 and
    verification code, and the UI says exactly that instead of implying a
    signature that does not exist. When a signature provider is chosen, the
    columns a real implementation needs - algorithm, certificate reference,
    signer identity, timestamp and document hash - are already here, so it is
    an INSERT rather than a schema change.
    """

    __tablename__ = "report_signatures"

    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generated_reports.id", ondelete="CASCADE"), index=True
    )
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="NOT_SIGNED")
    algorithm: Mapped[str | None] = mapped_column(String(60))
    signer_identity: Mapped[str | None] = mapped_column(String(255))
    signer_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    certificate_reference: Mapped[str | None] = mapped_column(String(512))
    document_hash: Mapped[str | None] = mapped_column(String(64))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)

    report: Mapped["GeneratedReport"] = relationship()
