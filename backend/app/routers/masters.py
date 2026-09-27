"""Manufacturer, applicant, instrument and test-equipment masters (FR-03, FR-04)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_active_user
from app.dependencies.permissions import require_any_permission, require_permission
from app.models import (
    Applicant,
    EquipmentCalibration,
    Instrument,
    InstrumentRange,
    Manufacturer,
    TestEquipment,
    User,
)
from app.routers._helpers import paginate
from app.schemas.common import Paginated
from app.schemas.masters import (
    ApplicantCreate,
    ApplicantOut,
    EquipmentCalibrationCreate,
    EquipmentCalibrationOut,
    InstrumentCreate,
    InstrumentOut,
    ManufacturerCreate,
    ManufacturerOut,
    TestEquipmentCreate,
    TestEquipmentOut,
)
from app.security.permissions import P
from app.security.scope import laboratory_filter
from app.services import audit_service

router = APIRouter(tags=["Masters"])


# ---------------------------------------------------------------- manufacturers
@router.get("/manufacturers", response_model=Paginated[ManufacturerOut], summary="List manufacturers")
def list_manufacturers(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    search: str | None = None,
    page: int = 1,
    page_size: int = Query(25, le=200),
) -> Paginated[ManufacturerOut]:
    statement = select(Manufacturer).order_by(Manufacturer.name)
    if search:
        statement = statement.where(Manufacturer.name.ilike(f"%{search}%"))
    rows, meta = paginate(db, statement, page=page, page_size=page_size)
    return Paginated[ManufacturerOut](items=rows, meta=meta)


@router.post(
    "/manufacturers",
    response_model=ManufacturerOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a manufacturer",
)
def create_manufacturer(
    payload: ManufacturerCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.MASTERS_MANAGE)),
) -> Manufacturer:
    record = Manufacturer(**payload.model_dump())
    db.add(record)
    db.flush()
    audit_service.record(
        db, event_type="CREATE", entity_type="manufacturer", entity_id=record.id, actor=user,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(record)
    return record


@router.patch("/manufacturers/{manufacturer_id}", response_model=ManufacturerOut, summary="Update a manufacturer")
def update_manufacturer(
    manufacturer_id: uuid.UUID,
    payload: ManufacturerCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.MASTERS_MANAGE)),
) -> Manufacturer:
    record = db.get(Manufacturer, manufacturer_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Manufacturer not found")
    before = {key: getattr(record, key) for key in payload.model_dump()}
    for key, value in payload.model_dump().items():
        setattr(record, key, value)
    audit_service.record(
        db, event_type="EDIT", entity_type="manufacturer", entity_id=record.id, actor=user,
        before=before, after=payload.model_dump(),
    )
    db.commit()
    db.refresh(record)
    return record


# ------------------------------------------------------------------- applicants
@router.get("/applicants", response_model=Paginated[ApplicantOut], summary="List applicants")
def list_applicants(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    search: str | None = None,
    page: int = 1,
    page_size: int = Query(25, le=200),
) -> Paginated[ApplicantOut]:
    statement = select(Applicant).order_by(Applicant.name)
    if search:
        statement = statement.where(Applicant.name.ilike(f"%{search}%"))
    rows, meta = paginate(db, statement, page=page, page_size=page_size)
    return Paginated[ApplicantOut](items=rows, meta=meta)


@router.post(
    "/applicants",
    response_model=ApplicantOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an applicant",
)
def create_applicant(
    payload: ApplicantCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.MASTERS_MANAGE)),
) -> Applicant:
    record = Applicant(**payload.model_dump())
    db.add(record)
    db.flush()
    audit_service.record(
        db, event_type="CREATE", entity_type="applicant", entity_id=record.id, actor=user,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(record)
    return record


# ------------------------------------------------------------------ instruments
@router.get("/instruments", response_model=Paginated[InstrumentOut], summary="List instruments")
def list_instruments(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    search: str | None = None,
    instrument_class: str | None = None,
    page: int = 1,
    page_size: int = Query(25, le=200),
) -> Paginated[InstrumentOut]:
    statement = select(Instrument).order_by(Instrument.model)
    if search:
        statement = statement.where(
            Instrument.model.ilike(f"%{search}%") | Instrument.serial_number.ilike(f"%{search}%")
        )
    if instrument_class:
        statement = statement.where(Instrument.instrument_class == instrument_class.upper())
    rows, meta = paginate(db, statement, page=page, page_size=page_size)
    return Paginated[InstrumentOut](items=rows, meta=meta)


@router.post(
    "/instruments",
    response_model=InstrumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register an instrument model",
)
def create_instrument(
    payload: InstrumentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_any_permission(P.MASTERS_MANAGE, P.CASES_CREATE)),
) -> Instrument:
    data = payload.model_dump(exclude={"ranges"})
    record = Instrument(**data, created_by=user.id)
    for index, item in enumerate(payload.ranges, start=1):
        record.ranges.append(
            InstrumentRange(**{**item.model_dump(), "range_no": item.range_no or index})
        )
    db.add(record)
    db.flush()
    audit_service.record(
        db, event_type="CREATE", entity_type="instrument", entity_id=record.id, actor=user,
        after={"model": record.model, "class": record.instrument_class,
               "max": str(record.max_capacity), "e": str(record.verification_scale_interval)},
    )
    db.commit()
    db.refresh(record)
    return record


@router.get("/instruments/{instrument_id}", response_model=InstrumentOut, summary="Instrument detail")
def get_instrument(
    instrument_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Instrument:
    record = db.get(Instrument, instrument_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return record


# --------------------------------------------------------------------- equipment
@router.get("/equipment", response_model=Paginated[TestEquipmentOut], summary="List test equipment")
def list_equipment(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    search: str | None = None,
    page: int = 1,
    page_size: int = Query(25, le=200),
) -> Paginated[TestEquipmentOut]:
    statement = select(TestEquipment).order_by(TestEquipment.code)
    laboratory_id = laboratory_filter(user)
    if laboratory_id is not None:
        statement = statement.where(
            (TestEquipment.laboratory_id == laboratory_id) | (TestEquipment.laboratory_id.is_(None))
        )
    if search:
        statement = statement.where(
            TestEquipment.name.ilike(f"%{search}%") | TestEquipment.code.ilike(f"%{search}%")
        )
    rows, meta = paginate(db, statement, page=page, page_size=page_size)
    return Paginated[TestEquipmentOut](items=rows, meta=meta)


@router.post(
    "/equipment",
    response_model=TestEquipmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register test equipment",
)
def create_equipment(
    payload: TestEquipmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.EQUIPMENT_MANAGE)),
) -> TestEquipment:
    data = payload.model_dump()
    if data.get("laboratory_id") is None:
        data["laboratory_id"] = user.laboratory_id
    record = TestEquipment(**data)
    db.add(record)
    db.flush()
    audit_service.record(
        db, event_type="CREATE", entity_type="test_equipment", entity_id=record.id, actor=user,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(record)
    return record


@router.post(
    "/equipment/{equipment_id}/calibrations",
    response_model=EquipmentCalibrationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a calibration record",
)
def add_calibration(
    equipment_id: uuid.UUID,
    payload: EquipmentCalibrationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(P.EQUIPMENT_MANAGE)),
) -> EquipmentCalibration:
    equipment = db.get(TestEquipment, equipment_id)
    if equipment is None:
        raise HTTPException(status_code=404, detail="Test equipment not found")
    record = EquipmentCalibration(equipment_id=equipment_id, **payload.model_dump())
    db.add(record)
    db.flush()
    audit_service.record(
        db, event_type="CREATE", entity_type="equipment_calibration", entity_id=record.id, actor=user,
        after=payload.model_dump(),
    )
    db.commit()
    db.refresh(record)
    return record
