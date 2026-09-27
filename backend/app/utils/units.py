"""Mass unit conversion using exact decimal factors."""

from __future__ import annotations

from decimal import Decimal

from app.utils.decimals import InvalidDecimalValue, to_decimal

# Factor to convert one unit of the key into grams.
MASS_FACTORS: dict[str, Decimal] = {
    "mg": Decimal("0.001"),
    "g": Decimal("1"),
    "kg": Decimal("1000"),
    "t": Decimal("1000000"),
    "lb": Decimal("453.59237"),
    "oz": Decimal("28.349523125"),
}

ALIASES = {
    "milligram": "mg", "milligrams": "mg", "mgs": "mg",
    "gram": "g", "grams": "g", "gm": "g", "gms": "g",
    "kilogram": "kg", "kilograms": "kg", "kgs": "kg",
    "tonne": "t", "tonnes": "t", "metricton": "t",
    "pound": "lb", "pounds": "lb", "lbs": "lb",
    "ounce": "oz", "ounces": "oz",
}


def normalise_unit(unit: str | None) -> str:
    if not unit:
        raise InvalidDecimalValue("unit is required")
    key = unit.strip().lower().replace(" ", "")
    return ALIASES.get(key, key)


def is_mass_unit(unit: str | None) -> bool:
    try:
        return normalise_unit(unit) in MASS_FACTORS
    except InvalidDecimalValue:
        return False


def convert_mass(value, from_unit: str, to_unit: str) -> Decimal:
    amount = to_decimal(value, field="value")
    if amount is None:
        raise InvalidDecimalValue("value is required for conversion")
    source = normalise_unit(from_unit)
    target = normalise_unit(to_unit)
    if source not in MASS_FACTORS:
        raise InvalidDecimalValue(f"unsupported source unit '{from_unit}'")
    if target not in MASS_FACTORS:
        raise InvalidDecimalValue(f"unsupported target unit '{to_unit}'")
    if source == target:
        return amount
    return amount * MASS_FACTORS[source] / MASS_FACTORS[target]
