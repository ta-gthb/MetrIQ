"""Maximum permissible error resolution (PRD 11.2).

    m = load / e
    resolve_mpe(standard_version, instrument_class, load, e, verification_stage)
        -> mpe_value, rule_id, rule_version

The bands themselves live in the versioned ruleset payload, never in code, so a
standards revision is a data change rather than a rewrite (PRD 24.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.utils.decimals import InvalidDecimalValue, to_decimal


class MpeResolutionError(ValueError):
    """Raised when no band can be resolved for the supplied instrument class."""


@dataclass(frozen=True, slots=True)
class MpeResolution:
    mpe_value: Decimal
    factor: Decimal
    unit: str
    band_index: int
    band_label: str
    rule_id: str
    rule_version: str
    clause_reference: str
    m: Decimal

    def as_dict(self) -> dict:
        from app.utils.decimals import decimal_str

        return {
            "mpe_value": decimal_str(self.mpe_value),
            "factor": decimal_str(self.factor),
            "unit": self.unit,
            "band_index": self.band_index,
            "band_label": self.band_label,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "clause_reference": self.clause_reference,
            "m": decimal_str(self.m),
        }


def _normalise_class(instrument_class: str | None) -> str:
    if not instrument_class:
        raise MpeResolutionError("instrument class is required to resolve MPE")
    return str(instrument_class).strip().upper().replace(" ", "")


def _band_source(ruleset: dict, instrument_class: str, stage: str) -> tuple[list[dict], dict]:
    mpe_block = (ruleset or {}).get("mpe") or {}
    key = "in_service_bands" if stage == "in_service" else "bands"
    bands_by_class = mpe_block.get(key) or mpe_block.get("bands") or {}
    bands = bands_by_class.get(instrument_class)
    if bands is None:
        # Accept either notation: the ruleset stores Roman numerals, but an
        # instrument record (or an API client) may legitimately say "3".
        roman_to_arabic = {"III": "3", "IIII": "4", "II": "2", "I": "1"}
        arabic_to_roman = {value: key for key, value in roman_to_arabic.items()}
        for alias in (roman_to_arabic.get(instrument_class), arabic_to_roman.get(instrument_class)):
            if alias is None:
                continue
            bands = bands_by_class.get(alias)
            if bands:
                break
    if not bands:
        raise MpeResolutionError(
            f"no MPE bands configured for class {instrument_class!r} (stage={stage})"
        )
    return bands, mpe_block


def resolve_mpe(
    *,
    ruleset: dict,
    instrument_class: str,
    load,
    e,
    stage: str = "verification",
    rule_version: str | None = None,
    rule_prefix: str = "R76-MPE",
) -> MpeResolution:
    """Resolve the applicable MPE for a load.

    ``m = load / e`` and the first band whose upper bound is not exceeded wins.
    This matches OIML R 76-1 Table 1, where a boundary value such as m = 500
    belongs to the lower band ("0 <= m <= 500").
    """
    klass = _normalise_class(instrument_class)
    e_value = to_decimal(e, field="e")
    if e_value is None or e_value <= 0:
        raise InvalidDecimalValue("verification scale interval e must be greater than zero")
    load_value = to_decimal(load, field="load")
    if load_value is None:
        raise InvalidDecimalValue("load is required to resolve MPE")

    bands, mpe_block = _band_source(ruleset, klass, stage)
    m = abs(load_value) / e_value

    for index, band in enumerate(bands):
        max_m = band.get("max_m", band.get("max"))
        upper = Decimal("Infinity") if max_m is None else to_decimal(max_m, field="max_m")
        if upper is None:
            upper = Decimal("Infinity")
        if m <= upper:
            factor = to_decimal(band.get("factor", band.get("mpe_e")), field="factor")
            if factor is None:
                raise MpeResolutionError(f"MPE band {index} has no factor configured")
            unit = band.get("unit") or mpe_block.get("unit") or "e"
            mpe_value = factor * e_value if unit == "e" else factor
            return MpeResolution(
                mpe_value=mpe_value,
                factor=factor,
                unit=unit,
                band_index=index,
                band_label=_band_label(band),
                rule_id=f"{rule_prefix}-{klass}-{index + 1:02d}",
                rule_version=rule_version or mpe_block.get("rule_version") or "unversioned",
                clause_reference=band.get("clause_reference")
                or mpe_block.get("clause_reference")
                or "OIML R 76-1 Table 1",
                m=m,
            )
    raise MpeResolutionError(
        f"load {load_value} ({m} e) exceeds the configured MPE table for class {klass}"
    )


def _band_label(band: dict) -> str:
    low = band.get("min_m", 0)
    high = band.get("max_m", band.get("max"))
    high_text = "inf" if high is None else str(high)
    return f"{low} < m <= {high_text} e"


def resolve_mpe_at_zero(ruleset: dict, *, instrument_class: str, e, is_electronic: bool = True):
    """Resolve the MPE applied at zero load.

    Returns ``None`` when the ruleset does not configure a distinct zero point,
    in which case the band table already applies.
    """
    at_zero = ((ruleset or {}).get("mpe") or {}).get("at_zero")
    if not at_zero:
        return None
    applies_to = at_zero.get("applies_to")
    if applies_to:
        expected = "electronic" if is_electronic else "non_electronic"
        if expected not in applies_to and ("all" not in applies_to):
            return None
    klass = _normalise_class(instrument_class)
    by_class = at_zero.get("by_class") or {}
    factor_raw = by_class.get(klass, at_zero.get("factor"))
    if factor_raw is None:
        return None
    factor = to_decimal(factor_raw, field="at_zero.factor")
    e_value = to_decimal(e, field="e")
    if factor is None or e_value is None:
        return None
    unit = at_zero.get("unit", "e")
    return MpeResolution(
        mpe_value=factor * e_value if unit == "e" else factor,
        factor=factor,
        unit=unit,
        band_index=-1,
        band_label="zero load",
        rule_id=at_zero.get("rule_id", "R76-MPE-ZERO"),
        rule_version=at_zero.get("rule_version", "unversioned"),
        clause_reference=at_zero.get("clause_reference", "OIML R 76-1 5.2.2"),
        m=Decimal(0),
    )
