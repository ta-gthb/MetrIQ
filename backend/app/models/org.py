"""Manufacturer, applicant and test-equipment masters (FR-03, FR-09, section 13)."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PartyMixin:
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    contact_person: Mapped[str | None] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(120), default="India")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Manufacturer(Base, UUIDPrimaryKeyMixin, TimestampMixin, PartyMixin):
    __tablename__ = "manufacturers"

    code: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)


class Applicant(Base, UUIDPrimaryKeyMixin, TimestampMixin, PartyMixin):
    __tablename__ = "applicants"

    code: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    organisation_type: Mapped[str | None] = mapped_column(String(80))


class TestEquipment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Reference masses, thermometers, hygrometers, pressure gauges, etc."""

    __tablename__ = "test_equipment"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    equipment_type: Mapped[str] = mapped_column(String(80), default="weights")
    manufacturer: Mapped[str | None] = mapped_column(String(160))
    model: Mapped[str | None] = mapped_column(String(160))
    serial_no: Mapped[str | None] = mapped_column(String(120))
    nominal_value: Mapped[str | None] = mapped_column(String(80))
    unit: Mapped[str | None] = mapped_column(String(16))
    accuracy_class: Mapped[str | None] = mapped_column(String(40))
    laboratory_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("laboratories.id", ondelete="SET NULL"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    calibrations: Mapped[list["EquipmentCalibration"]] = relationship(
        back_populates="equipment", cascade="all, delete-orphan"
    )


class EquipmentCalibration(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "equipment_calibrations"

    equipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_equipment.id", ondelete="CASCADE"), index=True
    )
    certificate_no: Mapped[str | None] = mapped_column(String(120))
    issued_by: Mapped[str | None] = mapped_column(String(255))
    issue_date: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date, index=True)
    uncertainty: Mapped[str | None] = mapped_column(String(80))
    certificate_attachment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    equipment: Mapped[TestEquipment] = relationship(back_populates="calibrations")
