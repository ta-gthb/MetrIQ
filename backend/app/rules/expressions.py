"""Safe evaluator for applicability expressions (PRD FR-07, 10.1).

Expressions are data, not code. The DSL supports only comparison and boolean
composition so a stored rule can never execute arbitrary logic.

    {"all": [{"field": "instrument.is_electronic", "op": "eq", "value": true}]}
    {"any": [{"field": "instrument.instrument_class", "op": "in", "value": ["I", "II"]}]}
    {"always": true}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.utils.decimals import decimal_str, to_decimal

SUPPORTED_OPS = {
    "eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte",
    "exists", "not_exists", "truthy", "contains",
}


@dataclass(slots=True)
class EvaluationTrace:
    """Human-readable justification for an applicability decision (FR-07)."""

    passed: bool = True
    checks: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def add(self, description: str, result: bool, expected: Any = None, actual: Any = None) -> None:
        self.checks.append(
            {
                "check": description,
                "result": result,
                "expected": _jsonable(expected),
                "actual": _jsonable(actual),
            }
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def resolve_field(context: dict[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


def evaluate(expression: dict[str, Any] | None, context: dict[str, Any]) -> EvaluationTrace:
    trace = EvaluationTrace()
    if not expression:
        trace.reason = "No applicability expression configured; test treated as applicable."
        return trace
    trace.passed = bool(_walk(expression, context, trace, "root"))
    if trace.passed:
        trace.reason = "All applicability conditions satisfied."
    else:
        failed = [check for check in trace.checks if not check["result"]]
        if failed:
            first = failed[0]
            trace.reason = (
                f"Not applicable: {first['check']} "
                f"(expected {first['expected']!r}, actual {first['actual']!r})"
            )
        else:
            trace.reason = "Not applicable."
    return trace


def _walk(node: dict[str, Any], context: dict[str, Any], trace: EvaluationTrace, label: str) -> bool:
    if not isinstance(node, dict):
        raise ValueError("applicability expression nodes must be objects")

    if "always" in node:
        result = bool(node["always"])
        trace.add(f"{label}: always == {result}", result, node["always"], result)
        return result

    for keyword, joiner in (("all", all), ("any", any)):
        if keyword in node:
            children = node[keyword] or []
            results = [
                _walk(child, context, trace, f"{label}.{keyword}[{index}]")
                for index, child in enumerate(children)
            ]
            combined = joiner(results) if results else True
            trace.add(f"{label}: {keyword.upper()} of {len(results)} condition(s)", combined)
            return combined

    if "not" in node:
        result = not _walk(node["not"], context, trace, f"{label}.not")
        trace.add(f"{label}: NOT", result)
        return result

    path = node.get("field")
    op = node.get("op", "eq")
    if not path:
        raise ValueError("applicability leaf nodes require a 'field'")
    if op not in SUPPORTED_OPS:
        raise ValueError(f"unsupported operator '{op}'")
    actual = resolve_field(context, path)
    expected = node.get("value")
    result = _compare(actual, op, expected)
    description = node.get("description") or f"{path} {op} {expected!r}"
    trace.add(f"{label}: {description}", result, expected, actual)
    return result


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op == "exists":
        return actual is not None
    if op == "not_exists":
        return actual is None
    if op == "truthy":
        return bool(actual)
    if op == "in":
        return actual in (expected or [])
    if op == "not_in":
        return actual not in (expected or [])
    if op == "contains":
        try:
            return expected in actual
        except TypeError:
            return False
    if actual is None:
        return False

    left, right = actual, expected
    if op in {"gt", "gte", "lt", "lte"}:
        left_dec, right_dec = to_decimal(actual), to_decimal(expected)
        if left_dec is not None and right_dec is not None:
            left, right = left_dec, right_dec
    if op == "eq":
        return _loose_equal(left, right)
    if op == "ne":
        return not _loose_equal(left, right)
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    raise ValueError(f"unsupported operator '{op}'")


def _loose_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (Decimal, str)) or isinstance(right, (Decimal, str)):
        left_dec, right_dec = to_decimal(left), to_decimal(right)
        if left_dec is not None and right_dec is not None:
            return left_dec == right_dec
    if isinstance(left, str) and isinstance(right, str):
        return left.strip().lower() == right.strip().lower()
    return left == right
