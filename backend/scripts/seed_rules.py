"""Seed standards, versioned rules, the test catalogue and report templates.

    python backend/scripts/seed_rules.py

Idempotent. The application runs the same routine at start-up when
AUTO_SEED_REFERENCE is enabled, so a deployed instance is usable without a
release step; the functions live in app.services.reference_data.rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._bootstrap import banner, ok, warn  # noqa: E402

from app.database import session_scope  # noqa: E402
from app.models import StandardVersion, TestDefinition  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from app.services.reference_data.rules import (  # noqa: E402
    seed_report_template,
    seed_new_rulesets,
    seed_ruleset,
    seed_test_catalogue,
)

__all__ = ["seed_report_template", "seed_new_rulesets", "seed_ruleset", "seed_test_catalogue", "main"]


def main() -> int:
    banner("MetrIQ - seed standards, rules and test catalogue")
    with session_scope() as db:
        versions = seed_new_rulesets(db)
        standard_version = db.execute(
            select(StandardVersion).where(StandardVersion.version_label == "r76-1-2006-v1")
        ).scalars().one()
        template_version = seed_report_template(db, standard_version)
        definitions = db.execute(select(func.count()).select_from(TestDefinition)).scalar_one()
    ok(f"rulesets discovered: {len(versions)} new version(s)")
    ok(f"standard version {standard_version.version_label}: {'active' if standard_version.is_active else 'inactive'}")
    ok(f"report template {template_version.version_label}: {template_version.id}")
    ok(f"test catalogue: {definitions} definitions")
    warn("New rulesets remain inactive until a System Administrator activates them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
