"""Boundary and regression cases for the rules a metrology reviewer signs off.

Audit item 6 asks for two things that code alone cannot supply: a named
reviewer's approval for every active rule, and automated boundary cases for each
rule that was verified. This module is the second half. Each case sits exactly
on, or one increment either side of, a numeric edge that decides a PASS/FAIL -
the place where an off-by-one in a comparison operator or a wrong rounding
policy stops being invisible.

``BOUNDARY_CASES`` maps every rule and test in the shipped catalogue to the
place its edges are exercised. ``test_every_catalogue_rule_has_a_boundary_case``
fails if the catalogue grows a rule this module does not cover, so the map
cannot quietly go stale - which is the failure mode a coverage matrix exists to
prevent.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

API = "/api/v1"

CATALOGUE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "rules"
    / "data"
    / "test-catalogue-r76-1-2006-v1.json"
)

#: Every catalogue test code mapped to the boundary case that exercises it.
BOUNDARY_CASES: dict[str, str] = {
    "T-WP": "test_weighing_performance_error_is_inclusive_at_the_mpe",
    "T-REP": "test_repeatability_spread_is_inclusive_at_the_mpe",
    "T-ECC": "test_eccentricity_difference_is_inclusive_at_the_mpe",
    "T-ZR": "test_zero_return_tolerance_is_inclusive",
    "T-CREEP": "test_creep_tolerance_is_inclusive",
    "T-TEMP-NL": "test_temperature_no_load_tolerance_is_inclusive",
    "T-SENS": "test_sensitivity_tolerance_is_inclusive",
    "T-DISC": "test_discrimination_requires_at_least_one_scale_interval",
    "T-STAB": "test_stability_tolerance_is_inclusive",
    "T-CHK-CON": "test_construction_checklist_requires_every_mandatory_item",
    "T-CHK-ID": "test_identification_checklist_requires_every_mandatory_item",
    "T-TILT": "test_tilt_tolerance_is_inclusive",
    "T-TARE": "test_tare_tolerance_is_inclusive",
    "T-WARMUP": "test_warmup_tolerance_is_inclusive",
    "T-VOLT": "test_voltage_variation_tolerance_is_inclusive",
    "T-EMC": "test_immunity_tolerance_is_inclusive",
    "T-DAMP": "test_damp_heat_tolerance_is_inclusive",
    "MPE-BANDS": "test_verification_band_edges",
    "MPE-IN-SERVICE": "test_in_service_band_edges",
    "MPE-ZERO": "test_mpe_at_zero_load",
    "R76-ZR-01": "test_zero_return_tolerance_is_inclusive",
    "R76-CREEP-01": "test_creep_tolerance_is_inclusive",
    "R76-TEMP-NL-01": "test_temperature_no_load_tolerance_is_inclusive",
    "R76-SENS-01": "test_sensitivity_tolerance_is_inclusive",
    "R76-DISC-01": "test_discrimination_requires_at_least_one_scale_interval",
    "R76-STAB-01": "test_stability_tolerance_is_inclusive",
    "R76-ECC-01": "test_eccentricity_difference_is_inclusive_at_the_mpe",
    "R76-TILT-01": "test_tilt_tolerance_is_inclusive",
    "R76-TARE-01": "test_tare_tolerance_is_inclusive",
    "R76-WARMUP-01": "test_warmup_tolerance_is_inclusive",
    "R76-VOLT-01": "test_voltage_variation_tolerance_is_inclusive",
    "R76-EMC-01": "test_immunity_tolerance_is_inclusive",
    "R76-DAMP-01": "test_damp_heat_tolerance_is_inclusive",
    "R76-TILT": "test_tilt_tolerance_is_inclusive",
    "R76-TARE": "test_tare_tolerance_is_inclusive",
    "R76-WARMUP": "test_warmup_tolerance_is_inclusive",
    "R76-VOLTAGE-VARIATION": "test_voltage_variation_tolerance_is_inclusive",
    "R76-EMC-IMMUNITY": "test_immunity_tolerance_is_inclusive",
    "R76-DAMP-HEAT": "test_damp_heat_tolerance_is_inclusive",
    "R76-ZR": "test_zero_return_tolerance_is_inclusive",
    "R76-CREEP": "test_creep_tolerance_is_inclusive",
    "R76-TEMP-NL": "test_temperature_no_load_tolerance_is_inclusive",
    "R76-SENS": "test_sensitivity_tolerance_is_inclusive",
    "R76-DISC": "test_discrimination_requires_at_least_one_scale_interval",
    "R76-STAB": "test_stability_tolerance_is_inclusive",
    "R76-ECC": "test_eccentricity_difference_is_inclusive_at_the_mpe",
}


def catalogue() -> dict:
    with CATALOGUE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def implemented_codes() -> set[str]:
    """Every test the catalogue says it actually implements."""
    payload = catalogue()
    codes = {entry["test_code"] for entry in payload["tests"]}
    for entry in payload.get("phase2_tests", []):
        if entry.get("implementation_status") == "implemented":
            codes.add(entry["test_code"])
    return codes


def test_every_catalogue_rule_has_a_boundary_case():
    """The map above must cover every rule a reviewer is asked to sign off.

    A rule that is deliberately not implemented is excluded, because there is
    nothing to exercise - but it must say so in the catalogue rather than be
    quietly absent from it (audit item 7).
    """
    codes = implemented_codes()
    codes |= {"MPE-BANDS", "MPE-IN-SERVICE", "MPE-ZERO"}
    missing = sorted(code for code in codes if code not in BOUNDARY_CASES)
    assert not missing, f"no boundary case recorded for: {missing}"


# ------------------------------------------------------------ MPE band edges
# Class III, e = 10 g, initial verification. The ruleset states the resolution
# rule as "the first band whose upper bound is not exceeded wins", so m = 500
# belongs to the lower band. These cases pin that decision down.
@pytest.mark.parametrize(
    "load, expected_factor, expected_band",
    [
        ("0", "0.5", 0),
        ("4999.9", "0.5", 0),      # m = 499.99
        ("5000", "0.5", 0),        # m = 500 exactly - the documented edge
        ("5000.1", "1.0", 1),      # one tenth of an e past the edge
        ("19999.9", "1.0", 1),     # m = 1999.99
        ("20000", "1.0", 1),       # m = 2000 exactly - the second edge
        ("20000.1", "1.5", 2),     # m = 2000.01
        ("300000", "1.5", 2),      # deep in the open-ended band
    ],
)
def test_verification_band_edges(client, tokens, load, expected_factor, expected_band):
    from app.security.permissions import ENGINEER

    response = client.post(
        f"{API}/calculations/mpe",
        json={"instrument_class": "III", "load": load, "e": "10", "stage": "verification"},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["factor"]) == Decimal(expected_factor), body
    assert body["band_index"] == expected_band, body
    assert body["unit"] == "e"
    # The MPE is expressed in the instrument's own scale interval; compare as
    # decimals so a trimmed trailing zero is not read as a difference.
    assert Decimal(body["mpe_value"]) == Decimal(expected_factor) * 10, body


@pytest.mark.parametrize(
    "load, expected_factor",
    [
        ("5000", "1.0"),      # in service: tolerance doubles at every edge
        ("20000", "2.0"),
        ("20000.1", "3.0"),
    ],
)
def test_in_service_band_edges(client, tokens, load, expected_factor):
    from app.security.permissions import ENGINEER

    response = client.post(
        f"{API}/calculations/mpe",
        json={"instrument_class": "III", "load": load, "e": "10", "stage": "in_service"},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text
    assert Decimal(response.json()["factor"]) == Decimal(expected_factor), response.text


def test_mpe_at_zero_load(client, tokens):
    """The zero point is a separate rule (R76-MPE-ZERO), not the first band."""
    from app.services.calculation_engine.mpe import resolve_mpe_at_zero
    from app.rules.loader import load_ruleset

    resolution = resolve_mpe_at_zero(load_ruleset(), instrument_class="III", e="10")
    assert resolution is not None
    assert resolution.rule_id == "R76-MPE-ZERO"
    assert resolution.mpe_value == Decimal("2.5")   # 0.25 e at e = 10 g
    assert resolution.unit == "e"


# ----------------------------------------------------- PASS/FAIL inclusivity
# Every comparison in the ruleset is "not greater than", so a result exactly on
# the limit passes. Each case below is one increment either side.
def _preview(client, tokens, test_code, instrument_extra, observations):
    from app.security.permissions import ENGINEER

    # The engine addresses the scale intervals as `e` and `d`; the case record
    # calls them verification_scale_interval / actual_scale_interval.
    instrument = {
        "instrument_class": "III",
        "max_capacity": "30000",
        "min_capacity": "200",
        "e": "10",
        "d": "10",
        "unit": "g",
        **instrument_extra,
    }
    response = client.post(
        f"{API}/calculations/preview",
        json={"test_code": test_code, "instrument": instrument, "observations": observations},
        headers=tokens[ENGINEER],
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_weighing_performance_error_is_inclusive_at_the_mpe(client, tokens):
    """m = 1000 e, so the MPE is 1.0 e = 10 g; an error of exactly 10 g passes."""
    on_the_limit = _preview(
        client, tokens, "T-WP", {},
        [{"observation_no": 1, "load": "10000", "indication": "10010", "additional_load": "5"}],
    )
    assert on_the_limit["compliance"]["limit_value"] == "10"
    assert on_the_limit["compliance"]["measured_value"] == "10"
    assert on_the_limit["compliance"]["status"] == "PASS", on_the_limit

    just_past = _preview(
        client, tokens, "T-WP", {},
        [{"observation_no": 1, "load": "10000", "indication": "10020", "additional_load": "5"}],
    )
    assert just_past["compliance"]["measured_value"] == "20"
    assert just_past["compliance"]["status"] == "FAIL", just_past

    just_inside = _preview(
        client, tokens, "T-WP", {},
        [{"observation_no": 1, "load": "10000", "indication": "10000", "additional_load": "5"}],
    )
    assert just_inside["compliance"]["status"] == "PASS", just_inside


def test_repeatability_spread_is_inclusive_at_the_mpe(client, tokens):
    """Spread across repeats is compared against the MPE for the load applied."""
    rows = [
        {"observation_no": 1, "load": "15000", "indication": "15000", "additional_load": "5"},
        {"observation_no": 2, "load": "15000", "indication": "15010", "additional_load": "5"},
    ]
    on_the_limit = _preview(client, tokens, "T-REP", {}, rows)
    assert on_the_limit["compliance"]["status"] == "PASS", on_the_limit

    rows[1]["indication"] = "15020"
    just_past = _preview(client, tokens, "T-REP", {}, rows)
    assert just_past["compliance"]["status"] == "FAIL", just_past


def test_eccentricity_difference_is_inclusive_at_the_mpe(client, tokens):
    rows = [
        {"observation_no": 1, "position_label": "centre", "load": "15000", "indication": "15000"},
        {"observation_no": 2, "position_label": "front-left", "load": "15000", "indication": "15010"},
    ]
    assert _preview(client, tokens, "T-ECC", {}, rows)["compliance"]["status"] == "PASS"

    rows[1]["indication"] = "15020"
    assert _preview(client, tokens, "T-ECC", {}, rows)["compliance"]["status"] == "FAIL"


def test_zero_return_tolerance_is_inclusive(client, tokens):
    """R76-ZR-01: the zero indication may drift by 0.5 e = 5 g, no more."""
    rows = [
        {"observation_no": 1, "position_label": "initial", "value": "0"},
        {"observation_no": 2, "position_label": "after unloading", "value": "5"},
    ]
    assert _preview(client, tokens, "T-ZR", {}, rows)["compliance"]["status"] == "PASS"

    rows[1]["value"] = "5.1"
    assert _preview(client, tokens, "T-ZR", {}, rows)["compliance"]["status"] == "FAIL"


def test_creep_tolerance_is_inclusive(client, tokens):
    rows = [
        {"observation_no": 1, "position_label": "t = 0", "value": "0", "elapsed_seconds": "0"},
        {"observation_no": 2, "position_label": "t = 15 min", "value": "5",
         "elapsed_seconds": "900"},
    ]
    assert _preview(client, tokens, "T-CREEP", {}, rows)["compliance"]["status"] == "PASS"
    rows[1]["value"] = "6"
    assert _preview(client, tokens, "T-CREEP", {}, rows)["compliance"]["status"] == "FAIL"


def test_temperature_no_load_tolerance_is_inclusive(client, tokens):
    """R76-TEMP-NL-01: 1.0 e = 10 g."""
    rows = [
        {"observation_no": 1, "position_label": "reference", "value": "0", "temperature_c": "20"},
        {"observation_no": 2, "position_label": "after temperature change", "value": "10",
         "temperature_c": "40"},
    ]
    assert _preview(client, tokens, "T-TEMP-NL", {}, rows)["compliance"]["status"] == "PASS"
    rows[1]["value"] = "11"
    assert _preview(client, tokens, "T-TEMP-NL", {}, rows)["compliance"]["status"] == "FAIL"


def test_tilt_tolerance_is_inclusive(client, tokens):
    """R76-TILT-01: the tilt limit is the MPE for the applied load."""
    rows = [
        {"observation_no": 1, "position_label": "level", "load": "15000", "indication": "15000"},
        {"observation_no": 2, "position_label": "tilted 1", "load": "15000", "indication": "15010"},
    ]
    assert _preview(client, tokens, "T-TILT", {}, rows)["compliance"]["status"] == "PASS"
    rows[1]["indication"] = "15011"
    assert _preview(client, tokens, "T-TILT", {}, rows)["compliance"]["status"] == "FAIL"


def test_tare_tolerance_is_inclusive(client, tokens):
    """R76-TARE-01: the error of the tared reading may be 0.5 e = 5 g, no more."""
    rows = [
        {"observation_no": 1, "position_label": "tared zero", "load": "0",
         "indication": "0", "additional_load": "5"},
        {"observation_no": 2, "position_label": "load applied", "load": "10000",
         "indication": "10005", "additional_load": "5"},
    ]
    assert _preview(client, tokens, "T-TARE", {}, rows)["compliance"]["status"] == "PASS"
    rows[1]["indication"] = "10006"
    assert _preview(client, tokens, "T-TARE", {}, rows)["compliance"]["status"] == "FAIL"


def test_warmup_tolerance_is_inclusive(client, tokens):
    """R76-WARMUP-01: the drift over the warm-up period may be 1.0 e = 10 g."""
    rows = [
        {"observation_no": 1, "position_label": "power-on", "elapsed_seconds": "0",
         "load": "10000", "indication": "10000"},
        {"observation_no": 2, "position_label": "after 30 min", "elapsed_seconds": "1800",
         "load": "10000", "indication": "10010"},
    ]
    assert _preview(client, tokens, "T-WARMUP", {}, rows)["compliance"]["status"] == "PASS"
    rows[1]["indication"] = "10011"
    assert _preview(client, tokens, "T-WARMUP", {}, rows)["compliance"]["status"] == "FAIL"


def test_voltage_variation_tolerance_is_inclusive(client, tokens):
    """R76-VOLT-01: the change between supply conditions may be 1.0 e = 10 g."""
    rows = [
        {"observation_no": 1, "position_label": "nominal supply",
         "load": "15000", "indication": "15000", "voltage_v": "230"},
        {"observation_no": 2, "position_label": "lower limit",
         "load": "15000", "indication": "15010", "voltage_v": "207"},
    ]
    assert _preview(client, tokens, "T-VOLT", {}, rows)["compliance"]["status"] == "PASS"
    rows[1]["indication"] = "15011"
    assert _preview(client, tokens, "T-VOLT", {}, rows)["compliance"]["status"] == "FAIL"


def test_immunity_tolerance_is_inclusive(client, tokens):
    """R76-EMC-01: the disturbance may move the indication by 1.0 e = 10 g."""
    rows = [
        {"observation_no": 1, "position_label": "before disturbance",
         "load": "15000", "indication": "15000"},
        {"observation_no": 2, "position_label": "during burst",
         "load": "15000", "indication": "15010"},
    ]
    assert _preview(client, tokens, "T-EMC", {}, rows)["compliance"]["status"] == "PASS"
    rows[1]["indication"] = "15011"
    assert _preview(client, tokens, "T-EMC", {}, rows)["compliance"]["status"] == "FAIL"


def test_damp_heat_tolerance_is_inclusive(client, tokens):
    """R76-DAMP-01: after conditioning the instrument must meet its MPE again."""
    rows = [
        {"observation_no": 1, "position_label": "before conditioning",
         "load": "15000", "indication": "15000", "temperature_c": "20"},
        {"observation_no": 2, "position_label": "after conditioning",
         "load": "15000", "indication": "15010", "temperature_c": "40", "humidity_percent": "93"},
    ]
    assert _preview(client, tokens, "T-DAMP", {}, rows)["compliance"]["status"] == "PASS"
    rows[1]["indication"] = "15011"
    assert _preview(client, tokens, "T-DAMP", {}, rows)["compliance"]["status"] == "FAIL"


def test_sensitivity_tolerance_is_inclusive(client, tokens):
    """R76-SENS-01: the response error may be 1.0 e = 10 g, no more.

    A 10 g load change and a 20 g indication change give a response error of
    exactly 10 g, which is on the limit and therefore passes.
    """
    rows = [
        {"observation_no": 1, "position_label": "initial",
         "load": "10000", "indication": "10000"},
        {"observation_no": 2, "position_label": "after a 1 e load change",
         "load": "10010", "indication": "10020"},
    ]
    on_the_limit = _preview(client, tokens, "T-SENS", {}, rows)
    assert on_the_limit["compliance"]["limit_value"] == "10", on_the_limit
    assert on_the_limit["compliance"]["measured_value"] == "10", on_the_limit
    assert on_the_limit["compliance"]["status"] == "PASS", on_the_limit

    rows[1]["indication"] = "10030"
    just_past = _preview(client, tokens, "T-SENS", {}, rows)
    assert just_past["compliance"]["measured_value"] == "20", just_past
    assert just_past["compliance"]["status"] == "FAIL", just_past


def test_stability_tolerance_is_inclusive(client, tokens):
    rows = [
        {"observation_no": 1, "position_label": "first", "value": "0"},
        {"observation_no": 2, "position_label": "last", "value": "10"},
    ]
    assert _preview(client, tokens, "T-STAB", {}, rows)["compliance"]["status"] == "PASS"
    rows[1]["value"] = "10.1"
    assert _preview(client, tokens, "T-STAB", {}, rows)["compliance"]["status"] == "FAIL"


def test_discrimination_requires_at_least_one_scale_interval(client, tokens):
    """R76-DISC-01 uses `gte`: exactly one d satisfies the rule, 0.9 d does not.

    The smallest observed change governs, so this is one row per changeover
    attempt, each carrying the additional load that produced the change.
    """
    rows = [
        {"observation_no": 1, "position_label": "changeover",
         "load": "14", "value": "10"},
    ]
    on_the_limit = _preview(client, tokens, "T-DISC", {}, rows)
    assert on_the_limit["compliance"]["limit_value"] == "10", on_the_limit
    assert on_the_limit["compliance"]["status"] == "PASS", on_the_limit

    rows[0]["value"] = "9"
    just_short = _preview(client, tokens, "T-DISC", {}, rows)
    assert just_short["compliance"]["status"] == "FAIL", just_short


def test_construction_checklist_requires_every_mandatory_item(client, tokens):
    payload = catalogue()
    mandatory = next(
        test["calculation_rules"]["mandatory_items"]
        for test in payload["tests"]
        if test["test_code"] == "T-CHK-CON"
    )
    complete = [{"observation_no": index + 1, "item_code": code, "conforms": True}
                for index, code in enumerate(mandatory)]
    assert _preview(client, tokens, "T-CHK-CON", {}, complete)["compliance"]["status"] == "PASS"

    incomplete = [dict(row) for row in complete]
    incomplete[0]["conforms"] = False
    assert _preview(client, tokens, "T-CHK-CON", {}, incomplete)["compliance"]["status"] == "FAIL"


def test_identification_checklist_requires_every_mandatory_item(client, tokens):
    payload = catalogue()
    mandatory = next(
        test["calculation_rules"]["mandatory_items"]
        for test in payload["tests"]
        if test["test_code"] == "T-CHK-ID"
    )
    complete = [{"observation_no": index + 1, "item_code": code, "conforms": True}
                for index, code in enumerate(mandatory)]
    assert _preview(client, tokens, "T-CHK-ID", {}, complete)["compliance"]["status"] == "PASS"

    incomplete = [dict(row) for row in complete]
    incomplete[-1]["conforms"] = False
    assert _preview(client, tokens, "T-CHK-ID", {}, incomplete)["compliance"]["status"] == "FAIL"
