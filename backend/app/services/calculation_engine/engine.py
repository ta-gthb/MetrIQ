"""Calculation engine entry point (PRD 11).

``evaluate`` is pure: it takes a fully materialised :class:`CalcContext` and
returns a :class:`CalcOutcome`. It performs no I/O, so the same call can be
replayed from a stored input snapshot to prove reproducibility (PRD 27.2).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.calculation_engine.calculators import get_calculator, supported_test_codes
from app.services.calculation_engine.errors import MissingInputError, ValidationError
from app.services.calculation_engine.types import (
    CalcContext,
    CalcOutcome,
    ObservationRow,
    find_stage,
)
from app.utils.decimals import InvalidDecimalValue, to_decimal

ENGINE_VERSION = "2.0.0"


def build_observation(data: dict[str, Any], index: int) -> ObservationRow:
    """Normalise a stored/JSON observation payload into an ObservationRow."""
    payload = dict(data.get("input_payload") or {})
    merged = {**payload, **{k: v for k, v in data.items() if v is not None}}

    def number(*keys: str) -> Decimal | None:
        for key in keys:
            if merged.get(key) is not None:
                try:
                    return to_decimal(merged[key], field=key)
                except InvalidDecimalValue:
                    return None
        return None

    conforms = merged.get("conforms")
    if isinstance(conforms, str):
        conforms = conforms.strip().lower() in {"true", "yes", "y", "1", "conforms", "pass"}

    range_no = merged.get("range_no")
    if range_no is not None:
        try:
            range_no = int(range_no)
        except (TypeError, ValueError):
            range_no = None

    return ObservationRow(
        observation_no=int(data.get("observation_no") or index),
        label=data.get("position_label") or merged.get("label") or merged.get("position"),
        load=number("load"),
        indication=number("indication"),
        additional_load=number("additional_load", "delta_l"),
        elapsed_seconds=number("elapsed_seconds", "elapsed"),
        temperature_c=number("temperature_c"),
        value=number("value"),
        unit=merged.get("unit"),
        range_no=range_no,
        conforms=conforms,
        item_code=merged.get("item_code") or merged.get("code"),
        remarks=merged.get("remarks"),
        raw=merged,
    )


def validate_inputs(ctx: CalcContext) -> list[str]:
    """Apply the test definition's declarative validation rules.

    Returns a list of human-readable problems. An empty list means the payload
    passed every configured validation rule.
    """
    rules = (ctx.test_definition or {}).get("validation_rules") or {}
    problems: list[str] = []
    if not isinstance(rules, dict):
        return problems

    min_rows = rules.get("min_rows")
    if isinstance(min_rows, int) and len(ctx.observations) < min_rows:
        problems.append(f"at least {min_rows} observation row(s) are required")
    max_rows = rules.get("max_rows")
    if isinstance(max_rows, int) and len(ctx.observations) > max_rows:
        problems.append(f"at most {max_rows} observation row(s) are allowed")

    required_rows = rules.get("required_rows") or []
    if isinstance(required_rows, list) and required_rows:
        positions: list[int | None] = []
        for requirement in required_rows:
            if not isinstance(requirement, dict):
                continue
            phrases = tuple(
                str(phrase).strip().lower()
                for phrase in (requirement.get("match") or [])
                if str(phrase).strip()
            )
            load_equals = requirement.get("load_equals")
            description = requirement.get("description") or " / ".join(phrases) or "required row"
            index: int | None = None
            if phrases:
                index = find_stage(ctx.observations, phrases)
            if index is None and load_equals is not None:
                target = to_decimal(load_equals, field="required_rows.load_equals")
                for position, obs in enumerate(ctx.observations):
                    if obs.load is not None and target is not None and obs.load == target:
                        index = position
                        break
            if index is None:
                problems.append(
                    f"the {description} is missing; record it as a row of this test"
                )
            positions.append(index)
        if rules.get("stage_order") == "strict":
            recorded = [position for position in positions if position is not None]
            if recorded != sorted(recorded):
                problems.append(
                    "the required stages were recorded out of the order this procedure prescribes"
                )

    non_negative = set(rules.get("non_negative_fields") or [])
    maximum = rules.get("max_value")
    minimum = rules.get("min_value")
    precision_fields = rules.get("precision_fields") or {}
    required = set(rules.get("required_fields") or [])

    for obs in ctx.observations:
        for field in non_negative:
            value = getattr(obs, field, None)
            if value is not None and value < 0:
                problems.append(
                    f"row {obs.observation_no}: {field} must not be negative (got {value})"
                )
        for field in required:
            if getattr(obs, field, None) is None:
                problems.append(f"row {obs.observation_no}: {field} is required")
        for field, decimals in precision_fields.items():
            value = getattr(obs, field, None)
            if value is None:
                continue
            exponent = value.as_tuple().exponent
            if isinstance(exponent, int) and -exponent > int(decimals):
                problems.append(
                    f"row {obs.observation_no}: {field} has more than {decimals} decimal place(s)"
                )
        if maximum is not None:
            ceiling = to_decimal(maximum)
            if ceiling is not None:
                for field in ("load", "value"):
                    value = getattr(obs, field, None)
                    if value is not None and value > ceiling:
                        problems.append(
                            f"row {obs.observation_no}: {field} {value} exceeds the permitted maximum {ceiling}"
                        )
        if minimum is not None:
            floor = to_decimal(minimum)
            if floor is not None:
                for field in ("load", "value"):
                    value = getattr(obs, field, None)
                    if value is not None and value < floor:
                        problems.append(
                            f"row {obs.observation_no}: {field} {value} is below the permitted minimum {floor}"
                        )
    return problems


def evaluate(ctx: CalcContext) -> CalcOutcome:
    """Run the deterministic calculation for ``ctx.test_code``.

    Never raises for bad input: the failure is encoded in the returned outcome
    as INCOMPLETE (missing input) or INVALID (unusable input), matching the
    status model in PRD 11.4.
    """
    outcome = CalcOutcome()
    try:
        calculator = get_calculator(ctx.test_code)
    except ValidationError as exc:
        outcome.is_valid = False
        outcome.status = "INVALID"
        outcome.errors.append(str(exc))
        outcome.explanation = str(exc)
        return outcome

    try:
        problems = validate_inputs(ctx)
        if problems:
            outcome.is_valid = False
            outcome.status = "INCOMPLETE"
            outcome.errors.extend(problems)
            outcome.explanation = "; ".join(problems)
            return outcome
        result = calculator(ctx)
    except MissingInputError as exc:
        outcome.is_valid = False
        outcome.status = "INCOMPLETE"
        outcome.errors.append(str(exc))
        outcome.explanation = str(exc)
        return outcome
    except (ValidationError, InvalidDecimalValue, ArithmeticError, KeyError) as exc:
        outcome.is_valid = False
        outcome.status = "INVALID"
        outcome.errors.append(str(exc))
        outcome.explanation = f"Calculation could not be completed: {exc}"
        return outcome
    except Exception as exc:  # pragma: no cover - defensive: never 500 on a test
        outcome.is_valid = False
        outcome.status = "INVALID"
        outcome.errors.append(f"unexpected calculation error: {exc}")
        outcome.explanation = "Calculation could not be completed."
        return outcome

    result.intermediates.setdefault("engine_version", ENGINE_VERSION)
    return result


__all__ = [
    "ENGINE_VERSION",
    "CalcContext",
    "CalcOutcome",
    "build_observation",
    "evaluate",
    "supported_test_codes",
    "validate_inputs",
]
