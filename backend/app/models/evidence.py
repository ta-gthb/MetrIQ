"""Evidence attachments and polymorphic links (FR-09, PRD 19.3)."""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import JSONType
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AttachmentCategory:
    NAMEPLATE_PHOTO = "nameplate_photograph"
    TEST_SETUP_PHOTO = "test_setup_photograph"
    CALIBRATION_CERTIFICATE = "calibration_certificate"
    TECHNICAL_MANUAL = "technical_manual"
    DRAWING = "drawing"
    CORRESPONDENCE = "correspondence"
    OTHER = "other"

    ALL = [NAMEPLATE_PHOTO, TEST_SETUP_PHOTO, CALIBRATION_CERTIFICATE, TECHNICAL_MANUAL,
           DRAWING, CORRESPONDENCE, OTHER]


class Attachment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "attachments"

    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="CASCADE"), index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    storage_backend: Mapped[str] = mapped_column(String(24), default="local")
    content_type: Mapped[str | None] = mapped_column(String(120))
    extension: Mapped[str | None] = mapped_column(String(16))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    caption: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), default=AttachmentCategory.OTHER)
    category_source: Mapped[str] = mapped_column(String(16), default="manual")
    classification_confidence: Mapped[str | None] = mapped_column(String(16))
    is_malware_scanned: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    links: Mapped[list["AttachmentLink"]] = relationship(
        back_populates="attachment", cascade="all, delete-orphan"
    )


class AttachmentLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "attachment_links"

    attachment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="CASCADE"), index=True
    )
    test_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_instances.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    linked_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    attachment: Mapped[Attachment] = relationship(back_populates="links")
    test_instance = relationship("TestInstance", lazy="joined")
