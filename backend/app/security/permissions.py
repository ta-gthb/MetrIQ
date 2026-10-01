"""Permission catalogue and role matrix (PRD Appendix A)."""

from __future__ import annotations

from dataclasses import dataclass


class P:
    """Permission codes."""

    DASHBOARD_VIEW = "dashboard.view"
    USERS_MANAGE = "users.manage"
    USERS_MANAGE_SCOPED = "users.manage.scoped"
    LABS_MANAGE = "laboratories.manage"
    LABS_VIEW = "laboratories.view"
    MASTERS_MANAGE = "masters.manage"
    EQUIPMENT_MANAGE = "equipment.manage"
    RULES_MANAGE = "rules.manage"
    RULES_VIEW = "rules.view"
    # Metrology/domain review of a rule and lifecycle approval of a ruleset
    # (audit items 6 and 10). Deliberately separate from rules.manage: the
    # person who drafts or edits a rule must not be able to sign it off alone.
    RULES_REVIEW = "rules.review"
    RULES_APPROVE = "rules.approve"
    AUDIT_VIEW = "audit.view"
    AUDIT_VIEW_LIMITED = "audit.view.limited"
    AUDIT_VIEW_SCOPE = "audit.view.scope"
    CASES_VIEW = "cases.view"
    CASES_VIEW_SCOPE = "cases.view.scope"
    CASES_CREATE = "cases.create"
    CASES_ASSIGN = "cases.assign"
    CASES_EDIT = "cases.edit"
    CASES_SUBMIT = "cases.submit"
    CASES_REVIEW = "cases.review"
    CASES_REQUEST_CORRECTION = "cases.request_correction"
    CASES_APPROVE = "cases.approve"
    CASES_FINALIZE = "cases.finalize"
    TESTS_EDIT = "tests.edit"
    TESTS_EDIT_OWN = "tests.edit.own"
    TESTS_VALIDATE = "tests.validate"
    TESTS_VIEW_RESULTS = "tests.view_results"
    REPORTS_GENERATE = "reports.generate"
    REPORTS_DOWNLOAD = "reports.download"
    OVERRIDE_REQUEST = "override.request"
    OVERRIDE_APPROVE = "override.approve"
    AI_USE = "ai.use"
    AI_VIEW = "ai.view"
    AI_MANAGE = "ai.manage"
    SETTINGS_MANAGE = "settings.manage"


PERMISSION_CATALOGUE: list[tuple[str, str, str]] = [
    (P.DASHBOARD_VIEW, "View dashboard", "dashboard"),
    (P.USERS_MANAGE, "Manage all users and roles", "administration"),
    (P.USERS_MANAGE_SCOPED, "Manage users within own laboratory", "administration"),
    (P.LABS_MANAGE, "Manage laboratory entities", "administration"),
    (P.LABS_VIEW, "View laboratory information", "administration"),
    (P.MASTERS_MANAGE, "Manage manufacturer and applicant masters", "masters"),
    (P.EQUIPMENT_MANAGE, "Manage test equipment and calibrations", "masters"),
    (P.RULES_MANAGE, "Manage standards, rulesets and templates", "standards"),
    (P.RULES_VIEW, "View standards, rulesets and templates", "standards"),
    (P.RULES_REVIEW, "Record a metrology review of a rule or ruleset", "standards"),
    (P.RULES_APPROVE, "Approve and activate a ruleset", "standards"),
    (P.AUDIT_VIEW, "View audit logs (all laboratories)", "governance"),
    (P.AUDIT_VIEW_SCOPE, "View audit logs within own laboratory", "governance"),
    (P.AUDIT_VIEW_LIMITED, "View case-level audit entries", "governance"),
    (P.CASES_VIEW, "View all evaluation cases", "cases"),
    (P.CASES_VIEW_SCOPE, "View cases within authorized scope", "cases"),
    (P.CASES_CREATE, "Create evaluation cases", "cases"),
    (P.CASES_ASSIGN, "Assign engineer, reviewer and approver", "cases"),
    (P.CASES_EDIT, "Edit any evaluation case", "cases"),
    (P.CASES_SUBMIT, "Submit a case for technical review", "workflow"),
    (P.CASES_REVIEW, "Perform technical review", "workflow"),
    (P.CASES_REQUEST_CORRECTION, "Request corrections", "workflow"),
    (P.CASES_APPROVE, "Approve or reject a case", "workflow"),
    (P.CASES_FINALIZE, "Finalize and lock a report", "workflow"),
    (P.TESTS_EDIT, "Edit tests on any authorized case", "tests"),
    (P.TESTS_EDIT_OWN, "Edit tests on assigned cases", "tests"),
    (P.TESTS_VALIDATE, "Run validation and calculation", "tests"),
    (P.TESTS_VIEW_RESULTS, "View calculated results", "tests"),
    (P.REPORTS_GENERATE, "Generate PDF/DOCX reports", "reports"),
    (P.REPORTS_DOWNLOAD, "Download reports", "reports"),
    (P.OVERRIDE_REQUEST, "Request a manual override of a result", "governance"),
    (P.OVERRIDE_APPROVE, "Approve a manual override", "governance"),
    (P.AI_USE, "Use AI assistance features", "ai"),
    (P.AI_VIEW, "View AI suggestions", "ai"),
    (P.AI_MANAGE, "Configure AI feature toggles", "ai"),
    (P.SETTINGS_MANAGE, "Manage system settings", "administration"),
]


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    code: str
    name: str
    description: str
    rank: int
    permissions: frozenset[str]


