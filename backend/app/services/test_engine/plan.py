"""Test plan generation (PRD FR-07).

The plan answers "which OIML tests apply to this instrument, and why?" using the
versioned applicability expressions, and preserves the decision trace so a
reviewer can see the reasoning rather than trusting a bare list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.models.test import TestImplementationStatus
from app.rules.expressions import EvaluationTrace, evaluate
from app.utils.decimals import decimal_str


@dataclass(slots=True)
class PlanItem:
    test_code: str
    name: str
    clause_reference: str | None
    category: str
    phase: str
    sequence_no: int
    applicable: bool
    reason: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    definition_id: str | None = None
    manual_override: bool = False
    implementation_status: str = "implemented"
    unsupported_reason: str | None = None

    @property
    def supported(self) -> bool:
        """True when a deterministic calculator exists for this test code."""
        return self.implementation_status == "implemented"

    def as_dict(self) -> dict[str, Any]:
        return {
            "test_code": self.test_code,
            "name": self.name,
            "clause_reference": self.clause_reference,
            "category": self.category,
            "phase": self.phase,
            "sequence_no": self.sequence_no,
            "applicable": self.applicable,
            "reason": self.reason,
            "trace": self.trace,
            "definition_id": self.definition_id,
            "manual_override": self.manual_override,
            "implementation_status": self.implementation_status,
            "unsupported_reason": self.unsupported_reason,
            "supported": self.supported,
        }


def range_payload(instrument) -> list[dict[str, Any]]:
    """Range/interval table as plain data, so expressions can test it."""
    payload: list[dict[str, Any]] = []
    for item in getattr(instrument, "ranges", None) or []:
        payload.append(
            {
                "range_no": getattr(item, "range_no", None),
                "min_capacity": decimal_str(getattr(item, "min_capacity", None)),
                "max_capacity": decimal_str(getattr(item, "max_capacity", None)),
                "e": decimal_str(getattr(item, "verification_scale_interval", None)),
                "d": decimal_str(getattr(item, "actual_scale_interval", None)),
                "unit": getattr(item, "unit", None),
            }
        )
    return payload


def build_applicability_context(
    instrument,
    *,
    case: Any | None = None,
    environment: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the read-only context that applicability expressions see."""

    def get(name: str, default=None):
        return getattr(instrument, name, default)

    ranges = range_payload(instrument)
    context: dict[str, Any] = {
        "instrument": {
            "instrument_class": get("instrument_class"),
            "model": get("model"),
            "type_designation": get("type_designation"),
            "serial_number": get("serial_number"),
            "max_capacity": decimal_str(get("max_capacity")),
            "min_capacity": decimal_str(get("min_capacity")),
            "e": decimal_str(get("verification_scale_interval")),
            "d": decimal_str(get("actual_scale_interval")),
            "unit": get("unit"),
            "is_electronic": bool(get("is_electronic")),
            "is_multi_range": bool(get("is_multi_range")),
            "is_multi_interval": bool(get("is_multi_interval")),
            "has_tare_device": bool(get("has_tare_device")),
            "has_zero_device": bool(get("has_zero_device")),
            "has_level_indicator": bool(get("has_level_indicator")),
            "range_count": len(ranges),
            "ranges": ranges,
        },
        "environment": environment or {},
    }
    if case is not None:
        context["case"] = {
            "id": str(getattr(case, "id", "")),
            "application_no": getattr(case, "application_no", None),
            "status": getattr(case, "status", None),
            "purpose": getattr(case, "purpose", None),
        }
    if extra:
        context.update(extra)
    return context


def generate_plan(
    test_definitions: Iterable[Any],
    *,
    context: dict[str, Any],
) -> list[PlanItem]:
    """Evaluate every active test definition against the instrument context."""
    items: list[PlanItem] = []
    for definition in test_definitions:
        expression = getattr(definition, "applicability_expression", None) or {}
        try:
            trace: EvaluationTrace = evaluate(expression, context)
            applicable = trace.passed
            reason = trace.reason
            checks = trace.checks
        except Exception as exc:  # a malformed rule must not block the plan
            applicable = False
            reason = f"Applicability could not be evaluated: {exc}. Marked for manual review."
            checks = [{"check": "expression evaluation", "result": False, "error": str(exc)}]
        items.append(
            PlanItem(
                test_code=definition.test_code,
                name=definition.name,
                clause_reference=definition.clause_reference,
                category=getattr(definition, "category", "metrological"),
                phase=getattr(definition, "phase", "MVP"),
                sequence_no=getattr(definition, "sequence_no", 100),
                applicable=applicable,
                reason=reason,
                trace=checks,
                definition_id=str(getattr(definition, "id", "")) or None,
                implementation_status=getattr(
                    definition,
                    "implementation_status",
                    TestImplementationStatus.IMPLEMENTED,
                )
                or TestImplementationStatus.IMPLEMENTED,
                unsupported_reason=getattr(definition, "unsupported_reason", None),
            )
        )
    items.sort(key=lambda item: (not item.applicable, item.sequence_no, item.test_code))
    return items
