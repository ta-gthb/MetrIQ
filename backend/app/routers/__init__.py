"""HTTP routers for the MetrIQ API (PRD 16.2)."""

from app.routers import (
    admin,
    ai,
    attachments,
    audit,
    auth,
    cases,
    dashboard,
    masters,
    reports,
    standards,
    tests,
    workflow,
)

__all__ = [
    "admin",
    "ai",
    "attachments",
    "audit",
    "auth",
    "cases",
    "dashboard",
    "masters",
    "reports",
    "standards",
    "tests",
    "workflow",
]