SUPER_ADMIN = "SUPER_ADMIN"
LAB_ADMIN = "LAB_ADMIN"
ENGINEER = "ENGINEER"
REVIEWER = "REVIEWER"
APPROVER = "APPROVER"
AUDITOR = "AUDITOR"

ALL_PERMISSIONS = frozenset(code for code, _, _ in PERMISSION_CATALOGUE)

ROLE_DEFINITIONS: dict[str, RoleDefinition] = {
    SUPER_ADMIN: RoleDefinition(
        code=SUPER_ADMIN,
        name="Super Admin",
        description="Platform and configuration control",
        rank=10,
        permissions=ALL_PERMISSIONS,
    ),
    LAB_ADMIN: RoleDefinition(
        code=LAB_ADMIN,
        name="Laboratory Admin / Manager",
        description="Operational management within the laboratory",
        rank=20,
        permissions=frozenset(
            {
                P.DASHBOARD_VIEW,
                P.USERS_MANAGE_SCOPED,
                P.LABS_MANAGE,
                P.LABS_VIEW,
                P.MASTERS_MANAGE,
                P.EQUIPMENT_MANAGE,
                P.RULES_VIEW,
                P.AUDIT_VIEW_SCOPE,
                P.AUDIT_VIEW_LIMITED,
                P.CASES_VIEW,
                P.CASES_VIEW_SCOPE,
                P.CASES_CREATE,
                P.CASES_ASSIGN,
                P.CASES_EDIT,
                P.CASES_SUBMIT,
                P.CASES_REQUEST_CORRECTION,
                P.TESTS_EDIT,
                P.TESTS_EDIT_OWN,
                P.TESTS_VALIDATE,
                P.TESTS_VIEW_RESULTS,
                P.REPORTS_GENERATE,
                P.REPORTS_DOWNLOAD,
                P.OVERRIDE_REQUEST,
                P.AI_USE,
                P.AI_VIEW,
            }
        ),
    ),
    ENGINEER: RoleDefinition(
        code=ENGINEER,
        name="Test Engineer / Metrologist",
        description="Perform type evaluation and record observations",
        rank=30,
        permissions=frozenset(
            {
                P.DASHBOARD_VIEW,
                P.LABS_VIEW,
                P.RULES_VIEW,
                P.AUDIT_VIEW_LIMITED,
                P.CASES_VIEW_SCOPE,
                P.CASES_CREATE,
                P.TESTS_EDIT_OWN,
                P.TESTS_VALIDATE,
                P.TESTS_VIEW_RESULTS,
                P.CASES_SUBMIT,
                P.REPORTS_GENERATE,
                P.REPORTS_DOWNLOAD,
                P.OVERRIDE_REQUEST,
                P.AI_USE,
                P.AI_VIEW,
            }
        ),
    ),
    REVIEWER: RoleDefinition(
        code=REVIEWER,
        name="Technical Reviewer / Verifier",
        description="Independent technical review of calculations and evidence",
        rank=40,
        permissions=frozenset(
            {
                P.DASHBOARD_VIEW,
                P.LABS_VIEW,
                P.RULES_VIEW,
                P.AUDIT_VIEW_SCOPE,
                P.AUDIT_VIEW_LIMITED,
                P.CASES_VIEW,
                P.CASES_VIEW_SCOPE,
                P.CASES_REVIEW,
                P.CASES_REQUEST_CORRECTION,
                P.TESTS_VALIDATE,
                P.TESTS_VIEW_RESULTS,
                P.REPORTS_GENERATE,
                P.REPORTS_DOWNLOAD,
                P.AI_USE,
                P.AI_VIEW,
            }
        ),
    ),
    APPROVER: RoleDefinition(
        code=APPROVER,
        name="Approving Authority",
        description="Final authorization and report finalization",
        rank=50,
        permissions=frozenset(
            {
                P.DASHBOARD_VIEW,
                P.LABS_VIEW,
                P.RULES_VIEW,
                P.AUDIT_VIEW_SCOPE,
                P.AUDIT_VIEW_LIMITED,
                P.CASES_VIEW,
                P.CASES_VIEW_SCOPE,
                P.CASES_REQUEST_CORRECTION,
                P.CASES_APPROVE,
                P.CASES_FINALIZE,
                P.TESTS_VIEW_RESULTS,
                P.REPORTS_GENERATE,
                P.REPORTS_DOWNLOAD,
                P.OVERRIDE_APPROVE,
                P.AI_USE,
                P.AI_VIEW,
            }
        ),
    ),
    AUDITOR: RoleDefinition(
        code=AUDITOR,
        name="Auditor / Read-only",
        description="Independent read-only access to history and audit trails",
        rank=60,
        permissions=frozenset(
            {
                P.DASHBOARD_VIEW,
                P.LABS_VIEW,
                P.RULES_VIEW,
                P.AUDIT_VIEW,
                P.AUDIT_VIEW_LIMITED,
                P.CASES_VIEW,
                P.TESTS_VIEW_RESULTS,
                P.REPORTS_DOWNLOAD,
                P.AI_VIEW,
            }
        ),
    ),
}


def role_permissions(role_code: str) -> frozenset[str]:
    role = ROLE_DEFINITIONS.get(role_code)
    return role.permissions if role else frozenset()


def has_permission(role_code: str | None, permission: str) -> bool:
    if not role_code:
        return False
    return permission in role_permissions(role_code)


def role_name(role_code: str) -> str:
    role = ROLE_DEFINITIONS.get(role_code)
    return role.name if role else role_code
