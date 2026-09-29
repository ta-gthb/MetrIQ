"""Row-level security for laboratory data (project-audit item 14).

Application-layer RBAC (`app/security/scope.py`) decides what a *request* may
see. This module adds the second layer: PostgreSQL policies on the tables that
hold laboratory data, so a connection that is not the schema owner cannot read
across laboratories even if it never goes through the API.

Two facts about how this is deployed matter:

* **Who the policies apply to.** PostgreSQL exempts the owner of a table from
  its policies unless the table is marked `FORCE ROW LEVEL SECURITY`. MetrIQ's
  backend connects as the schema owner (the Supabase `postgres` role), so the
  API keeps working unchanged; the policies constrain every *other* role - the
  `anon` / `authenticated` roles that Supabase's PostgREST issues, a reporting
  user, or a future least-privilege application role.
* **Where the laboratory comes from.** A policy reads
  `current_setting('metriq.lab_id')`, which is set per request by
  :func:`apply_session_scope`. An unset setting is `NULL`, and `NULL` matches
  nothing, so the default is deny.

Trusted backend operations are deliberately *not* given an escape hatch inside
the policies: the owner role bypasses them by design, which is the standard
"service role" separation. Adding a `metriq.service` flag that any role could
set would turn defense-in-depth into a single setting away from no defense.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

#: GUC that carries the laboratory a connection is allowed to see.
LAB_SETTING = "metriq.lab_id"

#: Policies and the setting are only meaningful on PostgreSQL.
POLICY_NAME = "laboratory_scope"

#: Tables that carry the laboratory themselves.
DIRECT_TENANT_TABLES: dict[str, str] = {
    "evaluation_cases": "laboratory_id",
    "users": "laboratory_id",
    "test_equipment": "laboratory_id",
    "audit_logs": "laboratory_id",
}

#: Tables that reach the laboratory through `evaluation_cases.id`.
CASE_SCOPED_TABLES: dict[str, str] = {
    "case_assignments": "case_id",
    "environmental_conditions": "case_id",
    "test_equipment_usage": "case_id",
    "attachments": "case_id",
    "workflow_actions": "case_id",
    "ai_events": "case_id",
    "generated_reports": "case_id",
    "test_instances": "case_id",
}

#: Tables that reach it through `test_instances.id`.
TEST_INSTANCE_SCOPED_TABLES: dict[str, str] = {
    "test_observations": "test_instance_id",
    "calculation_runs": "test_instance_id",
    "compliance_results": "test_instance_id",
    "manual_overrides": "test_instance_id",
}

#: Tables that reach it through `generated_reports.id`.
REPORT_SCOPED_TABLES: dict[str, str] = {
    "report_revisions": "report_id",
}

#: Tables that reach it through `attachments.id`.
ATTACHMENT_SCOPED_TABLES: dict[str, str] = {
    "attachment_links": "attachment_id",
}

TENANT_TABLES: dict[str, str] = {
    **DIRECT_TENANT_TABLES,
    **CASE_SCOPED_TABLES,
    **TEST_INSTANCE_SCOPED_TABLES,
    **REPORT_SCOPED_TABLES,
    **ATTACHMENT_SCOPED_TABLES,
}


def current_lab_expression() -> str:
    """The SQL that reads the laboratory of the current connection."""
    return "nullif(current_setting('%s', true), '')::uuid" % LAB_SETTING


def case_in_scope_expression(table: str, column: str) -> str:
    """A case belongs to the connection's laboratory.

    The subquery reads ``evaluation_cases``, which has its own policy; that is
    harmless here (both predicates say the same thing) and it avoids needing a
    SECURITY DEFINER function, which is where these policies usually acquire a
    privilege-escalation bug.
    """
    return (
        "%s.%s IN (SELECT id FROM evaluation_cases WHERE laboratory_id = %s)"
        % (table, column, current_lab_expression())
    )


def policy_predicate(table: str) -> str:
    """The USING / WITH CHECK expression for one protected table."""
    if table in DIRECT_TENANT_TABLES:
        column = DIRECT_TENANT_TABLES[table]
        return "%s.%s = %s" % (table, column, current_lab_expression())
    if table in CASE_SCOPED_TABLES:
        return case_in_scope_expression(table, CASE_SCOPED_TABLES[table])
    if table in TEST_INSTANCE_SCOPED_TABLES:
        column = TEST_INSTANCE_SCOPED_TABLES[table]
        return (
            "EXISTS (SELECT 1 FROM test_instances ti WHERE ti.id = %s.%s"
            " AND %s)" % (table, column, case_in_scope_expression("ti", "case_id"))
        )
    if table in REPORT_SCOPED_TABLES:
        column = REPORT_SCOPED_TABLES[table]
        return (
            "EXISTS (SELECT 1 FROM generated_reports gr WHERE gr.id = %s.%s"
            " AND %s)" % (table, column, case_in_scope_expression("gr", "case_id"))
        )
    if table in ATTACHMENT_SCOPED_TABLES:
        column = ATTACHMENT_SCOPED_TABLES[table]
        return (
            "EXISTS (SELECT 1 FROM attachments a WHERE a.id = %s.%s"
            " AND %s)" % (table, column, case_in_scope_expression("a", "case_id"))
        )
    raise KeyError(f"{table} is not a laboratory-scoped table")


def policy_statements(schema: str | None = None) -> list[str]:
    """Every statement that installs the policies, in order.

    The migration and the tests both read this list, so the deployed policies
    and the ones the tests exercise cannot drift apart.
    """
    prefix = f"{schema}." if schema else ""
    statements: list[str] = []
    for table in sorted(TENANT_TABLES):
        predicate = policy_predicate(table)
        qualified = f"{prefix}{table}"
        statements.append(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        statements.append(
            "DROP POLICY IF EXISTS %s ON %s" % (POLICY_NAME, qualified)
        )
        statements.append(
            "CREATE POLICY %s ON %s FOR ALL USING (%s) WITH CHECK (%s)"
            % (POLICY_NAME, qualified, predicate, predicate)
        )
    return statements


def drop_policy_statements(schema: str | None = None) -> list[str]:
    prefix = f"{schema}." if schema else ""
    return [
        "DROP POLICY IF EXISTS %s ON %s%s" % (POLICY_NAME, prefix, table)
        for table in sorted(TENANT_TABLES)
    ]


def apply_session_scope(session: Session, laboratory_id: uuid.UUID | str | None) -> None:
    """Bind the connection to a laboratory for the current transaction.

    A no-op on SQLite (used by the demonstration deployment and the test suite)
    and for a principal without a laboratory, such as the platform super admin,
    who is authorised by the application layer instead.
    """
    if laboratory_id is None:
        return
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT set_config(:setting, :value, true)"),
        {"setting": LAB_SETTING, "value": str(laboratory_id)},
    )


def describe() -> dict[str, Any]:
    """A small summary for diagnostics and the health endpoint."""
    return {
        "policy": POLICY_NAME,
        "setting": LAB_SETTING,
        "direct": sorted(DIRECT_TENANT_TABLES),
        "via_case": sorted(CASE_SCOPED_TABLES),
        "via_test_instance": sorted(TEST_INSTANCE_SCOPED_TABLES),
        "via_report": sorted(REPORT_SCOPED_TABLES),
        "via_attachment": sorted(ATTACHMENT_SCOPED_TABLES),
    }


__all__ = [
    "ATTACHMENT_SCOPED_TABLES",
    "CASE_SCOPED_TABLES",
    "DIRECT_TENANT_TABLES",
    "LAB_SETTING",
    "POLICY_NAME",
    "REPORT_SCOPED_TABLES",
    "TENANT_TABLES",
    "TEST_INSTANCE_SCOPED_TABLES",
    "apply_session_scope",
    "case_in_scope_expression",
    "current_lab_expression",
    "describe",
    "drop_policy_statements",
    "policy_predicate",
    "policy_statements",
]
