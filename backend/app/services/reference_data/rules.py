"""Standards, versioned rules, the test catalogue and report templates.

Seeding is idempotent: re-running refreshes the versioned rule data without
duplicating records and never rewrites historical results (PRD 24.1). The
routine is used both by backend/scripts/seed_rules.py and by the application
itself at start-up (see app/services/reference_data/bootstrap.py).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.models import (
    ReportTemplate,
    ReportTemplateVersion,
    Rule,
    RuleVersion,
    Standard,
    StandardVersion,
    TestDefinition,
    TestImplementationStatus,
    utcnow,
)
from app.rules.loader import (  # noqa: E402
    flatten_rules,
    load_report_template,
    load_ruleset,
    load_test_catalogue,
)

logger = logging.getLogger("metriq.reference_data")

RULESET_RULE_CODE = "R76-RULESET"
R76_1 = "OIML R 76-1"
R76_2 = "OIML R 76-2"


def _standard(db, code: str, title: str, description: str, publisher: str = "OIML") -> Standard:
    standard = db.execute(select(Standard).where(Standard.code == code)).scalars().first()
    if standard is None:
        standard = Standard(code=code, title=title, publisher=publisher, description=description)
        db.add(standard)
        db.flush()
    else:
        standard.title = title
        standard.description = description
    return standard


def _standard_version(db, standard: Standard, payload: dict) -> StandardVersion:
    label = payload["version_label"]
    version = db.execute(
        select(StandardVersion).where(
            StandardVersion.standard_id == standard.id,
            StandardVersion.version_label == label,
        )
    ).scalars().first()
    from datetime import date

    effective_from = payload.get("effective_from")
    if version is None:
        version = StandardVersion(
            standard_id=standard.id,
            edition=payload.get("edition", "unknown"),
            version_label=label,
            status=payload.get("status", "draft"),
            effective_from=date.fromisoformat(effective_from) if effective_from else None,
            source_reference=payload.get("source_reference"),
            notes="\n".join(payload.get("notes", [])) or None,
        )
        db.add(version)
        db.flush()
    else:
        version.edition = payload.get("edition", version.edition)
        version.source_reference = payload.get("source_reference")
        version.notes = "\n".join(payload.get("notes", [])) or version.notes
    return version


def _upsert_rule(db, code: str, name: str, category: str, clause: str | None, description: str | None) -> Rule:
    rule = db.execute(select(Rule).where(Rule.code == code)).scalars().first()
    if rule is None:
        rule = Rule(code=code, name=name, category=category, clause_reference=clause,
                    description=description)
        db.add(rule)
        db.flush()
    else:
        rule.name = name
        rule.category = category
        rule.clause_reference = clause
        rule.description = description or rule.description
    return rule


def _upsert_rule_version(db, rule: Rule, standard_version: StandardVersion, entry: dict) -> RuleVersion:
    version = db.execute(
        select(RuleVersion).where(
            RuleVersion.rule_id == rule.id,
            RuleVersion.version_label == entry["version_label"],
        )
    ).scalars().first()
    if version is None:
        version = RuleVersion(
            rule_id=rule.id,
            standard_version_id=standard_version.id,
            version_label=entry["version_label"],
        )
        db.add(version)
    version.definition = entry.get("definition") or {}
    version.formula = entry.get("formula")
    version.threshold = entry.get("threshold")
    version.unit = entry.get("unit")
    version.applicability = entry.get("applicability")
    version.rounding_policy = entry.get("rounding_policy")
    version.review_status = entry.get("review_status", "pending_domain_review")
    db.flush()
    return version


def seed_ruleset(db) -> StandardVersion:
    payload = load_ruleset("r76-1-2006-v1")
    standard = _standard(
        db,
        R76_1,
        "Non-automatic weighing instruments - Part 1: Metrological and technical requirements - Tests",
        "Initial technical baseline for the application's metrological requirements and test procedures.",
    )
    version = _standard_version(db, standard, payload)

    # 1. The rule set as a single, atomically versioned artefact.
    ruleset_rule = _upsert_rule(
        db, RULESET_RULE_CODE, "OIML R 76-1 metrological ruleset", "ruleset",
        (payload.get("mpe") or {}).get("clause_reference"),
        "Complete MPE table and tolerance set used by the deterministic calculation and compliance engines.",
    )
    _upsert_rule_version(
        db,
        ruleset_rule,
        version,
        {
            "version_label": payload["version_label"],
            "definition": payload,
            "formula": "see mpe.formula and tolerances.*",
            "unit": "e",
            "rounding_policy": "Exact decimal arithmetic; ROUND_HALF_UP only when a rule requests rounding",
            "review_status": payload.get("review_status", "pending_domain_review"),
        },
    )

    # 2. Individual rules so a laboratory can inspect and diff each band/tolerance.
    entries = flatten_rules(payload)
    for entry in entries:
        rule = _upsert_rule(
            db, entry["code"], entry["name"], entry["category"], entry.get("clause_reference"),
            (entry.get("definition") or {}).get("description"),
        )
        _upsert_rule_version(db, rule, version, entry)

    # 3. A placeholder future edition, inactive, to prove that versioning is real.
    revision = db.execute(
        select(StandardVersion).where(
            StandardVersion.standard_id == standard.id,
            StandardVersion.version_label == "r76-1-rev-1.1-CD-2024",
        )
    ).scalars().first()
    if revision is None:
        db.add(
            StandardVersion(
                standard_id=standard.id,
                edition="revision 1.1 committee draft",
                version_label="r76-1-rev-1.1-CD-2024",
                status="draft",
                is_active=False,
                source_reference="https://www.oiml.org/en/tc-sc-pg/committee-drafts",
                notes=(
                    "Placeholder for the OIML R 76 revision project (1.1 committee draft circulated "
                    "April 2024). Load its rule definitions before activating; historical cases keep "
                    "the version they were created with."
                ),
            )
        )
    logger.info(
        "ruleset %s: 1 rule set + %s individual rules", version.version_label, len(entries)
    )
    return version


def seed_report_template(db, standard_version: StandardVersion) -> ReportTemplateVersion:
    payload = load_report_template("r76-2-2007-v1")
    standard = _standard(
        db,
        R76_2,
        "Non-automatic weighing instruments - Part 2: Test report format",
        "Defines the type-evaluation report format that the report engine is aligned to (PRD 17.4).",
    )
    _standard_version(db, standard, payload)

    template = db.execute(
        select(ReportTemplate).where(ReportTemplate.code == "R76-2-TYPE-EVAL")
    ).scalars().first()
    if template is None:
        template = ReportTemplate(
            code="R76-2-TYPE-EVAL",
            name="OIML R 76-2 type evaluation report",
            standard_version_id=standard_version.id,
            description="Report structure aligned to OIML R 76-2:2007.",
        )
        db.add(template)
        db.flush()

    version = db.execute(
        select(ReportTemplateVersion).where(
            ReportTemplateVersion.template_id == template.id,
            ReportTemplateVersion.version_label == payload["version_label"],
        )
    ).scalars().first()
    if version is None:
        version = ReportTemplateVersion(
            template_id=template.id,
            version_no=1,
            version_label=payload["version_label"],
            is_active=True,
            activated_at=utcnow(),
        )
        db.add(version)
    version.section_map = payload
    db.flush()
    logger.info(
        "report template %s: %s sections",
        version.version_label, len(payload.get("sections", [])),
    )
    return version


def seed_test_catalogue(db, standard_version: StandardVersion) -> int:
    catalogue = load_test_catalogue("r76-1-2006-v1")
    created = 0
    for entry in catalogue["tests"]:
        created += _upsert_test_definition(db, standard_version, entry, is_active=True, phase="MVP")
    for entry in catalogue.get("phase2_tests", []):
        created += _upsert_test_definition(
            db, standard_version, entry, is_active=False, phase="2",
            description=entry.get("description"),
        )
    logger.info(
        "test catalogue: %s definitions (%s active MVP, %s phase 2 inactive)",
        created, len(catalogue["tests"]), len(catalogue.get("phase2_tests", [])),
    )
    return created


def _upsert_test_definition(
    db, standard_version: StandardVersion, entry: dict, *, is_active: bool, phase: str,
    description: str | None = None,
) -> int:
    definition = db.execute(
        select(TestDefinition).where(
            TestDefinition.test_code == entry["test_code"],
            TestDefinition.standard_version_id == standard_version.id,
        )
    ).scalars().first()
    payload = {
        "name": entry["name"],
        "clause_reference": entry.get("clause_reference"),
        "category": entry.get("category", "metrological"),
        "phase": entry.get("phase", phase),
        "implementation_status": entry.get(
            "implementation_status", TestImplementationStatus.IMPLEMENTED
        ),
        "unsupported_reason": entry.get("unsupported_reason"),
        "description": description or entry.get("description"),
        "applicability_expression": entry.get("applicability_expression") or {},
        "input_schema": entry.get("input_schema") or {},
        "validation_rules": entry.get("validation_rules") or {},
        "calculation_rules": entry.get("calculation_rules") or {},
        "compliance_rules": entry.get("compliance_rules") or {},
        "report_section_mapping": entry.get("report_section_mapping") or {},
        "evidence_requirements": entry.get("evidence_requirements") or {},
        "sequence_no": entry.get("sequence_no", 100),
        "is_active": is_active,
    }
    if definition is None:
        definition = TestDefinition(
            test_code=entry["test_code"], standard_version_id=standard_version.id, **payload
        )
        db.add(definition)
    else:
        for key, value in payload.items():
            setattr(definition, key, value)
    db.flush()
    return 1
