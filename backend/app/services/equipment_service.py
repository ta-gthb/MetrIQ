"""Test-equipment traceability and calibration gates (audit item 11).

A reference standard is only traceable while its calibration is valid. The
platform therefore records which equipment was used for which test, refuses to
attach a standard whose calibration has expired or whose record is missing, and
refuses to calculate a test that names a standard that is out of calibration.

Nothing here changes a compliance result: the equipment gate decides whether a
test may be calculated at all, never how it is judged.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    EvaluationCase,
    EquipmentCalibration,
    TestEquipment,
    TestEquipmentUsage,
    TestInstance,
    utcnow,
)
from app.services import audit_service

#: The calibration covers today.
VALID = "valid"
#: The calibration still covers today but expires within the warning window.
EXPIRING = "expiring"
#: The calibration expired before today.
EXPIRED = "expired"
#: No calibration record has been filed.
MISSING = "missing"
#: The equipment is withdrawn from service in the register.
INACTIVE = "inactive"

#: Statuses that stop the equipment being used for a test.
BLOCKING_STATUSES = {EXPIRED, MISSING, INACTIVE}

#: A calibration expiring within this many days is reported as expiring.
EXPIRING_WINDOW_DAYS = 30


def current_calibration(equipment: TestEquipment) -> EquipmentCalibration | None:
    """The calibration that governs today: the latest expiry wins."""
    rows = list(equipment.calibrations or [])
    if not rows:
        return None

    def order(row: EquipmentCalibration):
        return (row.valid_until or date.max, row.issue_date or date.min, str(row.id))

    return sorted(rows, key=order)[-1]


def calibration_state(equipment: TestEquipment, *, on: date | None = None) -> dict:
    """Whether this equipment may be used today, and the certificate behind it."""
    today = on or utcnow().date()
    record = current_calibration(equipment)
    state: dict = {
        "equipment_id": str(equipment.id),
        "code": equipment.code,
        "name": equipment.name,
        "equipment_type": equipment.equipment_type,
        "is_active": bool(equipment.is_active),
        "checked_on": today.isoformat(),
        "certificate_no": record.certificate_no if record else None,
        "issued_by": record.issued_by if record else None,
        "issue_date": record.issue_date.isoformat() if record and record.issue_date else None,
        "valid_until": record.valid_until.isoformat() if record and record.valid_until else None,
        "uncertainty": record.uncertainty if record else None,
        "expiry_recorded": bool(record and record.valid_until),
        "days_remaining": None,
        "blocking": False,
        "detail": None,
    }
    if not equipment.is_active:
        state["status"] = INACTIVE
        state["blocking"] = True
        state["detail"] = "The equipment is withdrawn from service in the register."
        return state
    if record is None:
        state["status"] = MISSING
        state["blocking"] = True
        state["detail"] = "No calibration record has been filed for this equipment."
        return state
    if record.valid_until is not None:
        remaining = (record.valid_until - today).days
        state["days_remaining"] = remaining
        if remaining < 0:
            state["status"] = EXPIRED
            state["blocking"] = True
            state["detail"] = (
                f"Calibration expired on {record.valid_until.isoformat()}"
                f" ({abs(remaining)} day(s) ago)."
            )
            return state
        if remaining <= EXPIRING_WINDOW_DAYS:
            state["status"] = EXPIRING
            state["detail"] = f"Calibration expires in {remaining} day(s)."
            return state
        state["status"] = VALID
        return state
    state["status"] = VALID
    state["detail"] = "The calibration record carries no expiry date."
    return state


def equipment_state_line(state: dict) -> str:
    return f"{state['code']} ({state['name']}): {state['detail'] or state['status']}"


def usage_item(db: Session, row: TestEquipmentUsage, *, on: date | None = None) -> dict:
    """One usage record, with the calibration state of the equipment it names."""
    state = calibration_state(row.equipment, on=on) if row.equipment else None
    test = db.get(TestInstance, row.test_instance_id) if row.test_instance_id else None
    return {
        "id": str(row.id),
        "equipment_id": str(row.equipment_id),
        "role": row.role,
        "notes": row.notes,
        "test_instance_id": str(row.test_instance_id) if row.test_instance_id else None,
        "test_code": test.definition.test_code if test is not None and test.definition else None,
        "revision_no": test.revision_no if test is not None else None,
        "superseded_at": test.superseded_at if test is not None else None,
        "calibration": state,
    }


def case_equipment(db: Session, case: EvaluationCase, *, on: date | None = None) -> dict:
    """Every piece of equipment recorded against the case, with its state."""
    rows = (
        db.execute(
            select(TestEquipmentUsage)
            .where(TestEquipmentUsage.case_id == case.id)
            .order_by(TestEquipmentUsage.created_at, TestEquipmentUsage.id)
        )
        .scalars()
        .all()
    )
    items: list[dict] = []
    blocking: list[dict] = []
    warnings: list[dict] = []
    for row in rows:
        item = usage_item(db, row, on=on)
        items.append(item)
        state = item["calibration"]
        if state is None:
            continue
        if state["blocking"]:
            blocking.append(
                {
                    "code": state["code"],
                    "kind": "equipment",
                    "message": (
                        f"Test equipment {state['code']} cannot be relied on: "
                        f"{state['detail']} Replace the calibration or record different equipment."
                    ),
                }
            )
        elif state["status"] == EXPIRING:
            warnings.append(
                {
                    "code": state["code"],
                    "kind": "equipment",
                    "message": f"Test equipment {state['code']}: {state['detail']}",
                }
            )
    return {
        "recorded": len(rows),
        "items": items,
        "blocking": blocking,
        "warnings": warnings,
    }


def blocking_equipment_for_test(db: Session, test_instance: TestInstance) -> list[dict]:
    """Equipment attached to this test that may not be used today."""
    rows = (
        db.execute(
            select(TestEquipmentUsage).where(
                TestEquipmentUsage.test_instance_id == test_instance.id
            )
        )
        .scalars()
        .all()
    )
    out: list[dict] = []
    for row in rows:
        if row.equipment is None:
            continue
        state = calibration_state(row.equipment)
        if state["blocking"]:
            out.append(state)
    return out


def attach_usage(
    db: Session,
    *,
    case: EvaluationCase,
    equipment: TestEquipment,
    actor=None,
    test_instance: TestInstance | None = None,
    role: str | None = None,
    notes: str | None = None,
) -> TestEquipmentUsage:
    """Record that this equipment was used for this case (or this test)."""
    if test_instance is not None:
        if test_instance.case_id != case.id:
            raise ValueError("That test belongs to a different evaluation case.")
        if test_instance.superseded_at is not None:
            raise ValueError(
                "That test record has been superseded; attach the equipment to its live revision."
            )
    state = calibration_state(equipment)
    if state["blocking"]:
        raise ValueError(f"{equipment.code} cannot be used: {state['detail']}")

    duplicate = (
        db.execute(
            select(TestEquipmentUsage).where(
                TestEquipmentUsage.case_id == case.id,
                TestEquipmentUsage.equipment_id == equipment.id,
                TestEquipmentUsage.test_instance_id
                == (test_instance.id if test_instance is not None else None),
            )
        )
        .scalars()
        .first()
    )
    if duplicate is not None:
        raise ValueError("That equipment is already recorded against this test.")

    usage = TestEquipmentUsage(
        case_id=case.id,
        test_instance_id=test_instance.id if test_instance is not None else None,
        equipment_id=equipment.id,
        role=role,
        notes=notes,
    )
    db.add(usage)
    db.flush()
    audit_service.record(
        db,
        event_type="CREATE",
        entity_type="test_equipment_usage",
        entity_id=usage.id,
        actor=actor,
        case_id=case.id,
        after={
            "equipment_code": equipment.code,
            "test_instance_id": str(test_instance.id) if test_instance is not None else None,
            "role": role,
            "calibration_status": state["status"],
            "valid_until": state["valid_until"],
        },
    )
    return usage


def detach_usage(db: Session, *, usage: TestEquipmentUsage, actor=None) -> None:
    data = {
        "equipment_code": usage.equipment.code if usage.equipment else None,
        "test_instance_id": str(usage.test_instance_id) if usage.test_instance_id else None,
        "role": usage.role,
    }
    case_id = usage.case_id
    db.delete(usage)
    db.flush()
    audit_service.record(
        db,
        event_type="DELETE",
        entity_type="test_equipment_usage",
        entity_id=usage.id,
        actor=actor,
        case_id=case_id,
        before=data,
    )
