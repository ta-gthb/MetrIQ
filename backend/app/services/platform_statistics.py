"""Aggregate platform statistics for the public home page.

The home page is served before anyone has signed in, so everything here is
either a count or a configuration fact that is already public (the standard the
platform is built on, the ruleset it evaluates against). Nothing that could
describe a single evaluation, applicant, manufacturer, instrument or person is
returned: a figure that identifies one laboratory's work does not belong on a
page anyone can load.

Counts are read from the database on every collection, so the page is showing
the live state of this instance rather than a figure baked in at build time.
"""

from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Applicant,
    AuditLog,
    CaseStatus,
    EvaluationCase,
    GeneratedReport,
    Instrument,
    Laboratory,
    Manufacturer,
    TestDefinition,
    TestInstance,
    TestObservation,
    TestResultStatus,
    utcnow,
)
from app.services.reference_data.bootstrap import active_ruleset_state

#: How often the home page re-reads these figures. Published in the payload so
#: the two ends cannot disagree about what "live" means.
REFRESH_SECONDS = 15

#: Counts move slowly, and this endpoint is unauthenticated: a short cache keeps
#: a burst of page loads from becoming a burst of queries, while still changing
#: often enough for the page to look alive.
CACHE_TTL_SECONDS = 5

#: Statuses that mean an evaluation is still moving through the workflow.
OPEN_STATUSES = (
    CaseStatus.DRAFT,
    CaseStatus.ASSIGNED,
    CaseStatus.IN_PROGRESS,
    CaseStatus.TESTING_COMPLETED,
    CaseStatus.UNDER_REVIEW,
    CaseStatus.CORRECTION_REQUIRED,
    CaseStatus.VERIFIED,
    CaseStatus.UNDER_APPROVAL,
)

#: Test outcomes that represent a completed execution.
SETTLED_RESULTS = (
    TestResultStatus.PASS,
    TestResultStatus.FAIL,
    TestResultStatus.NOT_APPLICABLE,
    TestResultStatus.WAIVED,
)

_cache: dict = {"payload": None, "expires_at": 0.0}


def _count(db: Session, model: type, *criteria) -> int:
    statement = select(func.count()).select_from(model)
    for criterion in criteria:
        statement = statement.where(criterion)
    return int(db.execute(statement).scalar_one() or 0)


def _grouped(db: Session, column, model: type) -> dict[str, int]:
    rows = db.execute(select(column, func.count()).select_from(model).group_by(column)).all()
    return {key or "PENDING": int(count) for key, count in rows}


def collect(db: Session) -> dict:
    """Read the figures this instance is willing to publish."""
    now = utcnow()
    cases = _grouped(db, EvaluationCase.status, EvaluationCase)
    results = _grouped(db, TestInstance.result_status, TestInstance)
    reports = _grouped(db, GeneratedReport.status, GeneratedReport)

    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    return {
        "generated_at": now.isoformat(),
        "refresh_seconds": REFRESH_SECONDS,
        "standards": {
            "framework": ["OIML R 76-1:2006", "OIML R 76-2:2007"],
            "ruleset": active_ruleset_state(db),
            "test_definitions": _count(db, TestDefinition),
        },
        "evaluations": {
            "total": sum(cases.values()),
            "open": sum(cases.get(status, 0) for status in OPEN_STATUSES),
            "awaiting_review": (
                cases.get(CaseStatus.TESTING_COMPLETED, 0)
                + cases.get(CaseStatus.UNDER_REVIEW, 0)
            ),
            "awaiting_approval": (
                cases.get(CaseStatus.VERIFIED, 0) + cases.get(CaseStatus.UNDER_APPROVAL, 0)
            ),
            "approved": cases.get(CaseStatus.APPROVED, 0) + cases.get(CaseStatus.FINALIZED, 0),
            "rejected": cases.get(CaseStatus.REJECTED, 0),
        },
        "testing": {
            "test_instances": sum(results.values()),
            "completed": sum(results.get(status, 0) for status in SETTLED_RESULTS),
            "compliant": results.get(TestResultStatus.PASS, 0),
            "non_compliant": results.get(TestResultStatus.FAIL, 0),
            "pending": (
                results.get(TestResultStatus.PENDING, 0)
                + results.get(TestResultStatus.INCOMPLETE, 0)
                + results.get(TestResultStatus.INVALID, 0)
            ),
            "measurements": _count(db, TestObservation),
        },
        "reports": {
            "total": sum(reports.values()),
            "issued": reports.get("FINAL", 0),
            "draft": reports.get("DRAFT", 0),
        },
        "network": {
            "laboratories": _count(db, Laboratory),
            "instruments": _count(db, Instrument),
            "manufacturers": _count(db, Manufacturer),
            "applicants": _count(db, Applicant),
        },
        # Windows, so the page has something that genuinely moves rather than a
        # set of totals that only ever grow when someone adds a record.
        "activity": {
            "evaluations_last_7_days": _count(
                db, EvaluationCase, EvaluationCase.created_at >= week_ago
            ),
            "measurements_last_24_hours": _count(
                db, TestObservation, TestObservation.created_at >= day_ago
            ),
            "reports_last_30_days": _count(
                db, GeneratedReport, GeneratedReport.created_at >= month_ago
            ),
            "events_last_24_hours": _count(db, AuditLog, AuditLog.created_at >= day_ago),
        },
    }


def snapshot(db: Session, *, force: bool = False) -> dict:
    """The payload the endpoint returns, served from cache while it is fresh.

    ``generated_at`` is the moment the figures were read, so a cached response
    says how old it is rather than pretending to be newer than it is.
    """
    if not force and _cache["payload"] is not None and time.monotonic() < _cache["expires_at"]:
        return dict(_cache["payload"])

    payload = collect(db)
    _cache["payload"] = payload
    _cache["expires_at"] = time.monotonic() + CACHE_TTL_SECONDS
    return dict(payload)


def reset_cache() -> None:
    """Drop the cached payload. Used by the tests, and after a reseed."""
    _cache["payload"] = None
    _cache["expires_at"] = 0.0
