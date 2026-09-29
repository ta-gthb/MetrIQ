"""put laboratory row-level security on the protected tables

Audit item 14. Each protected table gets a single ``laboratory_scope`` policy
that confines a connection to one laboratory, read from
``current_setting('metriq.lab_id')``; the API sets that per request
(``app.security.rls.apply_session_scope``). PostgreSQL exempts a table's owner
from its own policies, so the backend, which connects as the schema owner, is
unaffected while every other role is confined.

SQLite has no row-level security, so this migration does nothing there: the
demonstration deployment and the test suite use it.

The policy list below is frozen on purpose - a migration must keep doing what it
did on the day it was written. ``tests/test_row_level_security.py`` compares it
with ``app.security.rls`` and fails if the two have drifted, which is what
forces a new migration when a protected table is added.

Revision ID: 0007
Revises: 0006
Created: 2026-09-29 22:05:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
# Rendered rather than imported by Alembic: JSONB's astext_type comes out as a
# bare `Text()`, which would be a NameError without this line.
from sqlalchemy import Text  # noqa: F401

from app.config import settings


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

POLICY_NAME = "laboratory_scope"

#: (table, predicate) as of this revision.
POLICIES: tuple[tuple[str, str], ...] = (
    ("ai_events", "ai_events.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid)"),
    ("attachment_links", "EXISTS (SELECT 1 FROM attachments a WHERE a.id = attachment_links.attachment_id AND a.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid))"),
    ("attachments", "attachments.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid)"),
    ("audit_logs", "audit_logs.laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid"),
    ("calculation_runs", "EXISTS (SELECT 1 FROM test_instances ti WHERE ti.id = calculation_runs.test_instance_id AND ti.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid))"),
    ("case_assignments", "case_assignments.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid)"),
    ("compliance_results", "EXISTS (SELECT 1 FROM test_instances ti WHERE ti.id = compliance_results.test_instance_id AND ti.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid))"),
    ("environmental_conditions", "environmental_conditions.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid)"),
    ("evaluation_cases", "evaluation_cases.laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid"),
    ("generated_reports", "generated_reports.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid)"),
    ("manual_overrides", "EXISTS (SELECT 1 FROM test_instances ti WHERE ti.id = manual_overrides.test_instance_id AND ti.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid))"),
    ("report_revisions", "EXISTS (SELECT 1 FROM generated_reports gr WHERE gr.id = report_revisions.report_id AND gr.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid))"),
    ("test_equipment", "test_equipment.laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid"),
    ("test_equipment_usage", "test_equipment_usage.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid)"),
    ("test_instances", "test_instances.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid)"),
    ("test_observations", "EXISTS (SELECT 1 FROM test_instances ti WHERE ti.id = test_observations.test_instance_id AND ti.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid))"),
    ("users", "users.laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid"),
    ("workflow_actions", "workflow_actions.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = nullif(current_setting('metriq.lab_id', true), '')::uuid)"),
)


def _prefix() -> str:
    return f"{settings.DB_SCHEMA}." if settings.DB_SCHEMA else ""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    prefix = _prefix()
    for table, predicate in POLICIES:
        bind.execute(sa.text(f"ALTER TABLE {prefix}{table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {prefix}{table}"))
        bind.execute(
            sa.text(
                f"CREATE POLICY {POLICY_NAME} ON {prefix}{table} FOR ALL"
                f" USING ({predicate}) WITH CHECK ({predicate})"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    prefix = _prefix()
    for table, _predicate in POLICIES:
        bind.execute(sa.text(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {prefix}{table}"))
        bind.execute(sa.text(f"ALTER TABLE {prefix}{table} DISABLE ROW LEVEL SECURITY"))
