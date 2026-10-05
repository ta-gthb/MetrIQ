"""Rule-set diff and impact analysis (audit item 15).

Activating a rule set is a metrological decision, so the reviewer has to see
what changes before it takes effect:

* which rules are added, removed or changed, field by field - including the
  bands of an MPE table;
* which catalogue procedures read the changed rules, derived from the
  catalogue itself (its ``limit_source`` and ``tolerance_key`` settings)
  rather than from a hand-maintained list;
* how many reports were generated under each rule set and how many cases are
  still open against them, so the effect on history is stated, not guessed.

Nothing here computes a result. It reads the versioned rules and answers what
a person would be affected by if the candidate became active.
"""

from __future__ import annotations

from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CaseStatus,
    EvaluationCase,
    GeneratedReport,
    Rule,
    StandardVersion,
    TestDefinition,
)
from app.services import ruleset_lifecycle
from app.services.reference_data.rules import RULESET_RULE_CODE

__all__ = ["diff_rulesets", "impacted_tests"]


def _leaf_differences(before: Any, after: Any, prefix: str = "") -> Iterator[tuple[str, Any, Any]]:
    """Yield (field, before, after) for every leaf that differs.

    The same shape the report comparison uses: dicts recurse, lists are
    compared item by item when their items are dicts (an MPE band list becomes
    band-level changes), and anything else is a leaf.
    """
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            name = f"{prefix}.{key}" if prefix else str(key)
            yield from _leaf_differences(before.get(key), after.get(key), name)
        return
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        if all(isinstance(item, dict) for item in before + after):
            for index, (old, new) in enumerate(zip(before, after)):
                yield from _leaf_differences(old, new, f"{prefix}[{index + 1}]")
            return
    if before != after:
        yield prefix, before, after


def _comparable(version) -> dict:
    return {
        "clause_reference": version.rule.clause_reference,
        "definition": version.definition or {},
        "formula": version.formula,
        "threshold": version.threshold,
        "unit": version.unit,
        "applicability": version.applicability,
        "rounding_policy": version.rounding_policy,
    }


def _catalogue_dependencies(db: Session, version: StandardVersion) -> list[dict]:
    """What each catalogue procedure reads from the rule set.

    ``limit_source`` is either ``mpe`` or ``tolerance:<key>``; a procedure may
    also name a ``tolerance_key`` in its calculation rules. That is the whole
    contract, so the dependency map is derived from it.
    """
    rows = db.execute(
        select(TestDefinition).where(TestDefinition.standard_version_id == version.id)
    ).scalars().all()
    dependencies = []
    for definition in rows:
        calculation = definition.calculation_rules or {}
        compliance = definition.compliance_rules or {}
        source = compliance.get("limit_source")
        keys = set()
        key = calculation.get("tolerance_key")
        if isinstance(key, str) and key.strip():
            keys.add(key.strip())
        if isinstance(source, str) and source.startswith("tolerance:"):
            keys.add(source.split(":", 1)[1].strip())
        dependencies.append(
            {
                "test_code": definition.test_code,
                "name": definition.name,
                "tolerance_keys": sorted(keys),
                "uses_mpe": source == "mpe",
            }
        )
    return dependencies


def _impacts(dependencies: list[dict], rule: Rule) -> list[dict]:
    """The catalogue procedures, out of ``dependencies``, that read this rule."""
    hits = []
    for dependency in dependencies:
        reason = None
        if rule.code == RULESET_RULE_CODE:
            reason = "the whole rule set is versioned"
        elif rule.code.startswith("R76-MPE"):
            if dependency["uses_mpe"]:
                reason = "judged against the MPE bands"
        else:
            for key in dependency["tolerance_keys"]:
                if rule.code == f"R76-{key.upper().replace('_', '-')}":
                    reason = f"judged against tolerance '{key}'"
                    break
        if reason:
            hits.append(
                {"test_code": dependency["test_code"], "name": dependency["name"], "reason": reason}
            )
    return hits


