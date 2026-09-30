"""Applicability driven by instrument configuration (audit item 6).

A test is applicable because of what the instrument is: its class, whether it is
self-indicating, electronic or mechanical, single or multi-range, whether it has
a tare device, a zero-setting device, a level indicator, a battery, interfaces or
embedded software. These tests pin the attributes the applicability expressions
and the checklist conditions read, so a rule can never silently depend on a value
nothing produces.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.rules.loader import load_test_catalogue
from app.services.calculation_engine.engine import evaluate
from app.services.calculation_engine.types import CalcContext, ObservationRow
from app.services.instrument_profile import instrument_flags
from app.services.test_engine.plan import build_applicability_context, generate_plan

CATALOGUE = load_test_catalogue("r76-1-2006-v1")


def instrument(**overrides):
    values = {
        "instrument_class": "III",
        "model": "BENCH-30K",
        "type_designation": "B-30K",
        "serial_number": "SN-0001",
        "max_capacity": Decimal("30000"),
        "min_capacity": Decimal("200"),
        "verification_scale_interval": Decimal("10"),
        "actual_scale_interval": Decimal("10"),
        "unit": "g",
        "is_electronic": True,
        "is_multi_range": False,
        "is_multi_interval": False,
        "has_tare_device": False,
        "has_zero_device": True,
        "has_level_indicator": True,
        "temperature_range": "-10 C to +40 C",
        "power_supply": None,
        "configuration": None,
        "ranges": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def definition(code: str):
    entry = next(
        item
        for item in CATALOGUE["tests"] + CATALOGUE["phase2_tests"]
        if item["test_code"] == code
    )
    return SimpleNamespace(
        test_code=entry["test_code"],
        name=entry["name"],
        clause_reference=entry["clause_reference"],
        category=entry["category"],
        phase=entry["phase"],
        sequence_no=entry["sequence_no"],
        implementation_status=entry["implementation_status"],
        unsupported_reason=entry.get("unsupported_reason"),
        applicability_expression=entry["applicability_expression"],
        id=entry["test_code"],
    )


def applicable(code: str, record) -> bool:
    context = build_applicability_context(record)
    items = {item.test_code: item for item in generate_plan([definition(code)], context=context)}
    return items[code].applicable


# ------------------------------------------------------------ derived flags
def test_a_mains_supply_is_recognised_and_a_battery_is_not_mains():
    mains = instrument_flags(instrument(power_supply="230 V AC, 50 Hz"))
    assert mains["is_mains_powered"] is True
    assert mains["has_battery"] is False

    battery = instrument_flags(instrument(power_supply="Internal rechargeable battery 6 V"))
    assert battery["has_battery"] is True
    assert battery["is_mains_powered"] is False


def test_configuration_declares_self_indication_and_interfaces():
    flags = instrument_flags(
        instrument(
            configuration={
                "self_indicating": False,
                "interfaces": ["RS232", "USB", "Printer"],
                "has_software": True,
            }
        )
    )
    assert flags["is_self_indicating"] is False
    assert flags["interfaces"] == ["rs232", "usb", "printer"]
    assert flags["has_interfaces"] is True
    assert flags["has_printer"] is True
    assert flags["has_software"] is True


def test_an_undeclared_self_indication_stays_unknown_rather_than_guessed():
    flags = instrument_flags(instrument())
    assert flags["is_self_indicating"] is None


def test_the_applicability_context_exposes_the_derived_attributes():
    context = build_applicability_context(
        instrument(power_supply="230 V AC", configuration={"interfaces": ["USB"]})
    )
    payload = context["instrument"]

    assert payload["is_mains_powered"] is True
    assert payload["has_battery"] is False
    assert payload["has_interfaces"] is True
    assert payload["interfaces"] == ["usb"]
    assert payload["is_self_indicating"] is None


def test_the_supply_test_needs_a_mains_connected_instrument():
    mains = instrument(power_supply="230 V AC, 50 Hz")
    battery = instrument(power_supply="Battery 6 V, 4 Ah")

    assert applicable("T-VOLT", mains) is True
    assert applicable("T-VOLT", battery) is False


def test_an_electronic_instrument_makes_the_influence_tests_applicable():
    electronic = instrument(is_electronic=True, power_supply="230 V AC")
    mechanical = instrument(is_electronic=False, power_supply=None)

    assert applicable("T-WARMUP", electronic) is True
    assert applicable("T-WARMUP", mechanical) is False
    assert applicable("T-DISC", mechanical) is False


# ------------------------------------------------- checklist conditions
def checklist_context(**instrument_values):
    record = instrument(**instrument_values)
    payload = {
        "instrument_class": record.instrument_class,
        "e": record.verification_scale_interval,
        "d": record.actual_scale_interval,
        "unit": record.unit,
        "max_capacity": record.max_capacity,
        "min_capacity": record.min_capacity,
        **instrument_flags(record),
    }
    return CalcContext(
        test_code="T-CHK-CON",
        instrument=payload,
        observations=[
            ObservationRow(observation_no=index, item_code=code, conforms=True)
            for index, code in enumerate(
                ["CON-01", "CON-02", "CON-03", "CON-04", "CON-06", "CON-07"], start=1
            )
        ],
        ruleset={"tolerances": {}},
        test_definition=next(
            entry for entry in CATALOGUE["tests"] if entry["test_code"] == "T-CHK-CON"
        ),
    )


def test_a_tare_device_makes_its_checklist_item_mandatory():
    outcome = evaluate(checklist_context(has_tare_device=True))

    assert outcome.status == "INCOMPLETE"
    assert any("CON-05" in error for error in outcome.errors)
    assert any("a tare device is fitted" in error for error in outcome.errors), outcome.errors


def test_an_instrument_without_a_tare_device_does_not_need_that_item():
    outcome = evaluate(checklist_context(has_tare_device=False))

    assert outcome.status == "PASS"
    assert outcome.intermediates["conditional_items_applied"] == {}


def test_a_battery_makes_the_battery_condition_item_mandatory():
    outcome = evaluate(checklist_context(power_supply="Rechargeable battery 6 V"))

    assert outcome.status == "INCOMPLETE"
    assert any("CON-10" in error for error in outcome.errors), outcome.errors
    assert any("battery powered" in error for error in outcome.errors), outcome.errors


def test_embedded_software_makes_the_software_item_mandatory():
    with_software = checklist_context(configuration={"has_software": True})
    with_software.observations.append(
        ObservationRow(observation_no=8, item_code="CON-08", conforms=True)
    )
    outcome = evaluate(with_software)

    assert outcome.status == "PASS"
    assert list(outcome.intermediates["conditional_items_applied"]) == ["CON-08"]


@pytest.mark.parametrize("code", ["T-CHK-CON", "T-CHK-ID"])
def test_the_checklists_are_always_applicable(code):
    assert applicable(code, instrument()) is True
