"""Seed a demonstrable laboratory dataset (PRD 23.2).

    python backend/scripts/seed_db.py
    python backend/scripts/seed_db.py --no-demo-case

Creates one laboratory, one account per role, manufacturer/applicant masters,
test equipment with calibrations, three instrument models and - unless
suppressed - one fully worked Class III evaluation case whose observations,
calculations and compliance results are produced by the deterministic engine.
"""

from __future__ import annotations

import argparse
import struct
import zlib
from datetime import date, timedelta
from decimal import Decimal

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from scripts._bootstrap import banner, ok, warn  # noqa: E402
from scripts.seed_identity import seed_roles_and_permissions  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import session_scope  # noqa: E402
from app.models import (  # noqa: E402
    Applicant,
    CaseStatus,
    EnvironmentalCondition,
    EvaluationCase,
    EquipmentCalibration,
    Instrument,
    InstrumentRange,
    Laboratory,
    Manufacturer,
    ReportTemplate,
    ReportTemplateVersion,
    StandardVersion,
    TestEquipment,
    TestEquipmentUsage,
    TestInstance,
    TestObservation,
    User,
    utcnow,
)
from app.security.passwords import hash_password  # noqa: E402
from app.security.permissions import (  # noqa: E402
    APPROVER,
    ENGINEER,
    LAB_ADMIN,
    REVIEWER,
    SUPER_ADMIN,
)
from app.services import metrology_service  # noqa: E402
from app.services.identity import generate_user_code  # noqa: E402
from app.services.test_engine import generate_and_persist_plan  # noqa: E402

# The demonstration password. It is not shipped to the browser: the sign-in page
# fetches accounts from /auth/demo-accounts, which answers only while DEMO_MODE is
# on. Set DEMO_PASSWORD to rotate the seeded hash and what that endpoint offers
# together (audit item 3).
DEFAULT_PASSWORD = settings.DEMO_PASSWORD

DEMO_OBSERVATIONS: dict[str, list[dict]] = {
    "T-WP": [
        {"observation_no": 1, "position_label": "Zero", "load": "0", "indication": "0", "additional_load": "5"},
        {"observation_no": 2, "position_label": "5 kg", "load": "5000", "indication": "5003", "additional_load": "2"},
        {"observation_no": 3, "position_label": "10 kg", "load": "10000", "indication": "10002", "additional_load": "3"},
        {"observation_no": 4, "position_label": "15 kg", "load": "15000", "indication": "15001", "additional_load": "4"},
        {"observation_no": 5, "position_label": "20 kg", "load": "20000", "indication": "20003", "additional_load": "2"},
        {"observation_no": 6, "position_label": "25 kg", "load": "25000", "indication": "25004", "additional_load": "1"},
        {"observation_no": 7, "position_label": "30 kg", "load": "30000", "indication": "30005", "additional_load": "0"},
    ],
    "T-REP": [
        {"observation_no": index, "position_label": f"Run {index}", "load": "15000",
         "indication": value, "additional_load": "5"}
        for index, value in enumerate(["15000", "15002", "14999", "15001", "15000"], start=1)
    ],
    "T-ECC": [
        {"observation_no": 1, "position_label": "centre", "load": "15000", "indication": "15000", "additional_load": "5"},
        {"observation_no": 2, "position_label": "front-left", "load": "15000", "indication": "15002", "additional_load": "5"},
        {"observation_no": 3, "position_label": "front-right", "load": "15000", "indication": "15001", "additional_load": "5"},
        {"observation_no": 4, "position_label": "rear-left", "load": "15000", "indication": "14999", "additional_load": "5"},
        {"observation_no": 5, "position_label": "rear-right", "load": "15000", "indication": "15000", "additional_load": "5"},
    ],
    "T-ZR": [
        {"observation_no": 1, "position_label": "initial", "value": "0"},
        {"observation_no": 2, "position_label": "after unloading", "value": "3"},
    ],
    "T-CREEP": [
        {"observation_no": 1, "position_label": "t0", "elapsed_seconds": "0", "value": "15000", "temperature_c": "20"},
        {"observation_no": 2, "position_label": "t+30min", "elapsed_seconds": "1800", "value": "15001", "temperature_c": "20"},
        {"observation_no": 3, "position_label": "t+4h", "elapsed_seconds": "14400", "value": "15003", "temperature_c": "21"},
    ],
    "T-TEMP-NL": [
        {"observation_no": index, "position_label": f"{temperature} C", "temperature_c": str(temperature), "value": value}
        for index, (temperature, value) in enumerate(
            [(15, "0"), (20, "1"), (25, "2"), (30, "3"), (35, "4")], start=1
        )
    ],
    "T-SENS": [
        {"observation_no": 1, "position_label": "before", "load": "15000", "indication": "15000"},
        {"observation_no": 2, "position_label": "after", "load": "15010", "indication": "15010"},
    ],
    "T-DISC": [
        {"observation_no": 1, "position_label": "additional 14 g applied", "load": "14", "value": "10"},
    ],
    "T-STAB": [
        {"observation_no": 1, "position_label": "reading 1", "elapsed_seconds": "0", "value": "15000"},
        {"observation_no": 2, "position_label": "reading 2", "elapsed_seconds": "60", "value": "15001"},
        {"observation_no": 3, "position_label": "reading 3", "elapsed_seconds": "120", "value": "15000"},
    ],
    "T-CHK-ID": [
        {"observation_no": index, "position_label": code, "value": None, "input_payload": {"item_code": code, "conforms": True}}
        for index, code in enumerate(
            ["ID-01", "ID-02", "ID-03", "ID-04", "ID-05", "ID-06", "ID-07", "ID-08", "ID-09", "ID-10"], start=1
        )
    ],
    "T-CHK-CON": [
        {"observation_no": index, "position_label": code, "value": None, "input_payload": {"item_code": code, "conforms": True}}
        for index, code in enumerate(
            ["CON-01", "CON-02", "CON-03", "CON-04", "CON-05", "CON-06", "CON-07", "CON-08", "CON-09", "CON-10"],
            start=1,
        )
    ],
}


