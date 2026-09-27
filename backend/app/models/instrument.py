"""Instrument master and multi-range support (FR-04)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import JSONType
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Instrument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "instruments"

    manufacturer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("manufacturers.id", ondelete="SET NULL"), index=True
    )
    model: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    type_designation: Mapped[str | None] = mapped_column(String(160))
    serial_number: Mapped[str | None] = mapped_column(String(120), index=True)
    instrument_class: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    max_capacity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    min_capacity: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    verification_scale_interval: Mapped[Decimal] = mapped_column("e", Numeric(24, 8), nullable=False)
    actual_scale_interval: Mapped[Decimal | None] = mapped_column("d", Numeric(24, 8))
    unit: Mapped[str] = mapped_column(String(16), default="g", nullable=False)
    is_electronic: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_multi_range: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_multi_interval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_tare_device: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_zero_device: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    has_level_indicator: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    temperature_range: Mapped[str | None] = mapped_column(String(80))
    power_supply: Mapped[str | None] = mapped_column(String(120))
    configuration: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    manufacturer = relationship("Manufacturer", lazy="joined")
    ranges: Mapped[list["InstrumentRange"]] = relationship(
        back_populates="instrument", cascade="all, delete-orphan", order_by="InstrumentRange.range_no"
    )


class InstrumentRange(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "instrument_ranges"

    instrument_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    range_no: Mapped[int] = mapped_column(default=1)
    min_capacity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    max_capacity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    verification_scale_interval: Mapped[Decimal] = mapped_column("e", Numeric(24, 8), nullable=False)
    actual_scale_interval: Mapped[Decimal | None] = mapped_column("d", Numeric(24, 8))
    unit: Mapped[str] = mapped_column(String(16), default="g")

    instrument: Mapped[Instrument] = relationship(back_populates="ranges")
