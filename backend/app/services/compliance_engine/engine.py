"""Compliance resolution.

    abs(measured_error) <= applicable_limit   -> PASS
    otherwise                                 -> FAIL
    missing required input                    -> INCOMPLETE
    invalid input/formula                     -> INVALID
    not applicable                            -> NOT_APPLICABLE
    configured approved exception             -> WAIVED

PASS/FAIL is derived here and nowhere else, so it can never be typed directly
into the record (PRD 27.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.calculation_engine.types import CalcOutcome
from app.utils.decimals import decimal_str

COMPARATORS = {"abs_lte", "lte", "gte", "range", "eq"}


class ComplianceConfigurationError(ValueError):
    """Raised when the rule set cannot be applied to the calculation output."""


@dataclass(slots=True)
class ComplianceDecision:
    status: str
    measured_value: Decimal | None = None
    limit_value: Decimal | None = None
    margin: Decimal | None = None
    comparator: str = "abs_lte"
    explanation: str = ""
    detail: dict | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "measured_value": decimal_str(self.measured_value),
            "limit_value": decimal_str(self.limit_value),
            "margin": decimal_str(self.margin),
            "comparator": self.comparator,
            "explanation": self.explanation,
            "detail": self.detail,
        }


def _compare(measured: Decimal, limit: Decimal, comparator: str) -> bool:
    if comparator == "abs_lte":
        return abs(measured) <= limit
    if comparator == "lte":
        return measured <= limit
    if comparator == "gte":
        return measured >= limit
    if comparator == "eq":
        return measured == limit
    raise ComplianceConfigurationError(f"unsupported comparator '{comparator}'")


def decide_status(
    *,
    is_valid: bool,
    measured_value: Decimal | None,
    limit_value: Decimal | None,
    comparator: str = "abs_lte",
    applicability_status: str | None = None,
    is_waived: bool = False,
    intrinsic_status: str | None = None,
) -> ComplianceDecision:
    if applicability_status == "NOT_APPLICABLE":
        return ComplianceDecision(
            status="NOT_APPLICABLE",
            measured_value=measured_value,
            limit_value=limit_value,
            comparator=comparator,
            explanation="The applicable rules marked this test as not applicable.",
        )
    if is_waived:
        return ComplianceDecision(
            status="WAIVED",
            measured_value=measured_value,
            limit_value=limit_value,
            comparator=comparator,
            explanation="A configured, approved exception applies to this test.",
        )
    if not is_valid:
        status = intrinsic_status if intrinsic_status in {"INCOMPLETE", "INVALID"} else "INVALID"
        return ComplianceDecision(
            status=status,
            measured_value=measured_value,
            limit_value=limit_value,
            comparator=comparator,
            explanation="The test could not be evaluated from the supplied inputs.",
        )
    if measured_value is None or limit_value is None:
        return ComplianceDecision(
            status="INCOMPLETE",
            measured_value=measured_value,
            limit_value=limit_value,
            comparator=comparator,
            explanation="No measured value and limit are available for comparison.",
        )
    if comparator not in COMPARATORS:
        raise ComplianceConfigurationError(f"unsupported comparator '{comparator}'")

    passed = _compare(measured_value, limit_value, comparator)
    margin = (measured_value - limit_value) if comparator == "gte" else (limit_value - abs(measured_value))
    return ComplianceDecision(
        status="PASS" if passed else "FAIL",
        measured_value=measured_value,
        limit_value=limit_value,
        margin=margin,
        comparator=comparator,
        explanation=(
            f"{'PASS' if passed else 'FAIL'}: measured {decimal_str(measured_value)} against "
            f"limit {decimal_str(limit_value)} ({comparator}), margin {decimal_str(margin)}."
        ),
    )


def evaluate(
    outcome: CalcOutcome,
    *,
    compliance_rules: dict | None = None,
    applicability_status: str | None = None,
    is_waived: bool = False,
) -> ComplianceDecision:
    """Decide the compliance status for a calculation outcome."""
    rules = compliance_rules or {}
    comparator = rules.get("comparator") or outcome.comparator or "abs_lte"

    measured = outcome.measured_value
    limit = outcome.limit_value
    override_limit = rules.get("limit_value")
    if override_limit is not None:
        from app.utils.decimals import to_decimal

        parsed = to_decimal(override_limit, field="compliance_rules.limit_value")
        if parsed is not None:
            limit = parsed

    decision = decide_status(
        is_valid=outcome.is_valid,
        measured_value=measured,
        limit_value=limit,
        comparator=comparator,
        applicability_status=applicability_status,
        is_waived=is_waived,
        intrinsic_status=outcome.status if not outcome.is_valid else None,
    )
    decision.detail = {
        "engine_status": outcome.status,
        "rule_id": outcome.rule_id,
        "rule_version": outcome.rule_version,
        "clause_reference": outcome.clause_reference,
        "rounding_policy": outcome.rounding_policy,
        "warnings": list(outcome.warnings),
        "errors": list(outcome.errors),
    }
    if outcome.explanation:
        decision.explanation = f"{outcome.explanation} {decision.explanation}".strip()
    return decision