def _get_or_create(db, model, defaults: dict | None = None, **filters):
    instance = db.execute(select(model).filter_by(**filters)).scalars().first()
    if instance is None:
        instance = model(**filters, **(defaults or {}))
        db.add(instance)
        db.flush()
    return instance


def _placeholder_png(label: str, rgb: tuple[int, int, int]) -> bytes:
    """A small valid PNG used as stand-in demonstration evidence.

    Built from the standard library so the seed has no imaging dependency. The
    pixels encode nothing; the point is a real, byte-valid image of the kind the
    mandatory-evidence rule accepts.
    """
    width, height = 240, 160
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        payload = tag + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"tEXt", b"Description\x00" + label.encode("ascii", "replace"))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def seed_laboratory(db) -> Laboratory:
    laboratory = _get_or_create(
        db,
        Laboratory,
        {
            "name": "MetrIQ Regional Legal Metrology Laboratory",
            "location": "Bengaluru",
            "address": "Plot 12, Industrial Estate, Bengaluru 560058",
            "contact_email": "lab@metriq.local",
            "accreditation_no": "NABL-LM-2026-0042",
        },
        code="LAB-001",
    )
    ok(f"laboratory {laboratory.code} - {laboratory.name}")
    return laboratory


def seed_users(
    db,
    laboratory: Laboratory,
    password: str,
    *,
    include_super_admin: bool = True,
) -> dict[str, User]:
    """Seed demo roles, optionally leaving Super Admin to the caller."""
    people = [
        (SUPER_ADMIN, "Platform Administrator", "admin@metriq.local", "Platform Owner"),
        (LAB_ADMIN, "Lakshmi Rao", "labadmin@metriq.local", "Laboratory Manager"),
        (ENGINEER, "Arjun Mehta", "engineer@metriq.local", "Test Engineer"),
        (REVIEWER, "Priya Nair", "reviewer@metriq.local", "Technical Reviewer"),
        (APPROVER, "Dr. Rao Krishnan", "approver@metriq.local", "Approving Authority"),
    ]
    if not include_super_admin:
        people = [person for person in people if person[0] != SUPER_ADMIN]
    users: dict[str, User] = {}
    for role, name, email, designation in people:
        user = db.execute(select(User).where(User.email == email)).scalars().first()
        if user is None:
            user = User(
                user_code=generate_user_code(db, role),
                email=email,
                full_name=name,
                designation=designation,
                role_code=role,
                laboratory_id=laboratory.id if role != SUPER_ADMIN else None,
                is_active=True,
                is_email_verified=True,
                is_demo=True,
                auth_provider="local",
                password_hash=hash_password(password),
            )
            db.add(user)
            db.flush()
        users[role] = user
    ok(f"users: {', '.join(user.email for user in users.values())}")
    return users


