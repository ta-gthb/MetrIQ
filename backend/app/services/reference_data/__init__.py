"""Reference data: roles, permissions, standards, rules and test catalogue.

The seeding entry points live in the application package rather than in
backend/scripts/ so a deployed instance can provision a fresh database as it
boots - Render's free plan offers neither a pre-deploy command nor a shell.
The scripts remain thin CLIs over these same functions.
"""

from app.services.reference_data.identity import seed_roles_and_permissions
from app.services.reference_data.rules import (
    seed_report_template,
    seed_ruleset,
    seed_test_catalogue,
)

__all__ = [
    "seed_roles_and_permissions",
    "seed_report_template",
    "seed_ruleset",
    "seed_test_catalogue",
]
