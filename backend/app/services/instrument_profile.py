"""Derived instrument attributes used by applicability and by the checklist.

A test is applicable because of what the instrument *is*, not because of what
someone typed at plan time (PRD FR-07). Some of those attributes are stored as
columns (electronic, multi-range, tare, zero setting, level indicator); the rest
belong to the instrument's configuration record - self-indicating type, battery,
interfaces, embedded software. They are derived once, here, so the test plan and
the deterministic engine see exactly the same values.
"""

from __future__ import annotations

from typing import Any

BATTERY_MARKERS = ("batt", "accumulator", "rechargeable", "akku")
MAINS_MARKERS = ("vac", "v ac", "mains", "230", "110", "120", "240", "400")


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1"}
    return bool(value)


def _interface_list(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(part).strip() for part in value]
    else:
        parts = []
    return [part.lower() for part in parts if part]


def instrument_flags(instrument: Any) -> dict[str, Any]:
    """Configuration-derived attributes of an instrument record."""
    configuration = getattr(instrument, "configuration", None) or {}
    if not isinstance(configuration, dict):
        configuration = {}
    power_supply = (getattr(instrument, "power_supply", None) or "").strip()
    lowered = power_supply.lower()
    interfaces = _interface_list(configuration.get("interfaces"))

    has_battery = _truthy(configuration.get("has_battery")) or any(
        marker in lowered for marker in BATTERY_MARKERS
    )
    declared_mains = configuration.get("is_mains_powered")
    if declared_mains is None:
        is_mains_powered = bool(power_supply) and not has_battery
        if any(marker in lowered for marker in MAINS_MARKERS):
            is_mains_powered = True
    else:
        is_mains_powered = _truthy(declared_mains)

    self_indicating = configuration.get("self_indicating")
    if self_indicating is None:
        self_indicating = configuration.get("is_self_indicating")

    return {
        # Declared attributes. They are repeated here so the applicability
        # context, the checklist conditions and the engine all read one object
        # rather than three slightly different ones.
        "is_electronic": bool(getattr(instrument, "is_electronic", False)),
        "is_multi_range": bool(getattr(instrument, "is_multi_range", False)),
        "is_multi_interval": bool(getattr(instrument, "is_multi_interval", False)),
        "has_tare_device": bool(getattr(instrument, "has_tare_device", False)),
        "has_zero_device": bool(getattr(instrument, "has_zero_device", False)),
        "has_level_indicator": bool(getattr(instrument, "has_level_indicator", False)),
        "is_self_indicating": (
            _truthy(self_indicating) if self_indicating is not None else None
        ),
        "has_battery": has_battery,
        "is_mains_powered": is_mains_powered,
        "interfaces": interfaces,
        "has_interfaces": bool(interfaces),
        "has_printer": _truthy(configuration.get("has_printer")) or "printer" in interfaces,
        "has_software": _truthy(configuration.get("has_software")),
        "power_supply": power_supply or None,
        "temperature_range": getattr(instrument, "temperature_range", None),
        "configuration": configuration,
    }


__all__ = ["instrument_flags"]