def impacted_tests(db: Session, version: StandardVersion, rule: Rule) -> list[dict]:
    """The catalogue procedures that read this rule."""
    return _impacts(_catalogue_dependencies(db, version), rule)


def _scope(db: Session, version: StandardVersion) -> dict:
    reports = db.execute(
        select(GeneratedReport).where(GeneratedReport.standard_version_id == version.id)
    ).scalars().all()
    open_cases = db.execute(
        select(EvaluationCase).where(
            EvaluationCase.standard_version_id == version.id,
            EvaluationCase.status.notin_(CaseStatus.IMMUTABLE),
        )
    ).scalars().all()
    return {
        "reports": len(reports),
        "finalized_reports": sum(1 for report in reports if report.is_immutable),
        "open_cases": len(open_cases),
    }


def _version_summary(db: Session, version: StandardVersion) -> dict:
    rows = ruleset_lifecycle.ruleset_rows(db, version)
    return {
        "id": version.id,
        "version_label": version.version_label,
        "edition": version.edition,
        "status": "active" if version.is_active else "inactive",
        "is_active": version.is_active,
        "source_reference": version.source_reference,
        "rule_count": len(rows),
        "fingerprint": ruleset_lifecycle.ruleset_fingerprint(rows),
    }


def diff_rulesets(db: Session, version: StandardVersion, against: StandardVersion) -> dict:
    """Compare the candidate ``version`` against the ``against`` baseline.

    "Added" means present in the candidate only; "removed" means present in the
    baseline only; both are read in the direction a reviewer reads them - what
    would change if the candidate were activated.
    """
    before = {rule.code: row for row, rule in ruleset_lifecycle.ruleset_rows(db, against)}
    after = {rule.code: row for row, rule in ruleset_lifecycle.ruleset_rows(db, version)}

    # The dependency map normally comes from the candidate's own catalogue. A
    # draft that does not carry one yet is read against the baseline catalogue,
    # and the answer says so instead of pretending it had its own.
    candidate_deps = _catalogue_dependencies(db, version)
    baseline_deps = _catalogue_dependencies(db, against) if not candidate_deps else []
    dependencies = candidate_deps or baseline_deps
    catalogue_source = (
        "candidate" if candidate_deps else ("baseline" if baseline_deps else "none")
    )

    rules: list[dict] = []
    impacted: dict[str, dict] = {}
    counts = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
    for code in sorted(set(before) | set(after)):
        old, new = before.get(code), after.get(code)
        if old is None:
            change, fields, hits = "added", [], _impacts(dependencies, new.rule)
        elif new is None:
            change, fields, hits = "removed", [], _impacts(dependencies, old.rule)
        else:
            fields = [
                {"field": field, "before": old_value, "after": new_value}
                for field, old_value, new_value in _leaf_differences(
                    _comparable(old), _comparable(new)
                )
            ]
            change = "changed" if fields else "unchanged"
            hits = _impacts(dependencies, new.rule) if fields else []
        counts[change] += 1
        for hit in hits:
            entry = impacted.setdefault(
                hit["test_code"],
                {"test_code": hit["test_code"], "name": hit["name"], "reasons": []},
            )
            reason = f"{code}: {hit['reason']}"
            if reason not in entry["reasons"]:
                entry["reasons"].append(reason)
        rules.append(
            {
                "code": code,
                "name": (new or old).rule.name,
                "category": (new or old).rule.category,
                "clause_reference": (new or old).rule.clause_reference,
                "change": change,
                "fields": fields,
                "impacted_tests": [hit["test_code"] for hit in hits],
            }
        )

    candidate = _version_summary(db, version)
    return {
        "ruleset": candidate,
        "against": _version_summary(db, against),
        "summary": {**counts, "change_count": counts["added"] + counts["removed"] + counts["changed"]},
        "rules": rules,
        "impact": {
            "tests": sorted(impacted.values(), key=lambda item: item["test_code"]),
            "catalogue_source": catalogue_source,
            "under_this_ruleset": _scope(db, version),
            "under_compared_ruleset": _scope(db, against),
        },
    }
