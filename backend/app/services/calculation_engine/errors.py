"""Error-of-indication mathematics (PRD 11.3).

For a digital indication the R 76-2 error method is:

    P       = I + (0.5 * e) - delta_L
    E       = P - L

where I is the indication, delta_L the additional load to the next changeover
point and L the applied load. Every intermediate value is preserved so the
result stays explainable from its inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.utils.decimals import CALC_PRECISION, InvalidDecimalValue, to_decimal


class MissingInputError(InvalidDecimalValue):
    """A required observation is absent -> the test resolves to INCOMPLETE."""


class ValidationError(InvalidDecimalValue):
    """An input is present but unusable -> the test resolves to INVALID."""


@dataclass(frozen=True, slots=True)
class ErrorComputation:
    indication: Decimal
    load: Decimal
    additional_load: Decimal
    e: Decimal
    half_e: Decimal
    P: Decimal
    E: Decimal
    assumed_additional_load: bool

    def as_dict(self) -> dict:
        from app.utils.decimals import decimal_str

        return {
            "I": decimal_str(self.indication),
            "L": decimal_str(self.load),
            "delta_L": decimal_str(self.additional_load),
            "e": decimal_str(self.e),
            "0.5e": decimal_str(self.half_e),
            "P": decimal_str(self.P),
            "E": decimal_str(self.E),
            "additional_load_assumed_zero": self.assumed_additional_load,
        }


def error_from_indication(
    *,
    indication,
    load,
    e,
    additional_load=None,
    require_additional_load: bool = False,
) -> ErrorComputation:
    """Compute P and E for one digital-indication observation."""
    i_value = to_decimal(indication, field="indication")
    l_value = to_decimal(load, field="load")
    e_value = to_decimal(e, field="e")
    if i_value is None:
        raise MissingInputError("indication is required")
    if l_value is None:
        raise MissingInputError("load is required")
    if e_value is None or e_value <= 0:
        raise MissingInputError("verification scale interval e must be greater than zero")

    delta = to_decimal(additional_load, field="additional_load")
    assumed = delta is None
    if assumed:
        if require_additional_load:
            raise MissingInputError(
                "additional load to the next changeover point is required for this test"
            )
        delta = Decimal(0)

    with localcontext() as ctx:
        ctx.prec = CALC_PRECISION
        half_e = e_value / 2
        P = i_value + half_e - delta
        E = P - l_value

    return ErrorComputation(
        indication=i_value,
        load=l_value,
        additional_load=delta,
        e=e_value,
        half_e=half_e,
        P=P,
        E=E,
        assumed_additional_load=assumed,
    )


def corrected_error(error: Decimal, zero_error: Decimal | None) -> Decimal:
    """Apply the error-at-zero correction E_c = E - E_0 (R 76-2)."""
    if zero_error is None:
        return error
    with localcontext() as ctx:
        ctx.prec = CALC_PRECISION
        return error - zero_error
