"""Per-test deterministic calculators (PRD 10.2, 10.3, 11).

Every calculator returns a :class:`CalcOutcome` carrying the measured value, the
applicable limit, the margin and the full intermediate trace. Compliance status
is resolved by the compliance engine from these values plus the configured
comparator, so PASS/FAIL is never typed by a user (PRD 27.2).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable

from app.services.calculation_engine.errors import (
    MissingInputError,
    ValidationError,
    corrected_error,
    error_from_indication,
)
from app.services.calculation_engine.mpe import MpeResolution, resolve_mpe
from app.services.calculation_engine.types import CalcContext, CalcOutcome, ObservationRow, RowResult
from app.utils.decimals import ROUNDING_POLICY_EXACT, ROUNDING_POLICY_R76, quantize, to_decimal
from app.utils.units import convert_mass, is_mass_unit, normalise_unit

ZERO_LABELS = {"initial", "zero", "start", "before", "no_load", "unloaded"}
CENTRE_LABELS = {"centre", "center", "middle", "c", "0", "central"}


def _require_e(ctx: CalcContext) -> Decimal:
    if ctx.e is None or ctx.e <= 0:
        raise MissingInputError("verification scale interval (e) is required for this test")
    return ctx.e


def _same_unit(left: str, right: str) -> bool:
    try:
        return normalise_unit(left) == normalise_unit(right)
    except Exception:
        return (left or "").strip().lower() == (right or "").strip().lower()


def _load_in_instrument_unit(ctx: CalcContext, obs: ObservationRow, field: str = "load") -> Decimal:
    value = to_decimal(getattr(obs, field), field=f"{field} (row {obs.observation_no})")
    if value is None:
        raise MissingInputError(f"{field} is required on observation row {obs.observation_no}")
    if obs.unit and not _same_unit(obs.unit, ctx.unit):
        if is_mass_unit(obs.unit) and is_mass_unit(ctx.unit):
            value = convert_mass(value, obs.unit, ctx.unit)
        else:
            raise ValidationError(
                f"row {obs.observation_no}: unit '{obs.unit}' cannot be converted to '{ctx.unit}'"
            )
    return value


def _maybe_round(ctx: CalcContext, value: Decimal) -> Decimal:
    if ctx.calc_rule("round_to_resolution", False):
        return quantize(value, ctx.resolution)
    return value


def _rounding_policy(ctx: CalcContext) -> str:
    return ROUNDING_POLICY_R76 if ctx.calc_rule("round_to_resolution", False) else ROUNDING_POLICY_EXACT


def _tolerance(
    ctx: CalcContext, key: str, load: Decimal | None = None
) -> tuple[Decimal | None, dict[str, Any]]:
    """Resolve a tolerance, using the scale interval of the range the load is in.

    ``load`` is optional: when it is supplied on a multi-range or
    multi-interval instrument the ``e``-based tolerance is expressed in the
    interval the observation was taken in (``CalcContext.e_for``).
    """
    tol = ctx.tolerance(key)
    if not tol:
        return None, {}
    factor = to_decimal(tol.get("factor"), field=f"tolerances.{key}.factor")
    if factor is None:
        return None, tol
    unit = tol.get("unit", "e")
    if unit == "e":
        limit = factor * (ctx.e_for(load) or _require_e(ctx))
    elif unit == "d":
        resolution = ctx.d if ctx.d and ctx.d > 0 else (ctx.e_for(load) or _require_e(ctx))
        limit = factor * resolution
    else:
        limit = factor
    return limit, tol


def _finalise(
    ctx: CalcContext,
    outcome: CalcOutcome,
    *,
    measured: Decimal,
    limit: Decimal,
    comparator: str,
    rule_id: str | None = None,
    rule_version: str | None = None,
    clause: str | None = None,
    unit: str | None = None,
) -> CalcOutcome:
    outcome.measured_value = _maybe_round(ctx, measured)
    outcome.limit_value = limit
    outcome.comparator = comparator
    if comparator == "gte":
        passed = measured >= limit
        outcome.margin = measured - limit
    elif comparator == "lte":
        passed = measured <= limit
        outcome.margin = limit - measured
    elif comparator == "eq":
        passed = measured == limit
        outcome.margin = limit - measured
    else:
        passed = abs(measured) <= limit
        outcome.margin = limit - abs(measured)
    outcome.status = "PASS" if passed else "FAIL"
    outcome.unit = unit if unit is not None else ctx.unit
    outcome.rule_id = rule_id or outcome.rule_id
    outcome.rule_version = rule_version or ctx.rule_version
    outcome.clause_reference = clause or outcome.clause_reference
    outcome.rounding_policy = _rounding_policy(ctx)
    return outcome


def _mpe_for(ctx: CalcContext, load: Decimal) -> MpeResolution:
    return resolve_mpe(
        ruleset=ctx.ruleset,
        instrument_class=ctx.instrument_class,
        load=load,
        e=ctx.e_for(load) or _require_e(ctx),
        stage=ctx.stage,
        rule_version=ctx.rule_version,
    )


# ---------------------------------------------------------------------------
# Weighing performance
# ---------------------------------------------------------------------------
def calc_weighing_performance(ctx: CalcContext) -> CalcOutcome:
    _require_e(ctx)
    if not ctx.observations:
        raise MissingInputError("at least one observation row is required")

    require_dl = bool(ctx.calc_rule("require_additional_load", False))
    apply_zero = bool(ctx.calc_rule("apply_zero_correction", True))

    computations = []
    for obs in ctx.observations:
        load = _load_in_instrument_unit(ctx, obs)
        comp = error_from_indication(
            indication=obs.indication,
            load=load,
            e=ctx.e,
            additional_load=obs.additional_load,
            require_additional_load=require_dl,
        )
        computations.append((obs, load, comp))

    zero_error: Decimal | None = None
    if apply_zero:
        for _obs, load, comp in computations:
            if load == 0:
                zero_error = comp.E
                break

    outcome = CalcOutcome()
    worst: RowResult | None = None
    assumed_any = False
    for obs, load, comp in computations:
        resolution = _mpe_for(ctx, load)
        reference = zero_error if (apply_zero and load != 0) else None
        rounded_error = _maybe_round(ctx, corrected_error(comp.E, reference))
        margin = resolution.mpe_value - abs(rounded_error)
        row = RowResult(
            observation_no=obs.observation_no,
            label=obs.label,
            load=load,
            indication=comp.indication,
            additional_load=comp.additional_load,
            error=rounded_error,
            reference_error=reference,
            corrected_error=rounded_error,
            mpe=resolution.mpe_value,
            margin=margin,
            within=margin >= 0,
            detail={
                **comp.as_dict(),
                "range_no": obs.range_no,
                "rule_id": resolution.rule_id,
                "band": resolution.band_label,
                "m_over_e": resolution.m,
                "clause_reference": resolution.clause_reference,
            },
        )
        outcome.rows.append(row)
        assumed_any = assumed_any or comp.assumed_additional_load
        if worst is None or abs(rounded_error) > abs(worst.error or Decimal(0)):
            worst = row

    if assumed_any:
        outcome.warnings.append(
            "Additional load to the next changeover point was not supplied for at least one row; "
            "delta_L was taken as zero. For type evaluation R 76-2 normally requires it."
        )
    assert worst is not None
    outcome.intermediates = {
        "method": "P = I + 0.5e - delta_L ; E = P - L ; E_c = E - E_0",
        "zero_error": zero_error,
        "error_at_zero_applied": zero_error is not None,
        "governing_row": worst.observation_no,
        "max_abs_error": abs(worst.error or Decimal(0)),
        "row_count": len(outcome.rows),
    }
    outcome.explanation = (
        f"Governing error {worst.error} {ctx.unit} at row {worst.observation_no} "
        f"against MPE +/-{worst.mpe} {ctx.unit}."
    )
    return _finalise(
        ctx, outcome,
        measured=abs(worst.error or Decimal(0)),
        limit=worst.mpe or Decimal(0),
        comparator="abs_lte",
        rule_id=worst.detail.get("rule_id"),
        clause=worst.detail.get("clause_reference"),
        unit=ctx.unit,
    )


# ---------------------------------------------------------------------------
# Repeatability
# ---------------------------------------------------------------------------
def calc_repeatability(ctx: CalcContext) -> CalcOutcome:
    _require_e(ctx)
    if len(ctx.observations) < 2:
        raise MissingInputError("repeatability requires at least two observations of the same load")

    require_dl = bool(ctx.calc_rule("require_additional_load", False))
    groups: dict[str, list[tuple[ObservationRow, Decimal, Decimal]]] = {}
    for obs in ctx.observations:
        load = _load_in_instrument_unit(ctx, obs)
        comp = error_from_indication(
            indication=obs.indication,
            load=load,
            e=ctx.e,
            additional_load=obs.additional_load,
            require_additional_load=require_dl,
        )
        groups.setdefault(str(load), []).append((obs, load, comp.E))

    outcome = CalcOutcome()
    worst_spread: Decimal | None = None
    worst_load: Decimal | None = None
    worst_limit: Decimal | None = None
    worst_rule_id: str | None = None
    worst_clause: str | None = None

    for load_key, entries in groups.items():
        errors = [error for _obs, _load, error in entries]
        spread = max(errors) - min(errors)
        resolution = _mpe_for(ctx, Decimal(load_key))
        for obs, load, error in entries:
            rounded = _maybe_round(ctx, error)
            outcome.rows.append(
                RowResult(
                    observation_no=obs.observation_no,
                    label=obs.label,
                    load=load,
                    indication=obs.indication,
                    additional_load=obs.additional_load,
                    error=rounded,
                    mpe=resolution.mpe_value,
                    margin=resolution.mpe_value - abs(rounded),
                    within=abs(rounded) <= resolution.mpe_value,
                    detail={
                        "group": load_key,
                        "group_size": len(entries),
                        "group_min_error": min(errors),
                        "group_max_error": max(errors),
                        "group_spread": spread,
                        "rule_id": resolution.rule_id,
                    },
                )
            )
        if worst_spread is None or spread > worst_spread:
            worst_spread, worst_load = spread, Decimal(load_key)
            worst_limit, worst_rule_id = resolution.mpe_value, resolution.rule_id
            worst_clause = resolution.clause_reference

    assert worst_spread is not None and worst_load is not None
    outcome.intermediates = {
        "method": "spread = max(E) - min(E) for repeated weighings of the same load",
        "groups": {
            key: {
                "count": len(items),
                "min": min(error for _o, _l, error in items),
                "max": max(error for _o, _l, error in items),
            }
            for key, items in groups.items()
        },
        "governing_load": worst_load,
        "governing_spread": worst_spread,
        "runs": len(ctx.observations),
    }
    outcome.explanation = (
        f"Largest spread {worst_spread} {ctx.unit} at load {worst_load} {ctx.unit} "
        f"against MPE +/-{worst_limit} {ctx.unit}."
    )
    return _finalise(
        ctx, outcome,
        measured=worst_spread,
        limit=worst_limit or Decimal(0),
        comparator="abs_lte",
        rule_id=worst_rule_id,
        clause=worst_clause,
        unit=ctx.unit,
    )


# ---------------------------------------------------------------------------
# Eccentricity
# ---------------------------------------------------------------------------
def calc_eccentricity(ctx: CalcContext) -> CalcOutcome:
    _require_e(ctx)
    if len(ctx.observations) < 2:
        raise MissingInputError(
            "eccentricity requires observations at the centre and at each eccentric position"
        )

    require_dl = bool(ctx.calc_rule("require_additional_load", False))
    configured_reference = ctx.calc_rule("reference_position")
    entries = []
    for obs in ctx.observations:
        load = _load_in_instrument_unit(ctx, obs)
        comp = error_from_indication(
            indication=obs.indication,
            load=load,
            e=ctx.e,
            additional_load=obs.additional_load,
            require_additional_load=require_dl,
        )
        entries.append((obs, load, comp.E))

    reference_error = None
    reference_label = None
    for obs, _load, error in entries:
        label = (obs.label or obs.item_code or "").strip().lower()
        if configured_reference and label == str(configured_reference).strip().lower():
            reference_error, reference_label = error, obs.label
            break
        if label in CENTRE_LABELS:
            reference_error, reference_label = error, obs.label
            break
    assumed_reference = reference_error is None
    if assumed_reference:
        reference_error = entries[0][2]
        reference_label = entries[0][0].label or "observation 1"

    outcome = CalcOutcome()
    worst: RowResult | None = None
    for obs, load, error in entries:
        resolution = _mpe_for(ctx, load)
        absolute_margin = resolution.mpe_value - abs(error)
        difference = error - reference_error
        difference_margin = resolution.mpe_value - abs(difference)
        row = RowResult(
            observation_no=obs.observation_no,
            label=obs.label,
            load=load,
            indication=obs.indication,
            additional_load=obs.additional_load,
            error=error,
            reference_error=reference_error,
            corrected_error=difference,
            mpe=resolution.mpe_value,
            margin=difference_margin,
            within=absolute_margin >= 0 and difference_margin >= 0,
            detail={
                "position": obs.label,
                "error": error,
                "difference_from_reference": difference,
                "absolute_margin": absolute_margin,
                "difference_margin": difference_margin,
                "rule_id": resolution.rule_id,
            },
        )
        outcome.rows.append(row)
        if worst is None or abs(difference) > abs(worst.corrected_error or Decimal(0)):
            worst = row

    assert worst is not None
    outcome.intermediates = {
        "method": "difference between the error at each eccentric position and the reference position",
        "reference_position": reference_label,
        "reference_assumed": assumed_reference,
        "reference_error": reference_error,
        "governing_row": worst.observation_no,
        "positions": len(outcome.rows),
        "absolute_check": "each |E| must also remain within MPE",
    }
    if assumed_reference:
        outcome.warnings.append(
            "No centre position could be identified; the first observation was used as the reference."
        )
    outcome.explanation = (
        f"Largest eccentricity difference {worst.corrected_error} {ctx.unit} at "
        f"{worst.label or ('row ' + str(worst.observation_no))} against MPE +/-{worst.mpe} {ctx.unit}."
    )
    return _finalise(
        ctx, outcome,
        measured=abs(worst.corrected_error or Decimal(0)),
        limit=worst.mpe or Decimal(0),
        comparator="abs_lte",
        rule_id=(worst.detail or {}).get("rule_id"),
        clause=(worst.detail or {}).get("clause_reference"),
        unit=ctx.unit,
    )


# ---------------------------------------------------------------------------
# Generic deviation calculators (zero return, creep, temperature, stability)
# ---------------------------------------------------------------------------
def _deviation_calculator(tolerance_key: str, method: str, minimum_rows: int = 2):
    def calculator(ctx: CalcContext) -> CalcOutcome:
        reference_load = next((obs.load for obs in ctx.observations if obs.load is not None), None)
        limit, tol = _tolerance(ctx, tolerance_key, reference_load)
        if limit is None:
            raise ValidationError(
                f"ruleset is missing tolerance '{tolerance_key}'; cannot evaluate this test"
            )
        values: list[tuple[ObservationRow, Decimal]] = []
        for obs in ctx.observations:
            raw = obs.value if obs.value is not None else obs.indication
            number = to_decimal(raw, field=f"value (row {obs.observation_no})")
            if number is None:
                raise MissingInputError(
                    f"indication value is required on observation row {obs.observation_no}"
                )
            values.append((obs, number))
        if len(values) < minimum_rows:
            raise MissingInputError(
                f"{tolerance_key} requires at least {minimum_rows} observation rows"
            )

        outcome = CalcOutcome()
        reference_obs, reference_value = values[0]
        for obs, value in values:
            deviation = value - reference_value
            outcome.rows.append(
                RowResult(
                    observation_no=obs.observation_no,
                    label=obs.label,
                    value=value,
                    error=deviation,
                    mpe=limit,
                    margin=limit - abs(deviation),
                    within=abs(deviation) <= limit,
                    detail={
                        "reference_value": reference_value,
                        "deviation": deviation,
                        "range_no": obs.range_no,
                        "elapsed_seconds": obs.elapsed_seconds,
                        "temperature_c": obs.temperature_c,
                        "clause_reference": tol.get("clause_reference"),
                    },
                )
            )
        worst = max(outcome.rows, key=lambda row: abs(row.error or Decimal(0)))
        outcome.intermediates = {
            "method": method,
            "reference_row": reference_obs.observation_no,
            "reference_value": reference_value,
            "governing_row": worst.observation_no,
            "max_abs_deviation": worst.error,
            "tolerance": tol,
            "comparator": tol.get("comparator", "abs_lte"),
        }
        outcome.explanation = (
            f"Maximum deviation {worst.error} {ctx.unit} against limit {limit} {ctx.unit} "
            f"({tol.get('clause_reference', '')})."
        )
        return _finalise(
            ctx, outcome,
            measured=abs(worst.error or Decimal(0)),
            limit=limit,
            comparator=tol.get("comparator", "abs_lte"),
            rule_id=tol.get("rule_id", f"R76-{tolerance_key.upper()}"),
            clause=tol.get("clause_reference"),
            unit=ctx.unit,
        )

    return calculator


# ---------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------
def calc_sensitivity(ctx: CalcContext) -> CalcOutcome:
    reference_reload = next((obs.load for obs in ctx.observations if obs.load is not None), None)
    limit, tol = _tolerance(ctx, "sensitivity", reference_reload)
    if limit is None:
        raise ValidationError("ruleset is missing tolerance 'sensitivity'")
    if len(ctx.observations) < 2:
        raise MissingInputError("sensitivity requires an initial and a final observation")

    entries = []
    for obs in ctx.observations:
        load = _load_in_instrument_unit(ctx, obs)
        value = to_decimal(obs.value if obs.value is not None else obs.indication, field="indication")
        if value is None:
            raise MissingInputError(f"indication is required on row {obs.observation_no}")
        entries.append((obs, load, value))
        outcome_row = RowResult(
            observation_no=obs.observation_no,
            label=obs.label,
            load=load,
            value=value,
            detail={"load": load, "indication": value},
        )
        if len(entries) == 1:
            outcome = CalcOutcome(rows=[outcome_row])
        else:
            outcome.rows.append(outcome_row)

    first, last = entries[0], entries[-1]
    load_change = last[1] - first[1]
    indication_change = last[2] - first[2]
    if load_change == 0:
        raise ValidationError("sensitivity requires two different applied loads")
    response_error = indication_change - load_change
    margin = limit - abs(response_error)
    outcome.rows[-1].error = response_error
    outcome.rows[-1].mpe = limit
    outcome.rows[-1].margin = margin
    outcome.rows[-1].within = margin >= 0
    outcome.intermediates = {
        "method": "response_error = (I2 - I1) - (L2 - L1)",
        "load_change": load_change,
        "indication_change": indication_change,
        "response_error": response_error,
        "tolerance": tol,
    }
    outcome.explanation = (
        f"Indication changed by {indication_change} {ctx.unit} for a load change of "
        f"{load_change} {ctx.unit}; response error {response_error} {ctx.unit} against limit "
        f"{limit} {ctx.unit}."
    )
    return _finalise(
        ctx, outcome,
        measured=abs(response_error),
        limit=limit,
        comparator=tol.get("comparator", "abs_lte"),
        rule_id=tol.get("rule_id", "R76-SENS"),
        clause=tol.get("clause_reference"),
        unit=ctx.unit,
    )


# ---------------------------------------------------------------------------
# Discrimination (changeover)
# ---------------------------------------------------------------------------
def calc_discrimination(ctx: CalcContext) -> CalcOutcome:
    reference_load = next((obs.load for obs in ctx.observations if obs.load is not None), None)
    limit, tol = _tolerance(ctx, "discrimination", reference_load)
    if limit is None:
        raise ValidationError("ruleset is missing tolerance 'discrimination'")
    if not ctx.observations:
        raise MissingInputError("discrimination requires at least one observation")

    outcome = CalcOutcome()
    smallest_change: Decimal | None = None
    for obs in ctx.observations:
        change = to_decimal(
            obs.value if obs.value is not None else obs.indication, field="indication change"
        )
        if change is None:
            raise MissingInputError(
                f"indication change is required on observation row {obs.observation_no}"
            )
        additional = to_decimal(obs.load, field="applied load")
        outcome.rows.append(
            RowResult(
                observation_no=obs.observation_no,
                label=obs.label,
                load=additional,
                value=change,
                mpe=limit,
                margin=change - limit,
                within=change >= limit,
                detail={"applied_load": additional, "indication_change": change},
            )
        )
        if smallest_change is None or change < smallest_change:
            smallest_change = change

    assert smallest_change is not None
    outcome.intermediates = {
        "method": "indication shall change by at least the configured discrimination threshold",
        "minimum_change": smallest_change,
        "threshold": limit,
        "tolerance": tol,
    }
    outcome.explanation = (
        f"Smallest indication change {smallest_change} {ctx.unit} against the required minimum "
        f"{limit} {ctx.unit}."
    )
    return _finalise(
        ctx, outcome,
        measured=smallest_change,
        limit=limit,
        comparator="gte",
        rule_id=tol.get("rule_id", "R76-DISC"),
        clause=tol.get("clause_reference"),
        unit=ctx.unit,
    )


# ---------------------------------------------------------------------------
# Checklist tests (construction, identification / markings)
# ---------------------------------------------------------------------------
def _checklist_calculator():
    def calculator(ctx: CalcContext) -> CalcOutcome:
        mandatory = list(ctx.calc_rule("mandatory_items", []) or [])
        if not ctx.observations:
            raise MissingInputError("at least one checklist row is required")

        outcome = CalcOutcome()
        seen: dict[str, bool] = {}
        non_conforming: list[str] = []
        for obs in ctx.observations:
            code = (obs.item_code or obs.label or f"row-{obs.observation_no}").strip()
            if obs.conforms is None:
                raise MissingInputError(f"checklist item '{code}' has no conformity decision")
            seen[code] = bool(obs.conforms)
            if not obs.conforms:
                non_conforming.append(code)
            outcome.rows.append(
                RowResult(
                    observation_no=obs.observation_no,
                    label=code,
                    within=bool(obs.conforms),
                    detail={
                        "item_code": code,
                        "conforms": bool(obs.conforms),
                        "remarks": obs.remarks,
                        "mandatory": code in mandatory,
                    },
                )
            )

        missing = [item for item in mandatory if item not in seen]
        if missing:
            raise MissingInputError(
                "checklist is incomplete; missing mandatory item(s): " + ", ".join(missing)
            )

        outcome.intermediates = {
            "method": "every mandatory checklist item must conform",
            "items_checked": len(outcome.rows),
            "non_conforming": non_conforming,
            "mandatory_items": mandatory,
        }
        outcome.explanation = (
            "All checklist items conform."
            if not non_conforming
            else f"{len(non_conforming)} checklist item(s) do not conform: {', '.join(non_conforming)}."
        )
        return _finalise(
            ctx, outcome,
            measured=Decimal(len(non_conforming)),
            limit=Decimal(0),
            comparator="lte",
            rule_id=ctx.calc_rule("rule_id", "R76-CHECKLIST"),
            clause=ctx.calc_rule("clause_reference"),
            unit="",
        )

    return calculator


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
CALCULATORS: dict[str, Callable[[CalcContext], CalcOutcome]] = {
    "T-WP": calc_weighing_performance,
    "T-REP": calc_repeatability,
    "T-ECC": calc_eccentricity,
    "T-ZR": _deviation_calculator(
        "zero_return",
        "deviation of the zero indication after unloading relative to the initial zero reading",
    ),
    "T-CREEP": _deviation_calculator(
        "creep", "change in indication over the observation period under constant load"
    ),
    "T-TEMP-NL": _deviation_calculator(
        "temperature_no_load", "variation of the no-load indication over the temperature range"
    ),
    "T-SENS": calc_sensitivity,
    "T-DISC": calc_discrimination,
    "T-STAB": _deviation_calculator(
        "stability", "variation of the indication during the stability observation period"
    ),
    "T-CHK-CON": _checklist_calculator(),
    "T-CHK-ID": _checklist_calculator(),
}


def supported_test_codes() -> list[str]:
    return sorted(CALCULATORS)


def get_calculator(test_code: str) -> Callable[[CalcContext], CalcOutcome]:
    calculator = CALCULATORS.get(test_code)
    if calculator is None:
        raise ValidationError(f"no deterministic calculator is registered for test code '{test_code}'")
    return calculator
