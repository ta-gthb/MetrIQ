"""Manufacturer, applicant, instrument and equipment schemas (FR-03, FR-04)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel

VALID_CLASSES = {"I", "II", "III", "IIII", "1", "2", "3", "4"}


class ManufacturerBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    code: str | None = None
    contact_person: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    country: str | None = "India"
    is_active: bool = True


class ManufacturerCreate(ManufacturerBase):
    pass


class ManufacturerOut(ORMModel, ManufacturerBase):
    id: uuid.UUID


class ApplicantBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    code: str | None = None
    organisation_type: str | None = None
    contact_person: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    country: str | None = "India"
    is_active: bool = True


class ApplicantCreate(ApplicantBase):
    pass


class ApplicantOut(ORMModel, ApplicantBase):
    id: uuid.UUID


class InstrumentRangeIn(BaseModel):
    range_no: int = 1
    min_capacity: Decimal
    max_capacity: Decimal
    verification_scale_interval: Decimal = Field(gt=0)
    actual_scale_interval: Decimal | None = None
    unit: str = "g"

    @field_validator("unit")
    @classmethod
    def _mass_unit(cls, value: str) -> str:
        from app.utils.units import is_mass_unit, normalise_unit

        if not is_mass_unit(value):
            raise ValueError(f"unsupported mass unit '{value}'")
        return normalise_unit(value)


class InstrumentRangeOut(ORMModel):
    id: uuid.UUID
    range_no: int
    min_capacity: Decimal
    max_capacity: Decimal
    verification_scale_interval: Decimal
    actual_scale_interval: Decimal | None = None
    unit: str


class InstrumentBase(BaseModel):
    manufacturer_id: uuid.UUID | None = None
    model: str = Field(min_length=1, max_length=160)
    type_designation: str | None = None
    serial_number: str | None = None
    instrument_class: str
    max_capacity: Decimal = Field(gt=0)
    min_capacity: Decimal | None = None
    verification_scale_interval: Decimal = Field(gt=0)
    actual_scale_interval: Decimal | None = None
    unit: str = "g"
    is_electronic: bool = True
    is_multi_range: bool = False
    is_multi_interval: bool = False
    has_tare_device: bool = False
    has_zero_device: bool = True
    has_level_indicator: bool = True
    temperature_range: str | None = None
    power_supply: str | None = None
    configuration: dict | None = None
    remarks: str | None = None

    @field_validator("instrument_class")
    @classmethod
    def _known_class(cls, value: str) -> str:
        normalised = value.strip().upper()
        if normalised not in VALID_CLASSES:
            raise ValueError("instrument class must be one of I, II, III or IIII")
        return {"1": "I", "2": "II", "3": "III", "4": "IIII"}.get(normalised, normalised)

    @field_validator("unit")
    @classmethod
    def _mass_unit(cls, value: str) -> str:
        from app.utils.units import is_mass_unit, normalise_unit

        if not is_mass_unit(value):
            raise ValueError(f"unsupported mass unit '{value}'")
        return normalise_unit(value)


class InstrumentCreate(InstrumentBase):
    ranges: list[InstrumentRangeIn] = Field(default_factory=list)


class InstrumentOut(ORMModel, InstrumentBase):
    id: uuid.UUID
    ranges: list[InstrumentRangeOut] = Field(default_factory=list)
    manufacturer: "ManufacturerOut | None" = None


#: The equipment types the register offers. Kept in step with the frontend
#: dropdown in frontend/js/admin.js.
EQUIPMENT_TYPES: tuple[str, ...] = (
    "weights",
    "mass_comparator",
    "balance",
    "thermometer",
    "hygrometer",
    "pressure_gauge",
    "voltmeter",
    "timer",
    "other",
)


class TestEquipmentBase(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=2, max_length=255)
    equipment_type: str = Field(min_length=1, max_length=80)
    manufacturer: str = Field(min_length=2, max_length=160)
    model: str = Field(min_length=1, max_length=160)
    serial_no: str = Field(min_length=1, max_length=120)
    nominal_value: str | None = None
    unit: str = Field(min_length=1, max_length=16)
    accuracy_class: str = Field(min_length=1, max_length=40)
    laboratory_id: uuid.UUID | None = None
    is_active: bool = True

    @field_validator("equipment_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        text = value.strip().lower().replace(" ", "_")
        if text not in EQUIPMENT_TYPES:
            raise ValueError(
                "equipment type must be one of: " + ", ".join(EQUIPMENT_TYPES)
            )
        return text


class TestEquipmentCreate(TestEquipmentBase):
    pass


class EquipmentUsageCreate(BaseModel):
    """Equipment recorded against a case, optionally against one test."""

    equipment_id: uuid.UUID
    test_instance_id: uuid.UUID | None = None
    role: str | None = Field(default=None, max_length=120)
    notes: str | None = None


class EquipmentCalibrationCreate(BaseModel):
    certificate_no: str | None = None
    issued_by: str | None = None
    issue_date: date | None = None
    valid_until: date | None = None
    uncertainty: str | None = None


class EquipmentCalibrationOut(ORMModel, EquipmentCalibrationCreate):
    id: uuid.UUID
    equipment_id: uuid.UUID


class TestEquipmentOut(ORMModel):
    """The register row as it is read, including rows recorded before the
    mandatory-fields policy existed.

    The create schema above stays strict; this output schema must not apply its
    validators, because a historical row may carry no model, serial number or
    accuracy class (and a type outside the current dropdown), and reading it
    must never fail the whole register.
    """

    id: uuid.UUID
    code: str
    name: str
    equipment_type: str
    manufacturer: str | None = None
    model: str | None = None
    serial_no: str | None = None
    nominal_value: str | None = None
    unit: str | None = None
    accuracy_class: str | None = None
    laboratory_id: uuid.UUID | None = None
    is_active: bool = True
    calibrations: list[EquipmentCalibrationOut] = Field(default_factory=list)


InstrumentOut.model_rebuild()