def seed_masters(db) -> dict:
    manufacturer = _get_or_create(
        db, Manufacturer,
        {"contact_person": "R. Sharma", "email": "sales@sartorius-example.in", "city": "Pune",
         "address": "MIDC Bhosari, Pune 411026", "country": "India"},
        name="Sartorius Industrial Weighing (demo)",
    )
    second = _get_or_create(
        db, Manufacturer,
        {"contact_person": "K. Iyer", "city": "Chennai", "country": "India"},
        name="Precision Balance Systems (demo)",
    )
    applicant = _get_or_create(
        db, Applicant,
        {"organisation_type": "Manufacturer representative", "contact_person": "N. Verma",
         "email": "compliance@example-applicant.in", "city": "New Delhi", "country": "India"},
        name="Example Instruments Pvt. Ltd. (demo)",
    )
    ok("manufacturers and applicants ready")
    return {"manufacturer": manufacturer, "second_manufacturer": second, "applicant": applicant}


def seed_equipment(db, laboratory: Laboratory) -> dict:
    equipment = {}
    sets = [
        ("EQ-WTS-01", "OIML M1 test weight set 1 mg to 20 kg", "weights", "M1", "g"),
        ("EQ-THM-01", "Digital thermometer, -10 to 50 C", "thermometer", "+/- 0.5 C", "C"),
        ("EQ-HYG-01", "Relative humidity meter", "hygrometer", "+/- 2 %RH", "%RH"),
        ("EQ-BAR-01", "Barometric pressure gauge", "pressure_gauge", "+/- 0.1 kPa", "kPa"),
    ]
    for code, name, kind, accuracy, unit in sets:
        equipment[code] = _get_or_create(
            db, TestEquipment,
            {"name": name, "equipment_type": kind, "accuracy_class": accuracy,
             "laboratory_id": laboratory.id, "is_active": True, "manufacturer": "Demo Instruments",
             "model": code.split("-")[1] + " series", "serial_no": f"{code}-2026",
             "unit": unit},
            code=code,
        )
    for item in equipment.values():
        if not item.calibrations:
            db.add(
                EquipmentCalibration(
                    equipment_id=item.id,
                    certificate_no=f"CAL-{item.code}-2026",
                    issued_by="Regional Calibration Service (demo)",
                    issue_date=date.today() - timedelta(days=120),
                    valid_until=date.today() + timedelta(days=245),
                    uncertainty="See certificate",
                )
            )
    db.flush()
    ok(f"test equipment: {', '.join(equipment)}")
    return equipment


