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
from app.rules.expressions import evaluate as evaluate_expression
from app.services.calculation_engine.types import (
    CalcContext,
    CalcOutcome,
    ObservationRow,
    RowResult,
    find_stage,
)
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
    elif unit == "mpe":
        # Some procedures are judged against the MPE that applies to the load
        # rather than against a fixed multiple of e (the eccentricity test is
        # the one already in the baseline). `load` selects the interval.
        limit = factor * _mpe_for(ctx, load if load is not None else Decimal(0)).mpe_value
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
    require_all_rows: bool = False,
) -> CalcOutcome:
    """Resolve the outcome of a calculation from its governing row.

    ``require_all_rows`` makes the test FAIL when any reported row is outside
    its own limit, even if the governing row alone would have passed. A test
    result is only as good as its worst required observation (audit item 3).
    """
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
    if require_all_rows:
        failed = [row.observation_no for row in outcome.rows if row.within is False]
        if failed:
            passed = False
        outcome.intermediates["required_rows"] = len(outcome.rows)
        outcome.intermediates["failed_rows"] = failed
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
    zero_reference_rows: set[int] = set()
    assumed_any = False
    for obs, load, comp in computations:
        resolution = _mpe_for(ctx, load)
        reference = zero_error if (apply_zero and load != 0) else None
        is_zero_reference = apply_zero and zero_error is not None and load == 0
        if is_zero_reference:
            zero_reference_rows.add(obs.observation_no)
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
                "is_zero_reference": is_zero_reference,
            },
        )
        outcome.rows.append(row)
        assumed_any = assumed_any or comp.assumed_additional_load
        # The governing row is the one with the least margin against its own
        # MPE, not the largest absolute error. Across MPE bands a 16 g error at
        # a 15 g limit is a worse non-conformity than a 25 g error at a 45 g
        # limit, so the margin decides; the largest absolute error is still
        # reported for the record. The zero reading that establishes E0 is not a
        # test load: it is still checked by the all-rows gate, but it does not
        # set the reported value because its own error is subtracted from every
        # other row.
        if is_zero_reference:
            continue
        if worst is None or (
            row.margin if row.margin is not None else Decimal(0),
            -abs(rounded_error),
        ) < (
            worst.margin if worst.margin is not None else Decimal(0),
            -abs(worst.error or Decimal(0)),
        ):
            worst = row

    if assumed_any:
        outcome.warnings.append(
            "Additional load to the next changeover point was not supplied for at least one row; "
            "delta_L was taken as zero. For type evaluation R 76-2 normally requires it."
        )
    if worst is None:
        # Only a zero reading was recorded; it is the result in its own right.
        worst = next(
            (row for row in outcome.rows if row.observation_no in zero_reference_rows),
            outcome.rows[0],
        )
    outcome.intermediates = {
        "method": "P = I + 0.5e - delta_L ; E = P - L ; E_c = E - E_0",
        "zero_error": zero_error,
        "error_at_zero_applied": zero_error is not None,
        "zero_reference_rows": sorted(zero_reference_rows),
        "governing_row": worst.observation_no,
        "governing_margin": worst.margin,
        "max_abs_error": max(abs(row.error or Decimal(0)) for row in outcome.rows),
        "rows_outside_mpe": [row.observation_no for row in outcome.rows if row.within is False],
        "row_count": len(outcome.rows),
    }
    outcome.explanation = (
        f"Governing error {worst.error} {ctx.unit} at row {worst.observation_no} "
        f"against MPE +/-{worst.mpe} {ctx.unit} (margin {worst.margin} {ctx.unit})."
    )
    return _finalise(
        ctx, outcome,
        measured=abs(worst.error or Decimal(0)),
        limit=worst.mpe or Decimal(0),
        comparator="abs_lte",
        rule_id=worst.detail.get("rule_id"),
        clause=worst.detail.get("clause_reference"),
        unit=ctx.unit,
        require_all_rows=True,
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

    minimum_repetitions = int(ctx.calc_rule("min_repetitions", 2) or 2)
    for load_key, entries in groups.items():
        if len(entries) < minimum_repetitions:
            raise MissingInputError(
                f"load {load_key} {ctx.unit} was weighed {len(entries)} time(s);"
                f" repeatability requires at least {minimum_repetitions} repetitions of every load"
            )

    outcome = CalcOutcome()
    worst_spread: Decimal | None = None
    worst_load: Decimal | None = None
    worst_limit: Decimal | None = None
    worst_margin: Decimal | None = None
    worst_rule_id: str | None = None
    worst_clause: str | None = None

    for load_key, entries in groups.items():
        errors = [error for _obs, _load, error in entries]
        spread = max(errors) - min(errors)
        resolution = _mpe_for(ctx, Decimal(load_key))
        spread_margin = resolution.mpe_value - spread
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
                        "group_margin": spread_margin,
                        "rule_id": resolution.rule_id,
                    },
                )
            )
        # The governing group is the one whose spread has the least margin
        # against the MPE; every repetition and every spread must pass.
        if worst_margin is None or spread_margin < worst_margin:
            worst_spread, worst_load = spread, Decimal(load_key)
            worst_margin = spread_margin
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
        "governing_margin": worst_margin,
        "runs": len(ctx.observations),
        "minimum_repetitions": minimum_repetitions,
        "rule": "every repetition must lie within its MPE and the spread of every group must not exceed it",
    }
    outcome.explanation = (
        f"Largest spread {worst_spread} {ctx.unit} at load {worst_load} {ctx.unit} "
        f"against MPE +/-{worst_limit} {ctx.unit}; every repetition was also checked"
        f" against the MPE for its load."
    )
    return _finalise(
        ctx, outcome,
        measured=worst_spread,
        limit=worst_limit or Decimal(0),
        comparator="abs_lte",
        rule_id=worst_rule_id,
        clause=worst_clause,
        unit=ctx.unit,
        require_all_rows=True,
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
# Procedure helpers shared by the sequence tests
#
# The procedures below differ only in which reading is the reference of the
# method and in what else must have been recorded; the comparison itself (every
# reading against the reading the procedure designates, limited by the
# tolerance that applies to the load) is written once. Every one of them
# requires all of its rows to pass, not just the governing row.
# ---------------------------------------------------------------------------
TEMPERATURE_REFERENCE_C = Decimal(20)
ZERO_REFERENCE_PHRASES = ("initial", "zero", "start", "no load", "unloaded", "empty")


def _value_entries(ctx: CalcContext) -> list[tuple[ObservationRow, Decimal]]:
    """Every observation row as (row, reading), refusing a row without a reading."""
    entries: list[tuple[ObservationRow, Decimal]] = []
    for obs in ctx.observations:
        raw = obs.value if obs.value is not None else obs.indication
        number = to_decimal(raw, field=f"value (row {obs.observation_no})")
        if number is None:
            raise MissingInputError(
                f"indication value is required on observation row {obs.observation_no}"
            )
        entries.append((obs, number))
    return entries


def _stage_label(obs: ObservationRow) -> str:
    return (obs.label or obs.item_code or "").strip().lower()


def _match_stage(observations: list[ObservationRow], phrases: tuple[str, ...]) -> int | None:
    """Index of the first row whose stage label names one of ``phrases``."""
    return find_stage(observations, phrases)


def _resolve_reference(
    ctx: CalcContext,
    entries: list[tuple[ObservationRow, Decimal]],
    *,
    phrases: tuple[str, ...],
) -> tuple[int, list[str]]:
    """The reference row of a procedure, plus any assumption that was made."""
    hint = ctx.calc_rule("reference_row")
    if isinstance(hint, str) and hint.strip():
        hint = hint.strip().lower()
        if hint == "first":
            return 0, []
        # The catalogue names the reference stage as an identifier
        # ("before_disturbance"); the operator records it as a label
        # ("before disturbance"). Both spellings have to find the row.
        alternatives = (hint, hint.replace("_", " "), hint.replace("_", ""))
        index = _match_stage([obs for obs, _ in entries], alternatives)
        if index is not None:
            return index, []
    index = _match_stage([obs for obs, _ in entries], phrases)
    if index is not None:
        return index, []
    return 0, [
        "No reference reading could be identified by its stage label;"
        " the first observation was used as the reference."
    ]


def _procedure_tolerance_key(ctx: CalcContext, default: str) -> str:
    """The tolerance a procedure is judged against, as configured in the catalogue."""
    configured = ctx.calc_rule("tolerance_key")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    source = ctx.compliance_rule("limit_source")
    if isinstance(source, str) and source.startswith("tolerance:"):
        return source.split(":", 1)[1].strip()
    return default


def _tolerance_limit(ctx: CalcContext, key: str, load: Decimal | None):
    limit, tol = _tolerance(ctx, key, load)
    if limit is None:
        raise ValidationError(f"ruleset is missing tolerance '{key}'; cannot evaluate this test")
    return limit, tol


def _worst_row(rows: list[RowResult]) -> RowResult:
    """The row with the least margin against its own limit."""
    return min(
        rows,
        key=lambda row: (
            row.margin if row.margin is not None else Decimal(0),
            -(abs(row.error) if row.error is not None else Decimal(0)),
        ),
    )


def _pending_review_warning(tolerance_key: str, tol: dict[str, Any]) -> str | None:
    if tol.get("review_status") == "pending_domain_review":
        return (
            f"The limit for '{tolerance_key}' is awaiting metrology review"
            f" ({tol.get('clause_reference') or 'no clause reference recorded'});"
            " the verdict depends on that limit."
        )
    return None


def _sequence_outcome(
    ctx: CalcContext,
    *,
    tolerance_key: str,
    method: str,
    entries: list[tuple[ObservationRow, Decimal]],
    reference_index: int,
    reference_basis: str,
    extra_intermediates: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> CalcOutcome:
    """Compare every reading of a sequence with the reading that governs them."""
    if len(entries) < 2:
        raise MissingInputError(f"{tolerance_key} requires at least two observation rows")
    if reference_index >= len(entries) - 1:
        raise MissingInputError(
            "no observation after the reference reading of this procedure was recorded"
        )

    reference_obs, reference_value = entries[reference_index]
    outcome = CalcOutcome()
    outcome.warnings.extend(warnings or [])
    tol: dict[str, Any] = {}
    for obs, value in entries:
        limit, tol = _tolerance_limit(ctx, tolerance_key, obs.load)
        deviation = value - reference_value
        outcome.rows.append(
            RowResult(
                observation_no=obs.observation_no,
                label=obs.label,
                load=obs.load,
                value=value,
                error=deviation,
                reference_error=reference_value,
                corrected_error=deviation,
                mpe=limit,
                margin=limit - abs(deviation),
                within=abs(deviation) <= limit,
                detail={
                    "reference_value": reference_value,
                    "deviation": deviation,
                    "reference_basis": reference_basis,
                    "range_no": obs.range_no,
                    "elapsed_seconds": obs.elapsed_seconds,
                    "temperature_c": obs.temperature_c,
                    "rule_id": tol.get("rule_id"),
                    "clause_reference": tol.get("clause_reference"),
                },
            )
        )

    worst = _worst_row(outcome.rows)
    outcome.intermediates = {
        "method": method,
        "reference_row": reference_obs.observation_no,
        "reference_basis": reference_basis,
        "reference_value": reference_value,
        "governing_row": worst.observation_no,
        "governing_deviation": worst.error,
        "max_abs_deviation": max(abs(row.error or Decimal(0)) for row in outcome.rows),
        "rows_outside_limit": [row.observation_no for row in outcome.rows if row.within is False],
        "tolerance": tol,
        "comparator": tol.get("comparator", "abs_lte"),
        **(extra_intermediates or {}),
    }
    pending = _pending_review_warning(tolerance_key, tol)
    if pending:
        outcome.warnings.append(pending)
    outcome.explanation = (
        f"Governing deviation {worst.error} {ctx.unit} at row {worst.observation_no}"
        f" against the limit {worst.mpe} {ctx.unit} that applies to it."
    )
    return _finalise(
        ctx,
        outcome,
        measured=abs(worst.error or Decimal(0)),
        limit=worst.mpe or Decimal(0),
        comparator=tol.get("comparator", "abs_lte"),
        rule_id=tol.get("rule_id", f"R76-{tolerance_key.upper()}"),
        clause=tol.get("clause_reference"),
        unit=ctx.unit,
        require_all_rows=True,
    )


# ---------------------------------------------------------------------------
# Zero return (OIML R 76-1:2006 4.5.4 / T.3.2.1)
# ---------------------------------------------------------------------------
def calc_zero_return(ctx: CalcContext) -> CalcOutcome:
    """Deviation of the zero indication after the load has been removed.

    The reference is the zero reading taken before the load was applied, never
    simply the first row of the table: a table recorded in another order must
    not change the metrological result.
    """
    entries = _value_entries(ctx)
    if len(entries) < 2:
        raise MissingInputError(
            "zero return requires the initial zero reading and the reading taken after unloading"
        )
    index, warnings = _resolve_reference(ctx, entries, phrases=ZERO_REFERENCE_PHRASES)
    return _sequence_outcome(
        ctx,
        tolerance_key=_procedure_tolerance_key(ctx, "zero_return"),
        method="E_zr = reading(after unloading) - reading(initial zero)",
        entries=entries,
        reference_index=index,
        reference_basis="initial zero reading taken before the load was applied",
        extra_intermediates={"return_readings": len(entries) - index - 1},
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Creep, warm-up and stability: procedures defined by the course of time
# ---------------------------------------------------------------------------
def _time_ordered_sequence(
    ctx: CalcContext,
    *,
    tolerance_key: str,
    method: str,
    reference_basis: str,
    minimum_duration: Decimal | None,
) -> CalcOutcome:
    entries = _value_entries(ctx)
    if len(entries) < 2:
        raise MissingInputError(
            f"{tolerance_key} requires the reading at the start of the period and at least one later reading"
        )
    missing = [obs.observation_no for obs, _ in entries if obs.elapsed_seconds is None]
    if missing:
        raise MissingInputError(
            "every reading of this procedure must record the elapsed time;"
            " no time was recorded for row(s) "
            + ", ".join(str(number) for number in missing)
        )
    ordered = sorted(entries, key=lambda item: (item[0].elapsed_seconds, item[0].observation_no))
    duration = ordered[-1][0].elapsed_seconds - ordered[0][0].elapsed_seconds
    minimum = to_decimal(
        ctx.calc_rule("minimum_duration_seconds", minimum_duration),
        field="minimum_duration_seconds",
    )
    if minimum is not None and duration < minimum:
        raise MissingInputError(
            f"the observation period must last at least {minimum} s;"
            f" the recorded readings cover {duration} s"
        )
    warnings: list[str] = []
    if not any(obs.temperature_c is not None for obs, _ in ordered):
        warnings.append("No ambient temperature was recorded during the observation period.")
    return _sequence_outcome(
        ctx,
        tolerance_key=tolerance_key,
        method=method,
        entries=ordered,
        reference_index=0,
        reference_basis=reference_basis,
        extra_intermediates={
            "observation_seconds": str(duration),
            "minimum_duration_seconds": str(minimum) if minimum is not None else None,
        },
        warnings=warnings,
    )


def calc_creep(ctx: CalcContext) -> CalcOutcome:
    """Creep: change of indication under a constant load (4.5.5 / T.3.3).

    The reading at the start of the constant-load period is the reference; the
    load must be held for the configured minimum period, and every reading must
    carry its elapsed time so the period can be proven from the record.
    """
    return _time_ordered_sequence(
        ctx,
        tolerance_key=_procedure_tolerance_key(ctx, "creep"),
        method="E_i = reading(t_i) - reading(t_0) under a constant load",
        reference_basis="reading at the start of the constant-load period",
        minimum_duration=Decimal(900),
    )


def calc_warmup(ctx: CalcContext) -> CalcOutcome:
    """Warm-up time: drift between power-on and the end of the warm-up period."""
    return _time_ordered_sequence(
        ctx,
        tolerance_key=_procedure_tolerance_key(ctx, "warmup"),
        method="E_i = reading(t_i) - reading(t_0) after power-on",
        reference_basis="reading taken at power-on (t = 0)",
        minimum_duration=Decimal(1800),
    )


def calc_stability(ctx: CalcContext) -> CalcOutcome:
    """Stability of equilibrium: variation of the indication while loaded."""
    entries = _value_entries(ctx)
    if len(entries) < 2:
        raise MissingInputError(
            "stability requires at least two readings of the loaded instrument"
        )
    times = [obs.elapsed_seconds for obs, _ in entries]
    warnings: list[str] = []
    intermediates: dict[str, Any] = {}
    if any(value is None for value in times):
        ordered = entries
        warnings.append(
            "The elapsed time was not recorded against every reading, so the length of the"
            " observation period could not be confirmed from the record."
        )
    else:
        ordered = sorted(entries, key=lambda item: (item[0].elapsed_seconds, item[0].observation_no))
        duration = ordered[-1][0].elapsed_seconds - ordered[0][0].elapsed_seconds
        intermediates["observation_seconds"] = str(duration)
        minimum = to_decimal(
            ctx.calc_rule("minimum_duration_seconds"), field="minimum_duration_seconds"
        )
        if minimum is not None and duration < minimum:
            raise MissingInputError(
                f"the stability observation period must last at least {minimum} s;"
                f" the recorded readings cover {duration} s"
            )
    return _sequence_outcome(
        ctx,
        tolerance_key=_procedure_tolerance_key(ctx, "stability"),
        method="E_i = reading(t_i) - reading(t_0) over the observation period",
        entries=ordered,
        reference_index=0,
        reference_basis="first reading of the observation period",
        extra_intermediates=intermediates,
        warnings=warnings,
    )


def calc_temperature_no_load(ctx: CalcContext) -> CalcOutcome:
    """Temperature effect on the no-load indication (4.5.2 / T.3.4.2).

    The reference is the reading taken nearest the reference temperature, the
    recorded temperatures must cover the span the procedure requires, and every
    row must state the temperature at which it was read.
    """
    entries = _value_entries(ctx)
    if len(entries) < 2:
        raise MissingInputError(
            "the temperature effect on the no-load indication requires readings at two or more temperatures"
        )
    missing = [obs.observation_no for obs, _ in entries if obs.temperature_c is None]
    if missing:
        raise MissingInputError(
            "the temperature must be recorded against every reading;"
            " no temperature was recorded for row(s) "
            + ", ".join(str(number) for number in missing)
        )
    temperatures = [obs.temperature_c for obs, _ in entries]
    span = max(temperatures) - min(temperatures)
    required_span = to_decimal(
        ctx.calc_rule("required_temperature_span_c"), field="required_temperature_span_c"
    )
    if required_span is not None and span < required_span:
        raise MissingInputError(
            f"the recorded temperatures span {span} C, which is less than the"
            f" {required_span} C this procedure requires"
        )
    reference_c = to_decimal(
        ctx.calc_rule("reference_temperature_c", TEMPERATURE_REFERENCE_C),
        field="reference_temperature_c",
    )
    index = min(
        range(len(entries)),
        key=lambda position: (
            abs(entries[position][0].temperature_c - reference_c),
            entries[position][0].observation_no,
        ),
    )
    warnings: list[str] = []
    if entries[index][0].temperature_c != reference_c:
        warnings.append(
            f"No reading was taken exactly at the reference temperature {reference_c} C;"
            " the nearest reading was used as the reference."
        )
    return _sequence_outcome(
        ctx,
        tolerance_key=_procedure_tolerance_key(ctx, "temperature_no_load"),
        method="E_i = reading(T_i) - reading(T_ref), instrument unloaded",
        entries=entries,
        reference_index=index,
        reference_basis=f"reading nearest the reference temperature {reference_c} C",
        extra_intermediates={
            "temperature_span": str(span),
            "required_temperature_span_c": str(required_span) if required_span is not None else None,
            "reference_temperature_c": str(reference_c),
        },
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Procedures whose reference is a stage of the record
# (tilting, voltage variation, immunity, damp heat)
# ---------------------------------------------------------------------------
def _positional_sequence(
    ctx: CalcContext,
    *,
    tolerance_key: str,
    method: str,
    reference_phrases: tuple[str, ...],
    reference_basis: str,
) -> CalcOutcome:
    entries = _value_entries(ctx)
    if len(entries) < 2:
        raise MissingInputError(f"{tolerance_key} requires at least two observation rows")
    index, warnings = _resolve_reference(ctx, entries, phrases=reference_phrases)
    return _sequence_outcome(
        ctx,
        tolerance_key=tolerance_key,
        method=method,
        entries=entries,
        reference_index=index,
        reference_basis=reference_basis,
        warnings=warnings,
    )


def calc_tilt(ctx: CalcContext) -> CalcOutcome:
    """Tilting: effect of leaving the reference position (T.3.6)."""
    return _positional_sequence(
        ctx,
        tolerance_key=_procedure_tolerance_key(ctx, "tilt"),
        method="E_tilt = reading(tilted position) - reading(reference position)",
        reference_phrases=("level", "horizontal", "reference position", "not tilted", "0 tilt"),
        reference_basis="reading with the instrument in its reference position",
    )


def calc_voltage_variation(ctx: CalcContext) -> CalcOutcome:
    """Mains voltage variation, dips and short interruptions (T.3.9)."""
    return _positional_sequence(
        ctx,
        tolerance_key=_procedure_tolerance_key(ctx, "voltage_variation"),
        method="E_v = reading(supply condition) - reading(nominal supply)",
        reference_phrases=("nominal", "rated supply", "reference supply", "rated"),
        reference_basis="reading at the nominal supply voltage",
    )


def calc_emc_immunity(ctx: CalcContext) -> CalcOutcome:
    """Electrical bursts, surges, ESD and RF immunity (T.3.10)."""
    return _positional_sequence(
        ctx,
        tolerance_key=_procedure_tolerance_key(ctx, "emc_immunity"),
        method="E_emc = reading(after the disturbance) - reading(before the disturbance)",
        reference_phrases=("before", "no disturbance", "baseline", "reference"),
        reference_basis="reading taken before the disturbance was applied",
    )


def calc_damp_heat(ctx: CalcContext) -> CalcOutcome:
    """Damp heat, span stability and endurance (T.3.11)."""
    return _positional_sequence(
        ctx,
        tolerance_key=_procedure_tolerance_key(ctx, "damp_heat"),
        method="E_dh = reading(after conditioning) - reading(before conditioning)",
        reference_phrases=("before", "pre-conditioning", "baseline", "reference"),
        reference_basis="reading taken before the conditioning stage",
    )


def calc_tare(ctx: CalcContext) -> CalcOutcome:
    """Tare device in subtractive mode (4.6.4 / T.3.13).

    Once the tare device is engaged the instrument must indicate zero, and the
    net indication of a load applied afterwards must agree with that load. Both
    are the same quantity: the error of indication of the tared reading.
    """
    _require_e(ctx)
    if len(ctx.observations) < 2:
        raise MissingInputError(
            "the tare procedure requires the tared zero reading and at least one loaded reading"
        )
    require_dl = bool(ctx.calc_rule("require_additional_load", False))
    tolerance_key = _procedure_tolerance_key(ctx, "tare")
    outcome = CalcOutcome()
    tol: dict[str, Any] = {}
    for obs in ctx.observations:
        load = _load_in_instrument_unit(ctx, obs)
        computation = error_from_indication(
            indication=obs.indication,
            load=load,
            e=ctx.e,
            additional_load=obs.additional_load,
            require_additional_load=require_dl,
        )
        limit, tol = _tolerance_limit(ctx, tolerance_key, load)
        error = _maybe_round(ctx, computation.E)
        outcome.rows.append(
            RowResult(
                observation_no=obs.observation_no,
                label=obs.label,
                load=load,
                indication=computation.indication,
                additional_load=computation.additional_load,
                error=error,
                mpe=limit,
                margin=limit - abs(error),
                within=abs(error) <= limit,
                detail={
                    **computation.as_dict(),
                    "tare_mode": ctx.calc_rule("tare_mode", "subtractive"),
                    "rule_id": tol.get("rule_id"),
                    "clause_reference": tol.get("clause_reference"),
                },
            )
        )
    worst = _worst_row(outcome.rows)
    outcome.intermediates = {
        "method": "E = I + 0.5e - dL - L with the tare device engaged",
        "tare_mode": ctx.calc_rule("tare_mode", "subtractive"),
        "governing_row": worst.observation_no,
        "max_abs_error": max(abs(row.error or Decimal(0)) for row in outcome.rows),
        "rows_outside_limit": [row.observation_no for row in outcome.rows if row.within is False],
        "tolerance": tol,
    }
    pending = _pending_review_warning(tolerance_key, tol)
    if pending:
        outcome.warnings.append(pending)
    outcome.explanation = (
        f"Governing tare error {worst.error} {ctx.unit} at row {worst.observation_no}"
        f" against the limit {worst.mpe} {ctx.unit} that applies to it."
    )
    return _finalise(
        ctx,
        outcome,
        measured=abs(worst.error or Decimal(0)),
        limit=worst.mpe or Decimal(0),
        comparator="abs_lte",
        rule_id=tol.get("rule_id", "R76-TARE"),
        clause=tol.get("clause_reference"),
        unit=ctx.unit,
        require_all_rows=True,
    )


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
def _conditional_checklist_items(ctx: CalcContext) -> dict[str, str]:
    """Checklist items that are mandatory only on some instruments.

    A tare device, embedded software or a battery only has to be examined when
    the instrument actually has one, so each condition is evaluated against the
    same configuration attributes the applicability expressions use (audit item
    6). The returned mapping is the item and the reason it applies here, which is
    shown when the item is missing.
    """
    requirements = ctx.calc_rule("conditional_items", []) or []
    if not requirements:
        return {}
    context = {"instrument": ctx.instrument, "environment": ctx.environment, "case": {}}
    applied: dict[str, str] = {}
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        item = requirement.get("item_code")
        if not item:
            continue
        condition = requirement.get("when") or {}
        try:
            applies = evaluate_expression(condition, context).passed
        except Exception as exc:
            raise ValidationError(
                f"the applicability condition of checklist item '{item}' could not be"
                f" evaluated: {exc}"
            ) from exc
        if applies:
            applied[item] = requirement.get("description") or "the condition applies to this instrument"
    return applied

def _checklist_calculator():
    def calculator(ctx: CalcContext) -> CalcOutcome:
        mandatory = list(ctx.calc_rule("mandatory_items", []) or [])
        conditional = _conditional_checklist_items(ctx)
        for item in conditional:
            if item not in mandatory:
                mandatory.append(item)
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
            described = ", ".join(
                f"{item} ({conditional[item]})" if item in conditional else item
                for item in missing
            )
            raise MissingInputError(
                "checklist is incomplete; missing mandatory item(s): " + described
            )

        outcome.intermediates = {
            "method": "every mandatory checklist item must conform",
            "items_checked": len(outcome.rows),
            "non_conforming": non_conforming,
            "mandatory_items": mandatory,
            "conditional_items_applied": conditional,
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
    "T-ZR": calc_zero_return,
    "T-CREEP": calc_creep,
    "T-TEMP-NL": calc_temperature_no_load,
    "T-SENS": calc_sensitivity,
    "T-DISC": calc_discrimination,
    "T-STAB": calc_stability,
    "T-TILT": calc_tilt,
    "T-TARE": calc_tare,
    "T-WARMUP": calc_warmup,
    "T-VOLT": calc_voltage_variation,
    "T-EMC": calc_emc_immunity,
    "T-DAMP": calc_damp_heat,
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
