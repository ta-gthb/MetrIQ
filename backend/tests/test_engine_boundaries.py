"""Boundary-value and determinism tests for the deterministic engine (PRD 11, 22).

Every MPE band edge is probed at the boundary itself and just either side of it,
because an off-by-one in `m = load / e` silently misclassifies a whole load
range. The same tests pin the arithmetic to exact decimal behaviour so a future
refactor cannot quietly introduce binary floating point.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.rules.loader import load_ruleset, load_test_catalogue
from app.services.calculation_engine.engine import evaluate
from app.services.calculation_engine.mpe import MpeResolutionError, resolve_mpe
from app.services.calculation_engine.types import CalcContext, ObservationRow
from app.utils.decimals import InvalidDecimalValue, decimal_str, quantize, to_decimal

RULESET = load_ruleset("r76-1-2006-v1")
_CATALOGUE = {entry["test_code"]: entry for entry in load_test_catalogue("r76-1-2006-v1")["tests"]}


def mpe(load, *, e="10", klass="III", stage="verification"):
    return resolve_mpe(ruleset=RULESET, instrument_class=klass, load=load, e=e, stage=stage)


def row(number: int, **values) -> ObservationRow:
    return ObservationRow(observation_no=number, **values)


def context(test_code: str, observations: list[ObservationRow], **instrument) -> CalcContext:
    base_instrument = {
        "instrument_class": "III",
        "e": Decimal("10"),
        "d": Decimal("10"),
        "unit": "g",
        "max_capacity": Decimal("30000"),
    }
    base_instrument.update(instrument)
    return CalcContext(
        test_code=test_code,
        instrument=base_instrument,
        observations=observations,
        ruleset=RULESET,
        test_definition=_CATALOGUE[test_code],
        rule_version="r76-1-2006-v1",
    )


# ---------------------------------------------------------------------------
# MPE band resolution: class III, e = 10 g -> band edges at m = 500 and m = 2000
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("load", "expected_factor", "expected_index", "expected_mpe"),
    [
        ("0", "0.5", 0, "5"),          # zero load sits in the lowest band
        ("-5000", "0.5", 0, "5"),      # sign is ignored via abs(load)
        ("4999", "0.5", 0, "5"),       # boundary - epsilon
        ("5000", "0.5", 0, "5"),       # boundary: m = 500 belongs to the lower band
        ("5001", "1.0", 1, "10"),      # boundary + epsilon
        ("19999", "1.0", 1, "10"),
        ("20000", "1.0", 1, "10"),     # boundary: m = 2000 belongs to the middle band
        ("20001", "1.5", 2, "15"),     # boundary + epsilon
        ("30000", "1.5", 2, "15"),
        ("300000", "1.5", 2, "15"),    # open-ended top band
    ],
)
def test_class_iii_band_boundaries(load, expected_factor, expected_index, expected_mpe):
    resolution = mpe(load)
    assert resolution.band_index == expected_index
    assert resolution.factor == Decimal(expected_factor)
    assert resolution.mpe_value == Decimal(expected_mpe)
    assert resolution.unit == "e"
    assert resolution.m == abs(Decimal(load)) / Decimal(10)


@pytest.mark.parametrize(
    ("klass", "load", "expected_index"),
    [
        ("I", "500000", 0),      # m = 50 000 exactly
        ("I", "500010", 1),
        ("II", "50000", 0),      # m = 5 000 exactly
        ("II", "50010", 1),
        ("IIII", "500", 0),      # m = 50 exactly
        ("IIII", "510", 1),
        ("IIII", "2000", 1),     # m = 200 exactly
        ("IIII", "2010", 2),
    ],
)
def test_other_class_band_boundaries(klass, load, expected_index):
    assert mpe(load, klass=klass).band_index == expected_index


def test_roman_numeral_and_arabic_class_aliases_agree():
    for roman, arabic in (("I", "1"), ("II", "2"), ("III", "3"), ("IIII", "4")):
        assert mpe("100", klass=roman).mpe_value == mpe("100", klass=arabic).mpe_value


def test_in_service_stage_uses_the_wider_tolerance_table():
    assert mpe("5000").mpe_value == Decimal("5")
    assert mpe("5000", stage="in_service").mpe_value == Decimal("10")


def test_mpe_is_scaled_by_the_verification_scale_interval():
    assert mpe("5000", e="10").mpe_value == Decimal("5")
    assert mpe("5000", e="20").mpe_value == Decimal("10")


@pytest.mark.parametrize("bad_e", ["0", "-10", None])
def test_mpe_rejects_a_non_positive_or_missing_e(bad_e):
    with pytest.raises(InvalidDecimalValue):
        mpe("5000", e=bad_e)


def test_mpe_requires_a_load():
    with pytest.raises(InvalidDecimalValue):
        mpe(None)


@pytest.mark.parametrize("klass", [None, "", "IX", "V"])
def test_mpe_rejects_an_unknown_or_missing_class(klass):
    with pytest.raises(MpeResolutionError):
        mpe("5000", klass=klass)


# ---------------------------------------------------------------------------
# Weighing performance: P = I + 0.5e - delta_L, E = P - L
# ---------------------------------------------------------------------------
def test_weighing_performance_passes_well_inside_the_mpe():
    outcome = evaluate(context("T-WP", [row(1, load=Decimal("10000"), indication=Decimal("10002"),
                                           additional_load=Decimal("5"))]))
    assert outcome.rows[0].error == Decimal("2")
    assert outcome.rows[0].mpe == Decimal("10")
    assert outcome.status == "PASS"
    assert outcome.margin == Decimal("8")


def test_weighing_performance_at_the_exact_limit_passes():
    """A boundary value equal to the MPE conforms (<=, not <)."""
    outcome = evaluate(context("T-WP", [row(1, load=Decimal("10000"), indication=Decimal("10005"))]))
    assert outcome.rows[0].error == Decimal("10")
    assert outcome.margin == Decimal("0")
    assert outcome.status == "PASS"


def test_weighing_performance_one_resolution_over_the_limit_fails():
    outcome = evaluate(context("T-WP", [row(1, load=Decimal("10000"), indication=Decimal("10006"))]))
    assert outcome.rows[0].error == Decimal("11")
    assert outcome.margin == Decimal("-1")
    assert outcome.status == "FAIL"


def test_weighing_performance_negative_error_uses_absolute_value():
    outcome = evaluate(context("T-WP", [row(1, load=Decimal("10000"), indication=Decimal("9985"))]))
    assert outcome.rows[0].error == Decimal("-10")
    assert outcome.status == "PASS"


def test_weighing_performance_applies_the_zero_error_correction():
    outcome = evaluate(
        context(
            "T-WP",
            [
                row(1, load=Decimal("0"), indication=Decimal("1"), additional_load=Decimal("5")),
                row(2, load=Decimal("10000"), indication=Decimal("10007"), additional_load=Decimal("5")),
            ],
        )
    )
    # E(0) = 1 + 5 - 5 - 0 = 1 ; E(10 kg) = 10007 + 5 - 5 - 10000 = 7 ; E_c = 6
    assert outcome.intermediates["zero_error"] == Decimal("1")
    assert outcome.rows[1].corrected_error == Decimal("6")
    assert outcome.status == "PASS"
    assert outcome.measured_value == Decimal("6")


def test_weighing_performance_governing_row_is_the_worst_error():
    outcome = evaluate(
        context(
            "T-WP",
            [
                row(1, load=Decimal("1000"), indication=Decimal("1001")),
                row(2, load=Decimal("25000"), indication=Decimal("25030")),
            ],
        )
    )
    # Row 1: MPE at m = 100 is 0.5e = 5 -> error 1 + 5 - 1000 = ... see below.
    assert outcome.intermediates["governing_row"] == 2
    assert outcome.measured_value == Decimal("35")
    assert outcome.status == "FAIL"


def test_weighing_performance_uses_the_lower_band_mpe_near_zero():
    outcome = evaluate(context("T-WP", [row(1, load=Decimal("1000"), indication=Decimal("1006"))]))
    assert outcome.rows[0].mpe == Decimal("5")
    assert outcome.rows[0].error == Decimal("11")
    assert outcome.status == "FAIL"


def test_weighing_performance_reports_a_missing_indication_as_incomplete():
    outcome = evaluate(context("T-WP", [row(1, load=Decimal("1000"))]))
    assert outcome.is_valid is False
    assert outcome.status == "INCOMPLETE"
    assert "indication" in outcome.errors[0]


def test_weighing_performance_warns_when_the_additional_load_is_assumed_zero():
    outcome = evaluate(context("T-WP", [row(1, load=Decimal("1000"), indication=Decimal("1001"))]))
    assert outcome.warnings
    assert "Additional load" in outcome.warnings[0]


def test_weighing_performance_converts_observation_units():
    outcome = evaluate(
        context(
            "T-WP",
            [row(1, load=Decimal("10"), indication=Decimal("10002"), unit="kg")],
        )
    )
    assert outcome.rows[0].load == Decimal("10000")
    assert outcome.status == "PASS"


def test_weighing_performance_rejects_an_incompatible_unit():
    outcome = evaluate(context("T-WP", [row(1, load=Decimal("1"), indication=Decimal("1"), unit="s")]))
    assert outcome.is_valid is False
    assert outcome.status == "INVALID"


def test_multi_range_instrument_e_is_a_required_input():
    outcome = evaluate(context("T-WP", [row(1, load=Decimal("1000"), indication=Decimal("1001"))], e=None))
    assert outcome.status == "INCOMPLETE"


# ---------------------------------------------------------------------------
# Repeatability, eccentricity, zero return and checklists
# ---------------------------------------------------------------------------
def test_repeatability_is_the_spread_of_errors_and_passes_at_the_limit():
    # Errors: 0, +10, -10 -> spread 20. MPE at 15 kg (m = 1500) is 1.0e = 10 g,
    # and the repeatability tolerance is the MPE itself.
    outcome = evaluate(
        context(
            "T-REP",
            [
                row(1, load=Decimal("15000"), indication=Decimal("15000")),
                row(2, load=Decimal("15000"), indication=Decimal("15005")),
                row(3, load=Decimal("15000"), indication=Decimal("14995")),
            ],
        )
    )
    assert outcome.measured_value == Decimal("10")
    assert outcome.status == "PASS"


def test_repeatability_fails_when_the_spread_exceeds_the_mpe():
    outcome = evaluate(
        context(
            "T-REP",
            [
                row(1, load=Decimal("15000"), indication=Decimal("15000")),
                row(2, load=Decimal("15000"), indication=Decimal("15012")),
            ],
        )
    )
    assert outcome.measured_value == Decimal("12")
    assert outcome.status == "FAIL"


def test_repeatability_needs_at_least_two_rows():
    outcome = evaluate(context("T-REP", [row(1, load=Decimal("15000"), indication=Decimal("15000"))]))
    assert outcome.status == "INCOMPLETE"


def test_eccentricity_uses_the_centre_reading_as_reference():
    outcome = evaluate(
        context(
            "T-ECC",
            [
                row(1, label="centre", load=Decimal("15000"), indication=Decimal("15000")),
                row(2, label="front-left", load=Decimal("15000"), indication=Decimal("15004")),
                row(3, label="rear-right", load=Decimal("15000"), indication=Decimal("14996")),
            ],
        )
    )
    assert outcome.measured_value == Decimal("4")
    assert outcome.status == "PASS"


def test_zero_return_at_the_exact_limit_passes():
    # Tolerance is 0.5e = 5 g; a 5 g residual deviation still conforms.
    outcome = evaluate(
        context("T-ZR", [row(1, label="initial", value=Decimal("0")),
                         row(2, label="after unloading", value=Decimal("5"))])
    )
    assert outcome.measured_value == Decimal("5")
    assert outcome.limit_value == Decimal("5")
    assert outcome.status == "PASS"


def test_zero_return_one_gram_over_the_limit_fails():
    outcome = evaluate(
        context("T-ZR", [row(1, label="initial", value=Decimal("0")),
                         row(2, label="after unloading", value=Decimal("6"))])
    )
    assert outcome.status == "FAIL"


def test_discrimination_requires_the_indication_to_change_by_at_least_one_d():
    outcome = evaluate(context("T-DISC", [row(1, value=Decimal("10"))]))
    assert outcome.comparator == "gte"
    assert outcome.limit_value == Decimal("10")
    assert outcome.status == "PASS"

    outcome = evaluate(context("T-DISC", [row(1, value=Decimal("9"))]))
    assert outcome.status == "FAIL"


def test_conformity_checklist_requires_every_mandatory_item():
    items = ["CON-01", "CON-02", "CON-03", "CON-04", "CON-06", "CON-07"]
    complete = [row(index, item_code=code, conforms=True) for index, code in enumerate(items, 1)]
    assert evaluate(context("T-CHK-CON", complete)).status == "PASS"

    with_gap = complete[:-1]
    assert evaluate(context("T-CHK-CON", with_gap)).status == "INCOMPLETE"

    with_failure = [*complete[:-1], row(6, item_code="CON-07", conforms=False)]
    assert evaluate(context("T-CHK-CON", with_failure)).status == "FAIL"


# ---------------------------------------------------------------------------
# Validation rules: negative values and excessive precision
# ---------------------------------------------------------------------------
def test_negative_load_is_rejected_by_the_validation_rules():
    from app.services.calculation_engine.engine import validate_inputs

    problems = validate_inputs(context("T-WP", [row(1, load=Decimal("-1"), indication=Decimal("1"))]))
    assert any("must not be negative" in problem for problem in problems)


def test_the_engine_returns_a_stable_result_for_the_same_input():
    observations = [
        row(1, load=Decimal("10000"), indication=Decimal("10002")),
        row(2, load=Decimal("20000"), indication=Decimal("20004")),
    ]
    first = evaluate(context("T-WP", observations)).as_dict()
    second = evaluate(context("T-WP", observations)).as_dict()
    assert first == second


# ---------------------------------------------------------------------------
# Decimal utilities
# ---------------------------------------------------------------------------
def test_decimal_arithmetic_is_exact():
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
    assert to_decimal("0.1") + to_decimal("0.2") == Decimal("0.3")


@pytest.mark.parametrize("value", [True, "abc", "12abc", object()])
def test_to_decimal_rejects_unusable_input(value):
    with pytest.raises(InvalidDecimalValue):
        to_decimal(value)


def test_decimal_str_never_uses_scientific_notation():
    assert decimal_str(Decimal("1E+3")) == "1000"
    assert decimal_str(Decimal("1E-7")) == "0.0000001"
    assert decimal_str(Decimal("0.000")) == "0"
    assert decimal_str(None) is None


def test_decimal_str_accepts_strings_from_json_and_sqlite():
    assert decimal_str("30000") == "30000"
    assert decimal_str("0.10") == "0.1"


def test_quantize_rounds_half_up_at_the_requested_resolution():
    assert quantize(Decimal("2.5"), Decimal("1")) == Decimal("3")
    assert quantize(Decimal("-2.5"), Decimal("1")) == Decimal("-3")
    assert quantize(Decimal("0.05"), Decimal("0.1")) == Decimal("0.1")
