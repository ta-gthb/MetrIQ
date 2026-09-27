"""Create and sanity-check the database when the application starts.

A deployed instance has to provision itself: Render's free plan provides
neither a pre-deploy command nor a shell, so the schema and the reference
catalogue are created by the process as it boots. Every step is idempotent and
the expensive part (the R76 catalogue) is skipped unless it is missing.

Failures are reported loudly in the log and reflected in ``/health`` rather
than swallowed, so a misconfigured ``DATABASE_URL`` is obvious immediately
instead of surfacing as a 503 on the first sign-in attempt.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import engine, session_scope
from app.models import Base, Role, StandardVersion, TestDefinition

logger = logging.getLogger("metriq.bootstrap")


def target_description() -> str:
    """The configured database, with the password masked."""
    return engine.url.render_as_string(hide_password=True)


def probe_connection() -> tuple[bool, str]:
    """One cheap round trip, so a bad DATABASE_URL surfaces at boot."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # pragma: no cover - driver level surprises
        return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


def ensure_schema() -> int:
    """Create any missing table. Returns the number of tables in the schema."""
    Base.metadata.create_all(bind=engine)
    return len(Base.metadata.tables)


def reference_data_present(db) -> bool:
    """True when roles, an active standard version and the catalogue all exist."""
    if db.execute(select(func.count()).select_from(Role)).scalar_one() == 0:
        return False
    active = db.execute(
        select(func.count()).select_from(StandardVersion).where(StandardVersion.is_active.is_(True))
    ).scalar_one()
    if not active:
        return False
    return db.execute(select(func.count()).select_from(TestDefinition)).scalar_one() > 0


def seed_reference_data(db) -> dict[str, int]:
    """Upsert roles, permissions and the full R76 catalogue. Idempotent."""
    from app.services.reference_data.identity import seed_roles_and_permissions
    from app.services.reference_data.rules import (
        seed_report_template,
        seed_ruleset,
        seed_test_catalogue,
    )

    roles, grants = seed_roles_and_permissions(db)
    standard_version = seed_ruleset(db)
    seed_report_template(db, standard_version)
    definitions = seed_test_catalogue(db, standard_version)
    standard_version.is_active = True
    standard_version.status = "active"
    return {"roles": roles, "new_grants": grants, "test_definitions": definitions}


def initialise_database() -> dict:
    """Start-up hook: create the schema and seed the catalogue when missing."""
    report: dict = {
        "target": target_description(),
        "connected": False,
        "error": None,
        "tables": 0,
        "seeded": {},
        "reference_data": "unknown",
    }

    connected, detail = probe_connection()
    report["connected"] = connected
    if not connected:
        report["error"] = detail
        logger.error(
            "DATABASE UNAVAILABLE at %s (%s). The API will start, but every request that "
            "touches the database will fail. Check DATABASE_URL: for Supabase use the "
            "session pooler on port 5432 and percent-encode the password.",
            report["target"], detail,
        )
        return report

    if settings.AUTO_INIT_DB:
        report["tables"] = ensure_schema()

    if settings.AUTO_SEED_REFERENCE:
        with session_scope() as db:
            if reference_data_present(db):
                report["reference_data"] = "present"
            else:
                report["seeded"] = seed_reference_data(db)
                report["reference_data"] = "seeded"

    logger.info(
        "database ready at %s: %s tables, reference data %s%s",
        report["target"], report["tables"], report["reference_data"],
        f", seeded {report['seeded']}" if report["seeded"] else "",
    )
    return report


def database_status() -> str:
    """Short readiness string for the /health payload."""
    connected, detail = probe_connection()
    return "ok" if connected else f"unavailable: {detail}"
