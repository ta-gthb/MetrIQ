"""Decimal helpers.

PRD 11.5 requires decimal arithmetic for metrological values rather than binary
floating point, with rounding applied only at explicit, documented stages.
Every metrological value that crosses a JSON boundary is stored as a string so
it round-trips without precision loss.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any

# 28 significant digits is the default Decimal context; we make it explicit so
# results are reproducible regardless of the host.
CALC_PRECISION = 28

ROUNDING_POLICY_R76 = "R76-HALF-UP-TO-RESOLUTION"
ROUNDING_POLICY_EXACT = "EXACT-NO-ROUNDING"


class InvalidDecimalValue(ValueError):
    """Raised when an input cannot be represented exactly in decimal."""


def to_decimal(value: Any, *, field: str = "value") -> Decimal | None:
    """Convert a JSON/SQL value to Decimal without introducing binary error."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise InvalidDecimalValue(f"{field} must be numeric, got a boolean")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # Route through repr so 0.1 stays 0.1 rather than 0.1000000000000000055.
        return Decimal(repr(value))
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise InvalidDecimalValue(f"{field} is not a valid number: {value!r}") from exc
    raise InvalidDecimalValue(f"{field} has unsupported type {type(value).__name__}")


def require_decimal(value: Any, *, field: str) -> Decimal:
    parsed = to_decimal(value, field=field)
    if parsed is None:
        raise InvalidDecimalValue(f"{field} is required")
    return parsed


def resolution_exponent(resolution: Decimal) -> int:
    return resolution.normalize().as_tuple().exponent


def quantize(value: Decimal, resolution: Decimal | None) -> Decimal:
    """Round to a measurement resolution using ROUND_HALF_UP.

    ``resolution`` is the smallest representable increment (d, or e when the
    instrument does not state d). ``None`` leaves the value untouched.
    """
    if resolution is None or resolution <= 0:
        return value
    exponent = resolution_exponent(resolution)
    if not isinstance(exponent, int):
        return value
    quantum = Decimal(1).scaleb(exponent)
    with localcontext() as ctx:
        ctx.prec = CALC_PRECISION
        return value.quantize(quantum, rounding=ROUND_HALF_UP)


def decimal_str(value: Any) -> str | None:
    """Serialise a numeric value to a plain (non-scientific) string.

    Values that arrive from JSON, SQLite or a form field are coerced first so a
    string such as "30000" never reaches Decimal-only code paths. Invalid input
    is rejected rather than silently rendered.
    """
    if value is None:
        return None
    if not isinstance(value, Decimal):
        value = to_decimal(value)
        if value is None:
            return None
    if value == 0:
        return "0"
    normalised = value.normalize()
    _sign, _digits, exponent = normalised.as_tuple()
    if isinstance(exponent, int) and -18 <= exponent <= 18:
        return format(normalised, "f")
    return format(value, "f")


def safe_divide(numerator: Decimal, denominator: Decimal, *, field: str = "division") -> Decimal:
    if denominator == 0:
        raise InvalidDecimalValue(f"{field}: division by zero")
    with localcontext() as ctx:
        ctx.prec = CALC_PRECISION
        return numerator / denominator
