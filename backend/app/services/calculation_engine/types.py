"""Value objects exchanged between the calculation engine and its callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.utils.decimals import decimal_str, to_decimal


@dataclass(slots=True)
class ObservationRow:
    """A normalised observation row, independent of storage details."""

    observation_no: int
    label: str | None = None
    load: Decimal | None = None
    indication: Decimal | None = None
    additional_load: Decimal | None = None
    elapsed_seconds: Decimal | None = None
    temperature_c: Decimal | None = None
    value: Decimal | None = None
    unit: str | None = None
    range_no: int | None = None
    conforms: bool | None = None
    item_code: str | None = None
    remarks: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "observation_no": self.observation_no,
            "label": self.label,
            "load": decimal_str(self.load),
            "indication": decimal_str(self.indication),
            "additional_load": decimal_str(self.additional_load),
            "elapsed_seconds": decimal_str(self.elapsed_seconds),
            "temperature_c": decimal_str(self.temperature_c),
            "value": decimal_str(self.value),
            "unit": self.unit,
            "range_no": self.range_no,
            "conforms": self.conforms,
            "item_code": self.item_code,
            "remarks": self.remarks,
        }


def find_stage(observations: list["ObservationRow"], phrases: tuple[str, ...]) -> int | None:
    """Index of the first row whose stage label names one of ``phrases``.

    Whole phrases are matched against the lower-cased label, so "after
    unloading" is not mistaken for an initial zero reading merely because it
    mentions unloading.
    """
    for index, obs in enumerate(observations):
        label = (obs.label or obs.item_code or "").strip().lower()
        if label and any(phrase in label for phrase in phrases):
            return index
    return None


@dataclass(slots=True)
class RowResult:
    """Per-row calculation output, retained for the "How calculated" panel."""

    observation_no: int
    label: str | None = None
    load: Decimal | None = None
    indication: Decimal | None = None
    additional_load: Decimal | None = None
    value: Decimal | None = None
    error: Decimal | None = None
    reference_error: Decimal | None = None
    corrected_error: Decimal | None = None
    mpe: Decimal | None = None
    margin: Decimal | None = None
    within: bool | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "observation_no": self.observation_no,
            "label": self.label,
            "load": decimal_str(self.load),
            "indication": decimal_str(self.indication),
            "additional_load": decimal_str(self.additional_load),
            "value": decimal_str(self.value),
            "error": decimal_str(self.error),
            "reference_error": decimal_str(self.reference_error),
            "corrected_error": decimal_str(self.corrected_error),
            "mpe": decimal_str(self.mpe),
            "margin": decimal_str(self.margin),
            "within": self.within,
            "detail": _jsonable(self.detail),
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@dataclass(slots=True)
class CalcContext:
    """Everything the deterministic engine is allowed to depend on."""

    test_code: str
    instrument: dict[str, Any]
    observations: list[ObservationRow]
    ruleset: dict[str, Any] = field(default_factory=dict)
    test_definition: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    stage: str = "verification"
    rule_version: str | None = None
    ranges: list[dict[str, Any]] = field(default_factory=list)

    # --- convenience accessors -------------------------------------------
    @property
    def e(self) -> Decimal | None:
        return self.instrument.get("e")

    def e_for(self, load: Decimal | None = None) -> Decimal | None:
        """The verification scale interval that applies to a load.

        Multi-range and multi-interval instruments declare one ``e`` per range,
        and OIML R 76-1 Table 1 is indexed by ``m = load / e``. Resolving the
        band with the interval the load actually falls in is what makes
        range-specific testing meaningful; a single-range instrument falls back
        to its declared ``e``.
        """
        if load is None:
            return self.e
        magnitude = abs(load)
        for item in self.ranges or []:
            upper = to_decimal(item.get("max_capacity"), field="ranges.max_capacity")
            lower = to_decimal(item.get("min_capacity"), field="ranges.min_capacity")
            candidate = to_decimal(item.get("e"), field="ranges.e")
            if upper is None or candidate is None or candidate <= 0:
                continue
            if lower is not None and magnitude < lower:
                continue
            if magnitude <= upper:
                return candidate
        return self.e

    @property
    def d(self) -> Decimal | None:
        return self.instrument.get("d")

    @property
    def instrument_class(self) -> str | None:
        return self.instrument.get("instrument_class")

    @property
    def unit(self) -> str:
        return self.instrument.get("unit") or "g"

    @property
    def resolution(self) -> Decimal | None:
        return self.d if self.d and self.d > 0 else self.e

    def tolerance(self, key: str) -> dict[str, Any]:
        return ((self.ruleset or {}).get("tolerances") or {}).get(key) or {}

    def calc_rule(self, key: str, default: Any = None) -> Any:
        return ((self.test_definition or {}).get("calculation_rules") or {}).get(key, default)

    def compliance_rule(self, key: str, default: Any = None) -> Any:
        return ((self.test_definition or {}).get("compliance_rules") or {}).get(key, default)


@dataclass(slots=True)
class CalcOutcome:
    """Result of a single deterministic calculation."""

    is_valid: bool = True
    status: str = "PENDING"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    measured_value: Decimal | None = None
    limit_value: Decimal | None = None
    margin: Decimal | None = None
    unit: str = ""
    comparator: str = "abs_lte"
    rule_id: str | None = None
    rule_version: str | None = None
    clause_reference: str | None = None
    explanation: str = ""
    intermediates: dict[str, Any] = field(default_factory=dict)
    rows: list[RowResult] = field(default_factory=list)
    rounding_policy: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "status": self.status,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "measured_value": decimal_str(self.measured_value),
            "limit_value": decimal_str(self.limit_value),
            "margin": decimal_str(self.margin),
            "unit": self.unit,
            "comparator": self.comparator,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "clause_reference": self.clause_reference,
            "explanation": self.explanation,
            "intermediates": _jsonable(self.intermediates),
            "rows": [row.as_dict() for row in self.rows],
            "rounding_policy": self.rounding_policy,
        }
