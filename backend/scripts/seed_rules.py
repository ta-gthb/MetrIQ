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
from app.services.reference_data.bootstrap import (  # noqa: E402
    activate_seeded_ruleset,
)
from app.services.reference_data.rules import (  # noqa: E402
    seed_report_template,
    seed_ruleset,
    seed_test_catalogue,
)

__all__ = ["seed_report_template", "seed_ruleset", "seed_test_catalogue", "main"]


def main() -> int:
    banner("MetrIQ - seed standards, rules and test catalogue")
    with session_scope() as db:
        standard_version = seed_ruleset(db)
        template_version = seed_report_template(db, standard_version)
        definitions = seed_test_catalogue(db, standard_version)
        activation = activate_seeded_ruleset(db, standard_version)
    ok(f"standard version {standard_version.version_label}: {activation['state']}")
    ok(f"report template {template_version.version_label}: {template_version.id}")
    ok(f"test catalogue: {definitions} definitions")
    print()
    if activation.get("provisional"):
        warn("Activated provisionally: the bootstrap may do that outside production,")
        warn("and every surface labels it. It is not a metrology sign-off.")
    else:
        warn("The ruleset is not active. Record a review for every rule, then approve")
        warn("and activate it, before any evaluation case can be created.")
    warn("Every seeded rule carries review_status='pending_domain_review'.")
    warn("A qualified metrology authority must verify the bands, tolerances and clause")
    warn("references against the controlled standard before production use (PRD 25).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
