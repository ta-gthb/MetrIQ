"""OIML R 76 coverage and rules matrix (project-audit item 8).

The matrix is the traceability artefact that connects the standard to
executable software behaviour: clause -> test -> rule -> calculation ->
compliance -> report section -> automated test. It is generated from the
versioned rule data, the test catalogue and the registered calculators, so it
cannot describe behaviour the code does not have.

``python -m scripts.build_coverage_matrix`` rewrites
``docs/architecture/oiml-coverage-matrix.md``. ``tests/test_coverage_matrix.py``
regenerates it in memory and fails if the committed copy has drifted, which is
what keeps the document honest between releases.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any, Iterable

from app.rules.loader import flatten_rules, load_report_template, load_ruleset, load_test_catalogue
from app.services.calculation_engine.calculators import CALCULATORS

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = REPO_ROOT / "docs" / "architecture" / "oiml-coverage-matrix.md"

RULESET_KEY = "r76-1-2006-v1"
CATALOGUE_KEY = "r76-1-2006-v1"
TEMPLATE_KEY = "r76-2-2007-v1"

COMPARATOR_MEANING = {
    "abs_lte": "PASS when the absolute measured value is less than or equal to the limit",
    "lte": "PASS when the measured value is less than or equal to the limit",
    "gte": "PASS when the measured value is greater than or equal to the limit",
    "eq": "PASS when the measured value equals the limit",
    "range": "PASS when the measured value lies inside the permitted range",
}

#: Rules carried in the ruleset that no calculator reads. Each one must say why
#: it is there and what actually decides the result, so an unused rule shows up
#: as a decision a reviewer can check instead of looking like coverage.
DESCRIPTIVE_ONLY: dict[str, str] = {
    "eccentricity_mpe": (
        "Not read by the engine: the eccentricity calculator resolves the MPE table"
        " directly for the applied load (`R76-MPE-<class>-<band>`). The entry documents"
        " the comparison the calculator performs."
    ),
}

#: Which records own each report section. The second column answers the audit
#: question "can every displayed value be traced back to stored structured
#: data?" for that section.
REPORT_PROVENANCE: dict[str, tuple[str, str]] = {
    "cover": (
        "`config.settings` + `evaluation_cases`",
        "Deployment settings (`REPORT_LABORATORY_NAME/CODE`) and the case title and"
        " purpose; not typed at report time.",
    ),
    "applicant": (
        "`applicants` via `evaluation_cases.applicant_id`",
        "Master record attached to the case.",
    ),
    "instrument": (
        "`instruments` + `instrument_ranges`",
        "Instrument record with its range/interval table; the scale intervals the engine"
        " used are the values shown here.",
    ),
    "conditions": (
        "`environmental_conditions`",
        "Rows recorded against the case during the evaluation.",
    ),
    "equipment": (
        "`test_equipment_usage` + `test_equipment`",
        "Only equipment linked to the case, with its calibration record.",
    ),
    "summary": (
        "`metrology_service.summarise_case`",
        "Derived from `test_instances.result_status`; never typed by an operator.",
    ),
    "tests": (
        "`test_instances` + `calculation_runs` + `compliance_results`",
        "One block per catalogue entry, using the row detail the deterministic engine"
        " stored.",
    ),
    "checklist": (
        "`test_instances` for the T-CHK-* definitions",
        "Checklist decisions recorded as observations; the aggregate status is computed by"
        " the checklist calculator.",
    ),
    "evidence": (
        "`attachments`",
        "Index of uploaded files with their SHA-256 digests.",
    ),
    "review": (
        "`evaluation_cases` (+ `workflow_actions`)",
        "Named engineer, reviewer and approver with the workflow timestamps. Approval and"
        " hashing only: no digital signature is claimed.",
    ),
    "versions": (
        "`standard_versions`, `rule_versions`, `report_template_versions`",
        "The exact ruleset and template the case was evaluated against, plus the case"
        " revision number.",
    ),
    "verification": (
        "`meta.content_hash` / `meta.verification_code`",
        "SHA-256 over the canonical snapshot; recomputable by anyone holding the report"
        " data.",
    ),
}


def _cell(value: Any) -> str:
    """Render a value for a markdown table cell."""
    if value is None or value == "":
        return "-"
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(item) for item in value)
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return " ".join(text.split())


def _columns(entry: dict) -> str:
    rendered = []
    for column in entry["input_schema"].get("columns", []):
        unit = ", %s" % column["unit"] if column.get("unit") else ""
        rendered.append(
            "%s (%s%s)" % (column.get("label", column.get("key")), column.get("type", "text"), unit)
        )
    return "; ".join(rendered)


def _applicability(expression: dict | None) -> str:
    if not expression or expression.get("always"):
        return "Always applicable"
    checks: list[str] = []

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for keyword in ("all", "any"):
            for child in node.get(keyword) or []:
                walk(child)
        if "not" in node:
            walk(node["not"])
        if "field" in node:
            checks.append("%s %s %s" % (node["field"], node.get("op", "eq"), node.get("value")))

    walk(expression)
    joiner = " AND " if expression.get("all") else " OR "
    return joiner.join(checks) or "Always applicable"


def _validation(rules: dict) -> str:
    parts: list[str] = []
    if rules.get("min_rows") is not None:
        parts.append("at least %s row(s)" % rules["min_rows"])
    if rules.get("max_rows") is not None:
        parts.append("at most %s row(s)" % rules["max_rows"])
    if rules.get("required_fields"):
        parts.append("required: " + ", ".join(rules["required_fields"]))
    if rules.get("non_negative_fields"):
        parts.append("non-negative: " + ", ".join(rules["non_negative_fields"]))
    if rules.get("precision_fields"):
        parts.append(
            "decimals: "
            + ", ".join("%s <= %s" % item for item in sorted(rules["precision_fields"].items()))
        )
    if rules.get("max_value") is not None:
        parts.append("maximum %s" % rules["max_value"])
    if rules.get("min_value") is not None:
        parts.append("minimum %s" % rules["min_value"])
    return "; ".join(parts) or "no declarative constraints"


def _formula(entry: dict) -> str:
    calculator = CALCULATORS.get(entry["test_code"])
    method = entry["calculation_rules"].get("method", "-")
    if calculator is None:
        return "%s (no calculator registered)" % method
    if "<locals>" in calculator.__qualname__:
        # A factory-built calculator (the shared deviation/checklist shapes); name
        # the factory, because that is what a reviewer opens.
        factory = calculator.__qualname__.split(".<locals>", 1)[0]
        return "%s -> `%s.py::%s` (shared shape)" % (
            method,
            calculator.__module__.rsplit(".", 1)[-1],
            factory,
        )
    return "%s -> `%s.py::%s`" % (
        method,
        calculator.__module__.rsplit(".", 1)[-1],
        calculator.__name__,
    )


def _limit(ruleset: dict, entry: dict) -> str:
    source = (entry.get("compliance_rules") or {}).get("limit_source", "-")
    if source == "mpe":
        return "OIML R 76-1 Table 1 MPE for the instrument class and load (%s)" % (
            ruleset["mpe"].get("clause_reference")
        )
    if isinstance(source, str) and source.startswith("tolerance:"):
        key = source.split(":", 1)[1]
        tol = (ruleset.get("tolerances") or {}).get(key)
        if tol is None:
            return "tolerance `%s` - NOT PRESENT IN THE RULESET" % key
        return "%s %s (%s)" % (tol.get("factor"), tol.get("unit"), key)
    return _cell(source)


def _pass_fail(entry: dict) -> str:
    comparator = (entry.get("compliance_rules") or {}).get("comparator", "abs_lte")
    return "%s (`%s`)" % (COMPARATOR_MEANING.get(comparator, comparator), comparator)


def _automated_test(identifiers: Iterable[str], boundary_cases: dict[str, str]) -> str:
    for identifier in identifiers:
        if identifier and identifier in boundary_cases:
            return "`%s`" % boundary_cases[identifier]
    return "-"


def _catalogue_entries(catalogue: dict) -> list[dict]:
    return list(catalogue["tests"]) + list(catalogue.get("phase2_tests", []))


def _uses_tolerance(entry: dict, key: str) -> bool:
    compliance = entry.get("compliance_rules") or {}
    calculation = entry.get("calculation_rules") or {}
    return (
        compliance.get("limit_source") == "tolerance:%s" % key
        or calculation.get("tolerance_key") == key
    )


def build_matrix(boundary_cases: dict[str, str] | None = None) -> str:
    """Render the coverage and rules matrix as markdown."""
    if boundary_cases is None:
        boundary_cases = _load_boundary_cases()
    ruleset = load_ruleset(RULESET_KEY)
    catalogue = load_test_catalogue(CATALOGUE_KEY)
    template = load_report_template(TEMPLATE_KEY)
    entries = _catalogue_entries(catalogue)

    version = ruleset["version_label"]
    template_label = template["version_label"]
    lines: list[str] = []
    add = lines.append

    add("# OIML R 76 coverage and rules matrix")
    add("")
    add(
        "Generated from the versioned rule data, the test catalogue and the registered"
        " calculators. Regenerate with:"
    )
    add("")
    add("```powershell")
    add("cd backend")
    add("python -m scripts.build_coverage_matrix")
    add("```")
    add("")
    add("| | |")
    add("| --- | --- |")
    add("| Standard | %s |" % _cell(ruleset.get("standard")))
    add("| Edition | %s |" % _cell(ruleset.get("edition")))
    add("| Ruleset version | `%s` |" % _cell(version))
    add("| Report template | `%s` (%s) |" % (_cell(template_label), _cell(template.get("standard"))))
    add("| Activation | Inactive after deployment; only a System Administrator can activate or deactivate it. |")
    add(
        "| Rule source | Definitions and clause references are versioned with this ruleset."
        " The numeric boundary cases that back each rule live in `tests/test_rule_boundaries.py`. |"
    )
    add("")
    add(
        "The matrix is versioned with the ruleset: a new standard version produces a new set"
        " of rows, and historical cases stay bound to the version they were evaluated"
        " against."
    )
    add("")

    # ------------------------------------------------------------ tests
    add("## 1. Test coverage")
    add("")
    add(
        "One row per catalogue entry. *Implementation* is the catalogue's own"
        " `implementation_status`: a procedure that is not executable is listed with its"
        " reason, never removed from the table."
    )
    add("")
    add(
        "| Test ID | Clause | Test | Applicability | Inputs | Validation | Formula |"
        " MPE / limit | PASS / FAIL logic | Report section | Implementation | Automated test |"
    )
    add("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for entry in entries:
        mapping = entry["report_section_mapping"]
        implementation = entry["implementation_status"]
        if entry.get("unsupported_reason"):
            implementation += ": " + entry["unsupported_reason"]
        add(
            "| `%s` | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
            % (
                _cell(entry["test_code"]),
                _cell(entry["clause_reference"]),
                _cell(entry["name"]),
                _cell(_applicability(entry["applicability_expression"])),
                _cell(_columns(entry)),
                _cell(_validation(entry["validation_rules"])),
                _cell(_formula(entry)),
                _cell(_limit(ruleset, entry)),
                _cell(_pass_fail(entry)),
                _cell("%s %s" % (mapping["number"], mapping["title"])),
                _cell(implementation),
                _automated_test([entry["test_code"]], boundary_cases),
            )
        )
    add("")

    # ------------------------------------------------------------ rules
    add("## 2. Rule coverage")
    add("")
    add(
        "One row per rule version in the ruleset. A rule present in the ruleset but missing"
        " from this table does not exist; a rule listed without a calculation owner is the"
        " defect this table is meant to catch."
    )
    add("")
    add(
        "| Rule ID | Clause | Rule | Definition / formula | Threshold | Unit | Rounding |"
        " Used by | Automated test | Activation |"
    )
    add("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for rule in flatten_rules(ruleset):
        if rule["category"] != "mpe":
            continue
        add(
            "| `%s` | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
            % (
                _cell(rule["code"]),
                _cell(rule.get("clause_reference")),
                _cell(rule.get("name")),
                _cell(rule.get("formula")),
                _cell(_band_summary(rule.get("definition") or {})),
                _cell(rule.get("unit")),
                _cell(rule.get("rounding_policy")),
                "every test whose limit source is the MPE table (section 1)",
                _automated_test(
                    ["MPE-BANDS", "MPE-IN-SERVICE", "MPE-ZERO", rule["code"]], boundary_cases
                ),
                "controlled by ruleset activation",
            )
        )
    for key, tol in sorted((ruleset.get("tolerances") or {}).items()):
        users = [entry["test_code"] for entry in entries if _uses_tolerance(entry, key)]
        used_by = [("`%s`" % code) for code in users] or DESCRIPTIVE_ONLY.get(key)
        canonical = "R76-%s" % key.upper().replace("_", "-")
        engine_rule_id = tol.get("rule_id")
        registered = "`%s`" % canonical + (
            " (engine `%s`)" % engine_rule_id if engine_rule_id else ""
        )
        add(
            "| %s | %s | %s | limit = factor x %s | %s | %s | ROUND_HALF_UP at display"
            " resolution only | %s | %s | %s |"
            % (
                _cell(registered),
                _cell(tol.get("clause_reference")),
                _cell(tol.get("description") or key),
                _cell(tol.get("unit")),
                _cell(tol.get("factor")),
                _cell(tol.get("unit")),
                _cell(used_by),
                _automated_test([tol.get("rule_id"), "R76-%s" % key.upper().replace("_", "-")], boundary_cases),
                "controlled by ruleset activation",
            )
        )
    add("")

    # ------------------------------------------------------- MPE bands
    add("## 3. MPE bands")
    add("")
    add("Every band the engine can resolve, per instrument class and verification stage.")
    add("")
    add("| Class | Stage | Band | m = load / e | Factor | Unit | Clause | Automated test |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for stage, key in (("verification", "bands"), ("in service", "in_service_bands")):
        for instrument_class, bands in sorted((ruleset["mpe"].get(key) or {}).items()):
            for index, band in enumerate(bands, start=1):
                upper = band.get("max_m")
                span = "%s < m <= %s" % (band.get("min_m", 0), "inf" if upper is None else upper)
                case = (
                    "MPE-BANDS" if stage == "verification" else "MPE-IN-SERVICE"
                )
                add(
                    "| %s | %s | %d | %s | %s | %s | %s | %s |"
                    % (
                        _cell(instrument_class),
                        _cell(stage),
                        index,
                        _cell(span),
                        _cell(band.get("factor")),
                        _cell(band.get("unit") or ruleset["mpe"].get("unit")),
                        _cell(band.get("clause_reference")),
                        _automated_test([case], boundary_cases),
                    )
                )
    at_zero = ruleset["mpe"].get("at_zero") or {}
    if at_zero:
        add(
            "| %s | verification | 0 | m = 0 | %s | %s | %s | %s |"
            % (
                _cell(", ".join(at_zero.get("applies_to") or ["all"])),
                _cell(at_zero.get("factor")),
                _cell(at_zero.get("unit")),
                _cell(at_zero.get("clause_reference")),
                _automated_test(["MPE-ZERO", at_zero.get("rule_id")], boundary_cases),
            )
        )
    add("")

    # ------------------------------------------- report section provenance
    add("## 4. Report section provenance")
    add("")
    add(
        "Answers the audit question *is any report field populated by an unexplained or"
        " manually typed result?* Every section of `%s` names the records it is built from."
        % _cell(template_label)
    )
    add("")
    add("| Section | No. | Title | Required | Populated by | Sourced from |")
    add("| --- | --- | --- | --- | --- | --- |")
    for section in template.get("sections", []):
        owner, explanation = REPORT_PROVENANCE.get(section["key"], ("-", "-"))
        add(
            "| `%s` | %s | %s | %s | %s | %s |"
            % (
                _cell(section["key"]),
                _cell(section.get("number")),
                _cell(section.get("title")),
                "yes" if section.get("required") else "no",
                _cell(owner),
                _cell(explanation),
            )
        )
    add("")
    return "\n".join(lines)


def _band_summary(definition: dict) -> str:
    bands = definition.get("bands")
    if not bands:
        return definition.get("factor") and "%s %s" % (
            definition.get("factor"),
            definition.get("unit", "e"),
        ) or "-"
    return "%s bands for class %s (%s)" % (
        len(bands),
        definition.get("instrument_class"),
        definition.get("stage"),
    )


def _load_boundary_cases() -> dict[str, str]:
    try:
        from tests.test_rule_boundaries import BOUNDARY_CASES
    except Exception:  # the matrix is still useful without the test map
        return {}
    return dict(BOUNDARY_CASES)


def main(argv: Iterable[str] | None = None) -> int:
    text = build_matrix() + "\n"
    MATRIX_PATH.parent.mkdir(parents=True, exist_ok=True)
    io.open(MATRIX_PATH, "w", encoding="utf-8", newline="\n").write(text)
    print("wrote %s (%d bytes)" % (MATRIX_PATH, len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
