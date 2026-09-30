"""Readiness, re-tests and the answer to "why this result?" (audit item 16).

Two questions a reviewer asks before acting on a case, answered from the same
records the engine used:

* **Is this case ready to be submitted?** Every applicable test resolved, its
  observations complete enough to calculate, its evidence attached, its
  conditions recorded. The answer is a list of what is missing rather than a
  boolean, so the person who has to fix it knows where to look.
* **Why is this test PASS or FAIL?** The rule, the clause, the governing row,
  the measured value against its limit, and the steps in between - all of it
  read back from the stored calculation run, never recomputed for display.

A re-test is offered here rather than as an edit: the superseded record keeps
its observations and its result and the replacement is linked to it, so the
history of what was measured survives the correction.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CaseStatus,
    EvaluationCase,
    TestDefinition,
    TestImplementationStatus,
    TestInstance,
    TestResultStatus,
)
from app.services import audit_service, equipment_service
from app.services.attachment_service.service import evidence_requirements
from app.services.metrology_service import (
    evaluate_test_instance,
    ruleset_for_standard_version,
    summarise_case,
)

#: A result that is not an answer yet.
UNRESOLVED = {
    TestResultStatus.PENDING,
    TestResultStatus.INCOMPLETE,
    TestResultStatus.INVALID,
}

#: A result that is final for its revision.
RESOLVED = {
    TestResultStatus.PASS,
    TestResultStatus.FAIL,
    TestResultStatus.NOT_APPLICABLE,
    TestResultStatus.WAIVED,
}

#: Cases in these states accept new measurements, so a re-test may be started.
RETESTABLE_STATUSES = {
    CaseStatus.DRAFT,
    CaseStatus.ASSIGNED,
    CaseStatus.IN_PROGRESS,
    CaseStatus.CORRECTION_REQUIRED,
}


def live_tests(case: EvaluationCase) -> list[TestInstance]:
    """The current revision of every test in the case, in plan order.

    A superseded record is kept for history and never counted: its replacement
    carries the result. Ordering falls back to creation time and then identity,
    so the list is stable when two tests share a sequence number.
    """
    live = [test for test in case.tests if test.superseded_at is None]
    live.sort(key=lambda item: (item.sequence_no or 0, item.created_at, str(item.id)))
    return live


def superseded_tests(case: EvaluationCase) -> list[TestInstance]:
    rows = [test for test in case.tests if test.superseded_at is not None]
    rows.sort(key=lambda item: (item.superseded_at, str(item.id)))
    return rows


def test_revisions(case: EvaluationCase, definition_id) -> list[TestInstance]:
    """Every revision of one test, oldest first."""
    rows = [test for test in case.tests if test.test_definition_id == definition_id]
    rows.sort(key=lambda item: (item.revision_no or 1, item.created_at, str(item.id)))
    return rows


def can_retest(case: EvaluationCase, test: TestInstance) -> bool:
    """Whether a new revision may be started for this test record."""
    if test.superseded_at is not None:
        return False
    if case.status not in RETESTABLE_STATUSES:
        return False
    return not test.is_waived


def outstanding_inputs(db: Session, case: EvaluationCase, test: TestInstance) -> dict | None:
    """What the engine still needs for this test, without storing anything.

    A dry run, so read-only callers - the readiness page, the explanation of a
    test that has not been calculated yet - can say what is missing without
    writing a calculation run that would look like a real one.
    """
    evaluation = evaluate_test_instance(db, case=case, test_instance=test, persist=False)
    outcome = evaluation.outcome
    if outcome.is_valid:
        return None
    return {
        "status": outcome.status,
        "errors": list(outcome.errors),
        "explanation": outcome.explanation or None,
    }


def _environment_state(case: EvaluationCase) -> dict:
    return {
        "recorded": len(case.conditions),
        "labels": [condition.label for condition in case.conditions],
    }


def case_readiness(db: Session, case: EvaluationCase) -> dict:
    """Everything standing between this case and a technical review."""
    tests = live_tests(case)
    applicable = [test for test in tests if test.applicability_status != "NOT_APPLICABLE"]
    completed = [test for test in applicable if test.status == "COMPLETED"]

    blocking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    missing_data: list[dict[str, Any]] = []

    for test in tests:
        code = test.definition.test_code if test.definition else "?"
        name = test.definition.name if test.definition else None
        if test.applicability_status == "NOT_APPLICABLE":
            if not test.applicability_reason:
                blocking.append(
                    {
                        "code": code,
                        "kind": "applicability",
                        "message": f"{code} is marked not applicable without a reason.",
                    }
                )
            continue

        if test.result_status in UNRESOLVED:
            outstanding = outstanding_inputs(db, case, test)
            entry: dict[str, Any] = {"code": code, "name": name, "revision_no": test.revision_no}
            if outstanding is None:
                entry["missing"] = []
                entry["hint"] = "The inputs are complete; the test still has to be calculated."
            else:
                entry["missing"] = outstanding["errors"]
                entry["hint"] = "Complete these inputs, then calculate the test."
            missing_data.append(entry)

        if test.status != "COMPLETED":
            blocking.append(
                {
                    "code": code,
                    "kind": "status",
                    "message": f"{code} is {test.status.lower().replace('_', ' ')}.",
                }
            )
            continue
        if test.result_status == TestResultStatus.FAIL:
            blocking.append(
                {
                    "code": code,
                    "kind": "failed",
                    "message": (
                        f"{code} failed. Correct the instrument or the record, or record an "
                        "approved waiver with a reason, before this case can be submitted."
                    ),
                }
            )
        elif test.result_status in UNRESOLVED:
            blocking.append(
                {
                    "code": code,
                    "kind": "result",
                    "message": f"{code} has not resolved to a result ({test.result_status}).",
                }
            )
        details = (test.compliance_result.details if test.compliance_result else None) or {}
        for warning in details.get("warnings") or []:
            warnings.append({"code": code, "kind": "result", "message": warning})

    evidence = evidence_requirements(db, case)
    if not evidence["satisfied"]:
        blocking.append(
            {
                "code": None,
                "kind": "evidence",
                "message": (
                    "Photographic evidence still outstanding: "
                    + ", ".join(item.replace("_", " ") for item in evidence["missing"])
                    + "."
                ),
            }
        )

    environment = _environment_state(case)
    if not environment["recorded"]:
        warnings.append(
            {
                "code": None,
                "kind": "environment",
                "message": "No environmental conditions have been recorded for this case.",
            }
        )
    equipment = equipment_service.case_equipment(db, case)
    if not equipment["recorded"]:
        warnings.append(
            {
                "code": None,
                "kind": "equipment",
                "message": (
                    "No test equipment has been recorded against this case, so the "
                    "traceability of the reference standards cannot be shown."
                ),
            }
        )
    else:
        # A standard that is out of calibration, withdrawn or uncalibrated is not
        # a warning: the case cannot be submitted on measurements it supports.
        blocking.extend(equipment["blocking"])
        warnings.extend(equipment["warnings"])

    summary = summarise_case(case)
    applicable_count = len(applicable)
    percent = 100 if not applicable_count else round(100 * len(completed) / applicable_count)
    return {
        "case_id": str(case.id),
        "application_no": case.application_no,
        "case_status": case.status,
        "overall_result": summary["overall"],
        "completion": {
            "tests_total": len(tests),
            "applicable": applicable_count,
            "completed": len(completed),
            "pending": applicable_count - len(completed),
            "superseded": len(superseded_tests(case)),
            "percent": percent,
        },
        "ready_for_review": not blocking,
        "blocking": blocking,
        "warnings": warnings,
        "missing_data": missing_data,
        "evidence": evidence,
        "environment": environment,
        "equipment": equipment,
        "summary": summary,
        "plan_scope": plan_scope(db, case),
    }


def _definition_order(definition: TestDefinition) -> tuple:
    return (definition.sequence_no or 0, definition.test_code or "")


def _outside_plan_reason(definition: TestDefinition) -> str:
    """Why a catalogue entry is not part of this case's plan."""
    if definition.implementation_status == TestImplementationStatus.NOT_IMPLEMENTED:
        return "Defined in the catalogue but not implemented in this release."
    if not definition.is_active:
        return (
            "Implemented, but not active in this ruleset version: its limit and "
            "clause reference were added from the standard as a proposal and are "
            "pending metrology review."
        )
    return (
        "Active in the ruleset, but not part of this case's plan - the stored "
        "plan was generated from a different set of definitions."
    )


