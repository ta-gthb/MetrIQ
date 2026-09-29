"""SQLAlchemy model registry. Importing this package registers every table."""

from app.models.base import Base, utcnow
from app.models.case import (
    CaseAssignment,
    CaseStatus,
    EnvironmentalCondition,
    EvaluationCase,
    TestEquipmentUsage,
)
from app.models.audit import (
    AIEvent,
    AuditEventType,
    AuditLog,
    Notification,
    SystemSetting,
    WorkflowAction,
)
from app.models.evidence import Attachment, AttachmentCategory, AttachmentLink
from app.models.identity import Laboratory, Permission, Role, RolePermission, User
from app.models.instrument import Instrument, InstrumentRange
from app.models.org import Applicant, EquipmentCalibration, Manufacturer, TestEquipment
from app.models.report import GeneratedReport, ReportRevision, ReportSignature
from app.models.standards import (
    ReportTemplate,
    ReportTemplateVersion,
    Rule,
    RuleVersion,
    Standard,
    StandardVersion,
)
from app.models.test import (
    ApplicabilityStatus,
    CalculationRun,
    ComplianceResult,
    ManualOverride,
    TestDefinition,
    TestInstance,
    TestInstanceStatus,
    TestObservation,
    TestResultStatus,
)

__all__ = [
    "ApplicabilityStatus",
    "Attachment",
    "AttachmentCategory",
    "AttachmentLink",
    "AuditEventType",
    "AuditLog",
    "AIEvent",
    "Base",
    "CalculationRun",
    "CaseAssignment",
    "CaseStatus",
    "ComplianceResult",
    "EnvironmentalCondition",
    "EquipmentCalibration",
    "EvaluationCase",
    "GeneratedReport",
    "Instrument",
    "InstrumentRange",
    "Laboratory",
    "ManualOverride",
    "Manufacturer",
    "Notification",
    "Permission",
    "ReportRevision",
    "ReportSignature",
    "ReportTemplate",
    "ReportTemplateVersion",
    "Role",
    "RolePermission",
    "Rule",
    "RuleVersion",
    "Standard",
    "StandardVersion",
    "SystemSetting",
    "TestDefinition",
    "TestEquipment",
    "TestEquipmentUsage",
    "TestInstance",
    "TestInstanceStatus",
    "TestObservation",
    "TestResultStatus",
    "User",
    "WorkflowAction",
    "utcnow",
]