def seed_instruments(db, manufacturer: Manufacturer, second: Manufacturer) -> dict:
    instruments: dict[str, Instrument] = {}
    definitions = [
        {
            "key": "class3",
            "model": "SIW-30K",
            "type_designation": "SIW-30K-III",
            "serial_number": "SIW30K-2026-0117",
            "instrument_class": "III",
            "max_capacity": "30000",
            "min_capacity": "200",
            "e": "10",
            "d": "10",
            "unit": "g",
            "manufacturer": manufacturer,
            "ranges": [],
        },
        {
            "key": "class2",
            "model": "PB-5000",
            "type_designation": "PB-5000-II",
            "serial_number": "PB5000-2026-0043",
            "instrument_class": "II",
            "max_capacity": "5000",
            "min_capacity": "10",
            "e": "0.1",
            "d": "0.1",
            "unit": "g",
            "manufacturer": second,
            "ranges": [],
        },
        {
            "key": "multirange",
            "model": "MR-6000",
            "type_designation": "MR-6000-III",
            "serial_number": "MR6000-2026-0009",
            "instrument_class": "III",
            "max_capacity": "6000",
            "min_capacity": "100",
            "e": "2",
            "d": "1",
            "unit": "g",
            "manufacturer": manufacturer,
            "is_multi_range": True,
            "ranges": [
                {"range_no": 1, "min_capacity": "100", "max_capacity": "3000", "e": "1", "d": "1"},
                {"range_no": 2, "min_capacity": "3000", "max_capacity": "6000", "e": "2", "d": "2"},
            ],
        },
    ]
    for definition in definitions:
        key = definition.pop("key")
        ranges = definition.pop("ranges", [])
        manufacturer_ref = definition.pop("manufacturer")
        definition["verification_scale_interval"] = Decimal(definition.pop("e"))
        if "d" in definition:
            definition["actual_scale_interval"] = Decimal(definition.pop("d"))
        definition["max_capacity"] = Decimal(definition["max_capacity"])
        if definition.get("min_capacity") is not None:
            definition["min_capacity"] = Decimal(definition["min_capacity"])
        existing = db.execute(
            select(Instrument).where(Instrument.serial_number == definition["serial_number"])
        ).scalars().first()
        if existing is not None:
            instruments[key] = existing
            continue
        instrument = Instrument(
            manufacturer_id=manufacturer_ref.id,
            is_electronic=True,
            has_zero_device=True,
            has_level_indicator=True,
            temperature_range="-10 C to +40 C",
            power_supply="230 V AC / internal rechargeable battery",
            **definition,
        )
        for item in ranges:
            instrument.ranges.append(
                InstrumentRange(
                    unit=definition["unit"],
                    min_capacity=Decimal(item["min_capacity"]),
                    max_capacity=Decimal(item["max_capacity"]),
                    verification_scale_interval=Decimal(item.pop("e")),
                    actual_scale_interval=Decimal(item["d"]) if "d" in item else None,
                    **{k: v for k, v in item.items() if k not in {"d", "min_capacity", "max_capacity"}},
                )
            )
        db.add(instrument)
        db.flush()
        instruments[key] = instrument
    ok("instruments: " + ", ".join(f"{item.model} ({item.instrument_class})" for item in instruments.values()))
    return instruments


