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
from collections import defaultdict

import sqlalchemy as sa
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
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


class SchemaMismatch(RuntimeError):
    """A table exists but does not match this application's schema."""


def ensure_schema() -> int:
    """Create the configured schema and any missing table.

    Returns the number of tables in the model. When DB_SCHEMA is set the schema
    is created first, so unqualified DDL resolves into it rather than into
    another application's `public` schema.
    """
    if settings.DB_SCHEMA and engine.dialect.name != "sqlite":
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.DB_SCHEMA}"'))
    Base.metadata.create_all(bind=engine)
    return len(Base.metadata.tables)


def schema_problems(bind: Engine | None = None) -> list[str]:
    """Tables that exist but do not carry the columns this application needs.

    ``create_all`` only creates *missing* tables. A pre-existing table with a
    different shape - another application's ``users`` table in a shared
    database, for instance - is left untouched and then fails at query time
    with a raw ``UndefinedColumn``. Checking up front turns that into an
    actionable message.
    """
    target = bind if bind is not None else engine
    expected = {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.sorted_tables
    }

    if target.dialect.name == "sqlite":
        inspector = sa.inspect(target)
        present = {
            name: {column["name"] for column in inspector.get_columns(name)}
            for name in expected
            if inspector.has_table(name)
        }
    else:
        columns_by_table: dict[str, set[str]] = defaultdict(set)
        with target.connect() as connection:
            if settings.DB_SCHEMA:
                # Until the schema exists, current_schema() falls back to the
                # first existing entry in the search path - usually another
                # application's public schema - which would report false
                # conflicts against tables MetrIQ is not going to use.
                exists = connection.execute(
                    text(
                        "select count(*) from information_schema.schemata "
                        "where schema_name = :name"
                    ),
                    {"name": settings.DB_SCHEMA},
                ).scalar_one()
                if not exists:
                    return []
            params: dict = {"names": list(expected)}
            if settings.DB_SCHEMA:
                schema_predicate = "table_schema = :schema"
                params["schema"] = settings.DB_SCHEMA
            else:
                schema_predicate = "table_schema = current_schema()"
            rows = connection.execute(
                text(
                    "select table_name, column_name from information_schema.columns "
                    f"where {schema_predicate} and table_name = any(:names)"
                ),
                params,
            ).all()
        for table_name, column_name in rows:
            columns_by_table[table_name].add(column_name)
        present = columns_by_table

    problems: list[str] = []
    for name, columns in expected.items():
        if name not in present:
            problems.append(f"missing table '{name}'")
            continue
        missing = sorted(columns - present[name])
        if missing:
            problems.append(f"table '{name}' is missing column(s): {', '.join(missing)}")
    return problems


def require_schema(bind: Engine | None = None) -> None:
    """Raise a SchemaMismatch describing how to recover."""
    problems = schema_problems(bind)
    if not problems:
        return
    detail = "; ".join(problems[:4]) + (" ..." if len(problems) > 4 else "")
    raise SchemaMismatch(
        f"{detail}. A database shared with another application usually causes this: "
        "give MetrIQ its own database, or set DB_SCHEMA=<name> to keep its tables in a "
        "dedicated schema, then re-run this command."
    )


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
        "schema_problems": [],
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
        report["schema_problems"] = schema_problems()
        if report["schema_problems"]:
            logger.error(
                "SCHEMA MISMATCH: %s. A table that already exists does not match this "
                "application's schema - usually because the database is shared with another "
                "application. Give MetrIQ its own database, or set DB_SCHEMA=<name> to keep "
                "its tables in a dedicated schema.",
                "; ".join(report["schema_problems"][:4])
                + (" ..." if len(report["schema_problems"]) > 4 else ""),
            )

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