def plan_scope(db: Session, case: EvaluationCase) -> dict:
    """The plan's scope: what it covers, and what the catalogue defines besides.

    A reader must be able to see that the plan of this evaluation is narrower
    than the catalogue, and why - an unstated gap is the failure this guards
    against. Definitions outside the plan carry the reason and the limits that
    are still proposals.
    """
    definitions = db.execute(
        select(TestDefinition).where(
            TestDefinition.standard_version_id == case.standard_version_id
        )
    ).scalars().all()
    planned = {test.test_definition_id for test in case.tests}
    in_plan: list[dict] = []
    outside_plan: list[dict] = []
    for definition in sorted(definitions, key=_definition_order):
        entry = {
            "code": definition.test_code,
            "name": definition.name,
            "phase": definition.phase,
            "implementation_status": definition.implementation_status,
            "proposed_limits": _pending_review_limits(db, case, definition),
        }
        if definition.id in planned:
            in_plan.append(entry)
        else:
            entry["reason"] = _outside_plan_reason(definition)
            outside_plan.append(entry)
    return {"in_plan": in_plan, "outside_plan": outside_plan}


def _rule_key(definition) -> str | None:
    rules = definition.calculation_rules or {}
    key = rules.get("tolerance_key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    source = (definition.compliance_rules or {}).get("limit_source")
    if isinstance(source, str) and source.startswith("tolerance:"):
        return source.split(":", 1)[1].strip()
    return None


def _pending_review_limits(db: Session, case: EvaluationCase, definition) -> list[dict]:
    """Limits this test is judged against that are still proposals."""
    if definition is None:
        return []
    ruleset, _version = ruleset_for_standard_version(db, case.standard_version_id)
    tolerances = ruleset.get("tolerances") or {}
    keys = [key for key in (_rule_key(definition),) if key]
    pending = []
    for key in keys:
        tolerance = tolerances.get(key) or {}
        if str(tolerance.get("review_status") or "").startswith("pending"):
            pending.append(
                {
                    "key": key,
                    "rule_id": tolerance.get("rule_id"),
                    "clause_reference": tolerance.get("clause_reference"),
                    "factor": tolerance.get("factor"),
                    "unit": tolerance.get("unit"),
                    "description": tolerance.get("description"),
                }
            )
    return pending


def _number(value: Any) -> str:
    from app.utils.decimals import decimal_str

    if value is None:
        return "unknown"
    if isinstance(value, Decimal):
        return decimal_str(value) or "unknown"
    return str(value)


def _steps(intermediates: dict) -> list[dict[str, Any]]:
    """The stored intermediates, as ordered label/value rows."""
    rows = []
    for key, value in intermediates.items():
        if isinstance(value, (list, dict)):
            continue
        rows.append({"key": key, "label": key.replace("_", " "), "value": value})
    return rows


def _why(test: TestInstance, result, intermediates: dict, rows: list[dict]) -> list[str]:
    definition = test.definition
    sentences: list[str] = []
    code = definition.test_code if definition else "?"
    if result is None:
        sentences.append(f"{code} has not been calculated yet.")
        return sentences
    sentences.append(
        f"{code} was evaluated by the {definition.name if definition else 'configured'} "
        f"procedure against {result.rule_id or 'the configured rule'}"
        + (f" ({result.clause_reference})" if result.clause_reference else "")
        + "."
    )
    by_number = {
        row.get("observation_no"): row
        for row in rows
        if isinstance(row, dict) and row.get("observation_no") is not None
    }
    governing = intermediates.get("governing_row")
    if isinstance(governing, dict):
        governing = governing.get("observation_no")
    if governing is not None:
        row = by_number.get(governing)
        if row is not None:
            sentences.append(
                "The governing row is the row with the least margin: observation "
                f"{_number(row.get('observation_no'))}"
                + (f" at load {_number(row.get('load'))}" if row.get("load") is not None else "")
                + f", error {_number(row.get('error'))}, margin {_number(row.get('margin'))}."
            )
        else:
            sentences.append(
                f"The governing row is observation {_number(governing)}, the row with the "
                "least margin against its own limit."
            )
    outside = (
        intermediates.get("failed_rows")
        or intermediates.get("rows_outside_limit")
        or intermediates.get("rows_outside_mpe")
        or []
    )
    if outside:
        labels = [
            f"observation {_number(row.get('observation_no') if isinstance(row, dict) else row)}"
            for row in outside
        ]
        sentences.append("Rows outside their limit: " + ", ".join(labels) + ".")
    if result.measured_value is not None or result.limit_value is not None:
        sentences.append(
            f"The recorded value is {_number(result.measured_value)} against a limit of "
            f"{_number(result.limit_value)}, a margin of {_number(result.margin)}"
            + (f" {result.unit}" if result.unit else "")
            + "."
        )
    sentences.append(f"Result: {result.status}.")
    return sentences


def _investigation(test: TestInstance, result) -> list[str]:
    """What to check next, given the result."""
    if result is None:
        return ["Calculate the test to obtain a result."]
    if result.status == TestResultStatus.FAIL:
        return [
            "Re-read the observation rows above against the raw record and the instrument display.",
            "Correct the observation entries if they are wrong; a correction is a new calculation, and the previous one stays in the history.",
            "If the record is right, the instrument is outside the maximum permissible error: a re-test supersedes this record and keeps it for traceability.",
            "A failed type evaluation cannot be submitted as a conforming report; record an approved waiver with a reason if the failure is to be carried.",
        ]
    if result.status in {TestResultStatus.INCOMPLETE, TestResultStatus.INVALID}:
        return ["Complete or correct the inputs listed above, then calculate the test again."]
    if result.status == TestResultStatus.WAIVED:
        return ["An approved waiver applies to this test; the automated result is preserved beside it."]
    if result.status == TestResultStatus.NOT_APPLICABLE:
        return ["The ruleset marked this test not applicable; the reason is recorded with the test."]
    return ["No further action is required for this test."]


def test_explanation(db: Session, case: EvaluationCase, test: TestInstance) -> dict:
    """Why this test has the result it has, from the stored run."""
    definition = test.definition
    result = test.compliance_result
    details = (result.details if result and result.details else None) or {}
    intermediates = details.get("intermediates") or {}
    rows = details.get("rows") or []

    outstanding = None
    if result is None or test.result_status in UNRESOLVED:
        outstanding = outstanding_inputs(db, case, test)

    return {
        "test_instance_id": str(test.id),
        "test_code": definition.test_code if definition else None,
        "name": definition.name if definition else None,
        "clause_reference": definition.clause_reference if definition else None,
        "category": definition.category if definition else None,
        "status": test.status,
        "result_status": test.result_status,
        "revision_no": test.revision_no,
        "applicability": {
            "status": test.applicability_status,
            "reason": test.applicability_reason,
            "trace": test.applicability_trace or [],
        },
        "decision": {
            "status": result.status if result else None,
            "measured_value": result.measured_value if result else None,
            "limit_value": result.limit_value if result else None,
            "margin": result.margin if result else None,
            "unit": result.unit if result else None,
            "rule_id": result.rule_id if result else None,
            "rule_version": result.rule_version if result else None,
            "clause_reference": result.clause_reference if result else None,
            "explanation": result.explanation if result else None,
            "evaluated_at": result.evaluated_at if result else None,
        },
        "method": {
            "calculation_rules": definition.calculation_rules if definition else {},
            "compliance_rules": definition.compliance_rules if definition else {},
            "validation_rules": definition.validation_rules if definition else {},
            "rounding_policy": details.get("rounding_policy")
            or (definition.calculation_rules or {}).get("rounding_policy"),
            "engine_version": details.get("engine_version"),
        },
        "why": _why(test, result, intermediates, rows),
        "steps": _steps(intermediates),
        "rows": rows,
        "warnings": details.get("warnings") or [],
        "errors": details.get("errors") or [],
        "outstanding": outstanding,
        "investigation": _investigation(test, result),
        "pending_review_limits": _pending_review_limits(db, case, definition) if definition else [],
        "retest": {
            "allowed": can_retest(case, test),
            "revision_no": test.revision_no,
            "superseded_at": test.superseded_at,
            "supersedes_test_instance_id": test.supersedes_test_instance_id,
            "superseded_by_test_instance_id": test.superseded_by_test_instance_id,
            "reason": test.retest_reason,
        },
        "revisions": [
            {
                "id": str(item.id),
                "revision_no": item.revision_no,
                "status": item.status,
                "result_status": item.result_status,
                "completed_at": item.completed_at,
                "superseded_at": item.superseded_at,
                "retest_reason": item.retest_reason,
            }
            for item in test_revisions(case, test.test_definition_id)
        ],
    }


def start_retest(
    db: Session,
    *,
    case: EvaluationCase,
    test: TestInstance,
    actor,
    reason: str,
    request_id: str | None = None,
) -> TestInstance:
    """Supersede a test record with a new revision, keeping the old one."""
    if test.superseded_at is not None:
        raise ValueError("This record has already been superseded by a re-test.")
    if case.status not in RETESTABLE_STATUSES:
        raise ValueError(
            f"A re-test cannot be started while the case is {case.status.replace('_', ' ').lower()}."
        )
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValueError("A reason of at least 5 characters is required to start a re-test.")

    revision_no = max(
        (item.revision_no or 1) for item in test_revisions(case, test.test_definition_id)
    ) + 1
    from app.models import utcnow

    now = utcnow()
    test.superseded_at = now
    replacement = TestInstance(
        case_id=case.id,
        test_definition_id=test.test_definition_id,
        sequence_no=test.sequence_no,
        applicability_status=test.applicability_status,
        applicability_reason=test.applicability_reason,
        applicability_trace=test.applicability_trace,
        status="NOT_STARTED",
        result_status=TestResultStatus.PENDING,
        revision_no=revision_no,
        supersedes_test_instance_id=test.id,
        retest_reason=reason,
    )
    db.add(replacement)
    db.flush()
    test.superseded_by_test_instance_id = replacement.id

    audit_service.record(
        db,
        event_type="RETEST",
        entity_type="test_instance",
        entity_id=replacement.id,
        actor=actor,
        case_id=case.id,
        before={"superseded_test_instance_id": str(test.id),
                "superseded_result": test.result_status,
                "revision_no": test.revision_no},
        after={"revision_no": revision_no, "status": replacement.status},
        reason=reason,
        request_id=request_id,
    )
    return replacement