def seed_demo_case(db, *, laboratory: Laboratory, users: dict, masters: dict, equipment: dict,
                   instruments: dict) -> EvaluationCase | None:
    existing = db.execute(select(EvaluationCase)).scalars().first()
    if existing is not None:
        warn(f"a case already exists ({existing.application_no}); skipping demo case creation")
        return existing

    standard_version = db.execute(
        select(StandardVersion).where(StandardVersion.is_active.is_(True))
    ).scalars().first()
    if standard_version is None:
        warn("no active ruleset found; run seed_rules.py first")
        return None
    template_version = db.execute(
        select(ReportTemplateVersion)
        .join(ReportTemplate, ReportTemplate.id == ReportTemplateVersion.template_id)
        .where(ReportTemplateVersion.is_active.is_(True))
    ).scalars().first()

    instrument = instruments["class3"]
    case = EvaluationCase(
        application_no=f"NAWI-{utcnow().year}-000001",
        title=f"{instrument.model} type evaluation (Class {instrument.instrument_class})",
        instrument_id=instrument.id,
        applicant_id=masters["applicant"].id,
        manufacturer_id=masters["manufacturer"].id,
        laboratory_id=laboratory.id,
        engineer_id=users[ENGINEER].id,
        reviewer_id=users[REVIEWER].id,
        approver_id=users[APPROVER].id,
        standard_version_id=standard_version.id,
        template_version_id=template_version.id if template_version else None,
        status=CaseStatus.IN_PROGRESS,
        purpose=(
            "Initial type evaluation of a Class III non-automatic weighing instrument for the "
            "SIH demonstration dataset."
        ),
        application_date=utcnow(),
        created_by=users[ENGINEER].id,
    )
    db.add(case)
    db.flush()

    generate_and_persist_plan(db, case=case, user=users[ENGINEER])

    db.add(
        EnvironmentalCondition(
            case_id=case.id, label="Ambient", temperature_c="20.5", relative_humidity_pct="48",
            barometric_pressure_kpa="101.2", started_at=utcnow(), ended_at=utcnow(),
            recorded_by=users[ENGINEER].id, notes="Stable laboratory conditions during testing.",
        )
    )
    for code in ("EQ-WTS-01", "EQ-THM-01", "EQ-HYG-01"):
        if code in equipment:
            db.add(TestEquipmentUsage(case_id=case.id, equipment_id=equipment[code].id,
                                      role="Reference standard" if code == "EQ-WTS-01" else "Environment"))
    db.flush()

    db.refresh(case)
    for test_instance in case.tests:
        code = test_instance.definition.test_code
        rows = DEMO_OBSERVATIONS.get(code)
        if not rows or test_instance.applicability_status == "NOT_APPLICABLE":
            continue
        for row in rows:
            payload = row.get("input_payload") or {}
            db.add(
                TestObservation(
                    test_instance_id=test_instance.id,
                    observation_no=row["observation_no"],
                    position_label=row.get("position_label"),
                    load=row.get("load"),
                    indication=row.get("indication"),
                    additional_load=row.get("additional_load"),
                    elapsed_seconds=row.get("elapsed_seconds"),
                    temperature_c=row.get("temperature_c"),
                    value=row.get("value"),
                    input_payload=payload,
                    created_by=users[ENGINEER].id,
                )
            )
        test_instance.status = "IN_PROGRESS"
        test_instance.started_at = utcnow()
    db.flush()

    db.refresh(case)
    for test_instance in case.tests:
        if test_instance.applicability_status == "NOT_APPLICABLE":
            continue
        if not test_instance.observations:
            continue
        evaluation = metrology_service.evaluate_test_instance(
            db, case=case, test_instance=test_instance, actor=users[ENGINEER]
        )
        if evaluation.decision.status in {"PASS", "FAIL", "NOT_APPLICABLE", "WAIVED"}:
            test_instance.status = "COMPLETED"
            test_instance.completed_at = utcnow()
            test_instance.completed_by = users[ENGINEER].id
    db.flush()

    from app.services.attachment_service import store_upload

    for category, caption, rgb in (
        ("nameplate_photograph", "Instrument nameplate - Class III, Max 30 kg, e = d = 10 g", (58, 74, 102)),
        ("test_setup_photograph", "Test setup - instrument levelled on the bench with the standard weights", (74, 96, 122)),
    ):
        store_upload(
            db,
            case=case,
            filename=f"{case.application_no}-{category}.png",
            data=_placeholder_png(caption, rgb),
            content_type="image/png",
            caption=caption,
            category=category,
            actor=users[ENGINEER],
        )
    db.flush()
    ok(f"demo case {case.application_no} with {len(case.tests)} planned tests and 2 mandatory photographs")
    return case


def main() -> int:
    banner("MetrIQ - seed demonstration dataset")
    parser = argparse.ArgumentParser(description="Seed a demonstrable MetrIQ dataset.")
    parser.add_argument("--password", default=DEFAULT_PASSWORD,
                        help="Password assigned to every seeded demo account.")
    parser.add_argument("--no-demo-case", action="store_true",
                        help="Seed masters only, without a worked evaluation case.")
    args = parser.parse_args()

    if len(args.password) < 8:
        print("  Refusing to run: the demo password must be at least 8 characters.")
        return 2

    with session_scope() as db:
        seed_roles_and_permissions(db)
        laboratory = seed_laboratory(db)
        users = seed_users(db, laboratory, args.password)
        masters = seed_masters(db)
        equipment = seed_equipment(db, laboratory)
        instruments = seed_instruments(db, masters["manufacturer"], masters["second_manufacturer"])
        if not args.no_demo_case:
            seed_demo_case(
                db, laboratory=laboratory, users=users, masters=masters,
                equipment=equipment, instruments=instruments,
            )

    print()
    warn(f"All demo accounts share the password '{args.password}'.")
    warn("Change these credentials before any deployment beyond a local demonstration.")
    print()
    print("  Sign in with, for example: engineer@metriq.local / " + args.password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
