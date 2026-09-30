"""Report-consistency checks (audit item 17, PRD 12).

The checker compares the recorded case with the content the report engine will
publish and returns advisory findings only. Nothing here writes a value,
changes a compliance result or takes a decision; every finding cites the report
field it was derived from so a human can trace it.

The checks run locally and deterministically. The context contains applicant
and manufacturer data, so it is never sent to a hosted model.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.services.ai_service.base import ConsistencyFinding, ConsistencyReport
from app.services.attachment_service.service import per_test_evidence
from app.services.report_engine.snapshot import build_report_snapshot

#: Statuses at which the record has already been signed off further down the
#: workflow. A report that is still incomplete at one of these points is
#: internally inconsistent and must be reviewed.
SIGNED_OFF_STATUSES = {"VERIFIED", "APPROVED", "FINALIZED"}
RESOLVED_RESULTS = {"PASS", "FAIL", "WAIVED"}
UNRESOLVED_RESULTS = {None, "PENDING", "INCOMPLETE"}


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _temperature_bounds(text: Any) -> tuple[Decimal, Decimal] | None:
    """Read a declared range such as '-10 C to +40 C' into (low, high)."""
    if not text:
        return None
    numbers = re.findall(r"[-+]?\d+(?:[.,]\d+)?", str(text))
    if len(numbers) < 2:
        return None
    low = _decimal(numbers[0].replace(",", "."))
    high = _decimal(numbers[1].replace(",", "."))
    if low is None or high is None or low >= high:
        return None
    return low, high


def _row_payload(observation) -> dict[str, Any]:
    """Row as the engine sees it, with schema extras from input_payload."""
    row: dict[str, Any] = {
        "observation_no": observation.observation_no,
        "label": observation.position_label,
        "load": observation.load,
        "indication": observation.indication,
        "additional_load": observation.additional_load,
        "elapsed_seconds": observation.elapsed_seconds,
        "temperature_c": observation.temperature_c,
        "value": observation.value,
        "unit": observation.unit,
    }
    for key, value in (observation.input_payload or {}).items():
        row.setdefault(key, value)
    return row


def build_context(db: Session, case) -> dict[str, Any]:
    """Assemble the report content plus the facts the checks need."""
    snapshot, _digest = build_report_snapshot(db, case=case)
    # Keyed by test instance, not test code: a superseded revision of a test
    # must not answer for the live one.
    evidence_state = {
        row["test_instance_id"]: bool(row["satisfied"])
        for row in per_test_evidence(db, case)
    }

    tests: list[dict[str, Any]] = []
    for test in case.tests:
        if test.superseded_at is not None:
            continue
        definition = test.definition
        requirements = definition.evidence_requirements or {}
        result = test.compliance_result
        tests.append(
            {
                "test_code": definition.test_code,
                "name": definition.name,
                "status": test.status,
                "applicability_status": test.applicability_status,
                "result": {
                    "status": result.status if result else None,
                    "rule_id": result.rule_id if result else None,
                    "clause_reference": result.clause_reference if result else None,
                },
                "columns": list((definition.input_schema or {}).get("columns") or []),
                "validation_rules": dict(definition.validation_rules or {}),
                "rows": [
                    _row_payload(observation)
                    for observation in sorted(
                        test.observations, key=lambda item: item.observation_no
                    )
                ],
                "evidence_required": bool(requirements.get("required")),
                "evidence_satisfied": evidence_state.get(str(test.id), False),
            }
        )

    return {
        "meta": snapshot["meta"],
        "case_status": snapshot["review"]["status"],
        "versions": snapshot["versions"],
        "applicant": snapshot["applicant"],
        "manufacturer": snapshot["manufacturer"],
        "instrument": snapshot["instrument"],
        "conditions": snapshot["conditions"],
        "summary": snapshot["summary"],
        "tests": tests,
    }


def run_checks(
    context: dict[str, Any], *, provider: str = "local-deterministic"
) -> ConsistencyReport:
    """Run every deterministic consistency check over the report context."""
    findings: list[ConsistencyFinding] = []
    checks = 0

    def add(
        code: str,
        message: str,
        *,
        severity: str = "warning",
        scope: str = "case",
        test_code: str | None = None,
        citation: str | None = None,
        expected: str | None = None,
        observed: str | None = None,
    ) -> None:
        findings.append(
            ConsistencyFinding(
                code=code,
                message=message,
                severity=severity,
                scope=scope,
                test_code=test_code,
                citation=citation,
                expected=expected,
                observed=observed,
            )
        )

    versions = context.get("versions") or {}
    applicant = context.get("applicant") or {}
    manufacturer = context.get("manufacturer") or {}
    instrument = context.get("instrument") or {}
    conditions = list(context.get("conditions") or [])
    summary = context.get("summary") or {}
    tests = list(context.get("tests") or [])

    # -- report header blocks --------------------------------------------
    checks += 1
    if _blank(applicant.get("name")):
        add(
            "applicant_block_empty",
            "The report's applicant block carries no name.",
            citation="applicant.name",
        )

    checks += 1
    if _blank(manufacturer.get("name")):
        add(
            "manufacturer_block_empty",
            "The report's manufacturer block carries no name.",
            citation="manufacturer.name",
        )

    checks += 1
    if _blank(versions.get("ruleset_label")):
        add(
            "ruleset_version_missing",
            "The report does not reference a rule-set version, so its results cannot be reproduced.",
            citation="versions.ruleset_label",
        )

    checks += 1
    if _blank(versions.get("template_label")):
        add(
            "template_version_missing",
            "The report does not reference a report-template version.",
            citation="versions.template_label",
        )

    # -- instrument ------------------------------------------------------
    checks += 1
    missing_identity = [
        label
        for key, label in (
            ("model", "model"),
            ("serial_number", "serial number"),
            ("type_designation", "type designation"),
        )
        if _blank(instrument.get(key))
    ]
    if missing_identity:
        add(
            "instrument_identity_incomplete",
            "The instrument identity is incomplete: " + ", ".join(missing_identity) + ".",
            citation="instrument",
            expected="model, serial number and type designation recorded",
        )

    maximum = _decimal(instrument.get("max_capacity"))
    checks += 1
    if maximum is None or maximum <= 0:
        add(
            "instrument_max_missing",
            "The instrument has no usable Maximum capacity (Max), so the report cannot anchor its load points.",
            citation="instrument.max_capacity",
        )

    scale_interval = _decimal(instrument.get("e"))
    checks += 1
    if scale_interval is None or scale_interval <= 0:
        add(
            "instrument_e_missing",
            "The instrument has no usable verification scale interval (e).",
            citation="instrument.e",
        )

    actual_interval = _decimal(instrument.get("d"))
    checks += 1
    if scale_interval and actual_interval and actual_interval > scale_interval:
        add(
            "scale_interval_order",
            "The actual scale interval (d) is greater than the verification scale interval (e); "
            "confirm the scale intervals against the applicable R 76 requirement.",
            citation="instrument.d",
            expected="d less than or equal to e",
            observed=f"d = {instrument.get('d')}, e = {instrument.get('e')}",
        )

    minimum_capacity = _decimal(instrument.get("min_capacity"))
    checks += 1
    if minimum_capacity is not None and maximum is not None and minimum_capacity > maximum:
        add(
            "capacity_order",
            "The recorded Min capacity is greater than the recorded Max capacity.",
            citation="instrument.min_capacity",
            expected="Min less than Max",
            observed=f"Min = {instrument.get('min_capacity')}, Max = {instrument.get('max_capacity')}",
        )

    # -- environmental conditions ----------------------------------------
    completed_tests = [test for test in tests if test.get("status") == "COMPLETED"]
    checks += 1
    if completed_tests and not conditions:
        add(
            "conditions_missing",
            "Completed tests exist but the report contains no environmental conditions.",
            citation="conditions",
        )

    declared_range = _temperature_bounds(instrument.get("temperature_range"))
    for index, condition in enumerate(conditions):
        citation = f"conditions[{index}]"
        start = _decimal(condition.get("temperature_c"))
        peak = _decimal(condition.get("max_temperature_c"))
        end = _decimal(condition.get("end_temperature_c"))
        start_humidity = _decimal(condition.get("relative_humidity_pct"))
        peak_humidity = _decimal(condition.get("max_relative_humidity_pct"))
        end_humidity = _decimal(condition.get("end_relative_humidity_pct"))

        checks += 1
        if start is not None and peak is not None and peak < start:
            add(
                "temperature_max_below_start",
                f"The recorded maximum temperature is below the start temperature ({citation}).",
                citation=citation + ".max_temperature_c",
                expected=f">= {condition.get('temperature_c')}",
                observed=condition.get("max_temperature_c"),
            )

        checks += 1
        if peak is not None and end is not None and end > peak:
            add(
                "temperature_end_above_max",
                f"The recorded end temperature is above the maximum temperature ({citation}).",
                citation=citation + ".end_temperature_c",
                expected=f"<= {condition.get('max_temperature_c')}",
                observed=condition.get("end_temperature_c"),
            )

        checks += 1
        if (
            start_humidity is not None
            and peak_humidity is not None
            and peak_humidity < start_humidity
        ):
            add(
                "humidity_max_below_start",
                f"The recorded maximum relative humidity is below the start value ({citation}).",
                citation=citation + ".max_relative_humidity_pct",
                expected=f">= {condition.get('relative_humidity_pct')}",
                observed=condition.get("max_relative_humidity_pct"),
            )

        checks += 1
        if peak_humidity is not None and end_humidity is not None and end_humidity > peak_humidity:
            add(
                "humidity_end_above_max",
                f"The recorded end relative humidity is above the maximum ({citation}).",
                citation=citation + ".end_relative_humidity_pct",
                expected=f"<= {condition.get('max_relative_humidity_pct')}",
                observed=condition.get("end_relative_humidity_pct"),
            )

        if declared_range is not None:
            low, high = declared_range
            off_range = [
                (field, value)
                for field, value in (
                    ("temperature_c", start),
                    ("max_temperature_c", peak),
                    ("end_temperature_c", end),
                )
                if value is not None and (value < low or value > high)
            ]
            checks += 1
            if off_range:
                field, value = off_range[0]
                add(
                    "temperature_outside_declared_range",
                    f"A recorded temperature ({value}) in {citation} lies outside the instrument's "
                    f"declared range ({instrument.get('temperature_range')}).",
                    citation=f"{citation}.{field}",
                    expected=str(instrument.get("temperature_range")),
                    observed=str(value),
                )

    # -- case summary ----------------------------------------------------
    checks += 1
    if summary.get("overall") == "INCOMPLETE" and context.get("case_status") in SIGNED_OFF_STATUSES:
        add(
            "incomplete_at_sign_off",
            f"The case is {context.get('case_status')} while {summary.get('pending')} applicable "
            "test(s) remain unresolved.",
            citation="summary.overall",
            expected="every applicable test resolved before sign-off",
            observed="INCOMPLETE",
        )

    # -- per test --------------------------------------------------------
    codes = [test.get("test_code") for test in tests if test.get("test_code")]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    checks += 1
    for code in duplicates:
        add(
            "duplicate_live_test",
            f"{code} appears more than once among the live tests; the report would list it twice.",
            scope="test",
            test_code=code,
            citation=f"tests[{code}]",
        )

    for test in tests:
        code = test.get("test_code") or "unknown"
        citation = f"tests[{code}]"
        status = test.get("status")
        result = test.get("result") or {}
        validation = test.get("validation_rules") or {}
        rows = list(test.get("rows") or [])
        columns = list(test.get("columns") or [])

        checks += 1
        if status == "COMPLETED" and result.get("status") in UNRESOLVED_RESULTS:
            add(
                "completed_without_result",
                f"{code} is marked complete but the report carries no resolved compliance result.",
                scope="test",
                test_code=code,
                citation=citation + ".result.status",
                observed=None if result.get("status") is None else str(result.get("status")),
            )

        checks += 1
        if (
            result.get("status") in RESOLVED_RESULTS
            and _blank(result.get("rule_id"))
            and _blank(result.get("clause_reference"))
        ):
            add(
                "result_without_rule",
                f"{code} has a recorded result that is not traceable to a rule or clause citation.",
                scope="test",
                test_code=code,
                citation=citation + ".result",
            )

        min_rows = validation.get("min_rows")
        checks += 1
        if isinstance(min_rows, int) and min_rows > 0 and len(rows) < min_rows:
            add(
                "rows_below_minimum",
                f"{code} has {len(rows)} recorded row(s); the procedure requires at least {min_rows}.",
                scope="test",
                test_code=code,
                citation=citation + ".rows",
                expected=f">= {min_rows} rows",
                observed=f"{len(rows)} rows",
            )

        checks += 1
        for requirement in validation.get("required_rows") or []:
            if not isinstance(requirement, dict):
                continue
            target = _decimal(requirement.get("load_equals"))
            if target is None:
                continue
            if any(_decimal(row.get("load")) == target for row in rows):
                continue
            description = requirement.get("description") or f"row with load = {requirement.get('load_equals')}"
            add(
                "required_row_missing",
                f"{code} is missing a required row: {description}.",
                scope="test",
                test_code=code,
                citation=citation + ".rows",
                expected=description,
            )

        required_columns = [column for column in columns if column.get("required")]
        checks += 1
        for index, row in enumerate(rows, start=1):
            missing = [
                column.get("label") or column.get("key")
                for column in required_columns
                if _blank(row.get(column.get("key")))
            ]
            if missing:
                add(
                    "required_value_missing",
                    f"{code} row {index} leaves required field(s) empty: "
                    + ", ".join(str(item) for item in missing)
                    + ".",
                    scope="test",
                    test_code=code,
                    citation=f"{citation}.rows[{index}]",
                )

        row_units = {
            str(row.get("unit")).strip()
            for row in rows
            if not _blank(row.get("unit"))
        }
        instrument_unit = instrument.get("unit")
        checks += 1
        if len(row_units) > 1:
            add(
                "unit_inconsistent",
                f"{code} rows are recorded in more than one unit "
                f"({', '.join(sorted(row_units))}); an aggregate over mixed units is not reliable.",
                scope="test",
                test_code=code,
                citation=citation + ".rows",
            )
        elif row_units and not _blank(instrument_unit):
            row_unit = next(iter(row_units))
            if row_unit != str(instrument_unit).strip():
                add(
                    "unit_mismatch",
                    f"{code} rows are recorded in '{row_unit}' while the instrument is recorded "
                    f"in '{instrument_unit}'.",
                    scope="test",
                    test_code=code,
                    citation=citation + ".rows",
                    expected=str(instrument_unit),
                    observed=row_unit,
                )

        checks += 1
        if maximum is not None and maximum > 0:
            offender = None
            for index, row in enumerate(rows, start=1):
                load = _decimal(row.get("load"))
                if load is not None and load > maximum:
                    offender = (index, load)
                    break
            if offender is not None:
                index, load = offender
                add(
                    "load_above_max",
                    f"{code} row {index} applies a load of {load} above the recorded Max capacity "
                    f"({instrument.get('max_capacity')}).",
                    scope="test",
                    test_code=code,
                    citation=f"{citation}.rows[{index}].load",
                    expected=f"<= {instrument.get('max_capacity')}",
                    observed=str(load),
                )

        checks += 1
        if test.get("evidence_required") and not test.get("evidence_satisfied"):
            add(
                "required_evidence_not_linked",
                f"{code} requires evidence and no attachment is linked to it.",
                scope="test",
                test_code=code,
                citation=citation + ".evidence",
                severity="info",
            )

        checks += 1
        if status == "COMPLETED" and conditions:
            linked = [item for item in conditions if item.get("test_code") == code]
            case_level = [item for item in conditions if not item.get("test_code")]
            if not linked and not case_level:
                add(
                    "test_conditions_missing",
                    f"{code} is complete but no environmental conditions are recorded against it.",
                    scope="test",
                    test_code=code,
                    citation="conditions",
                    severity="info",
                )

    message = (
        f"{checks} consistency check(s) ran; {len(findings)} finding(s) need human review. "
        "Findings are advisory: they never change a result, a compliance outcome or an approval."
    )
    return ConsistencyReport(
        available=True,
        provider=provider,
        findings=findings,
        checks_run=checks,
        message=message,
    )
