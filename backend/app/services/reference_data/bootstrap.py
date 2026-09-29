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
import os
from collections import defaultdict

import sqlalchemy as sa
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import engine, session_scope
from app.migrations import apply_migrations
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
    """Bring the schema to the current revision, creating it when empty.

    Migrations own the schema: on an empty database Alembic creates every
    table, and on one MetrIQ provisioned before migrations existed
    :func:`app.migrations.apply_migrations` adopts it - stamped at the
    baseline, then upgraded - without touching the data. ``create_all``
    survives only as a fallback for a deployment whose migration tree is
    missing, so the API always boots; the failure is logged and reported on
    ``/health``.

    Returns the number of tables in the model. When DB_SCHEMA is set the schema
    is created first, so unqualified DDL resolves into it rather than into
    another application's "public" schema.
    """
    global _LAST_MIGRATION_REPORT
    if settings.DB_SCHEMA and engine.dialect.name != "sqlite":
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.DB_SCHEMA}"'))
    try:
        _LAST_MIGRATION_REPORT = apply_migrations()
        logger.info(
            "schema at revision %s (head %s, adopted=%s, resynced=%s)",
            _LAST_MIGRATION_REPORT.get("after"),
            _LAST_MIGRATION_REPORT.get("head"),
            _LAST_MIGRATION_REPORT.get("adopted"),
            _LAST_MIGRATION_REPORT.get("resynced"),
        )
    except Exception as exc:  # pragma: no cover - depends on the deployment
        _LAST_MIGRATION_REPORT = {"error": f"{type(exc).__name__}: {exc}"}
        logger.error(
            "MIGRATION FAILED (%s: %s). Falling back to create_all so the API still "
            "boots; the schema may be left at an unknown revision - check with "
            "`python -m scripts.migrate current`.",
            type(exc).__name__, exc,
        )
        Base.metadata.create_all(bind=engine)
    return len(Base.metadata.tables)


def schema_problems(
    bind: Engine | None = None, *, include_missing: bool = True
) -> list[str]:
    """Tables that exist but do not carry the columns this application needs.

    ``create_all`` only creates *missing* tables. A pre-existing table with a
    different shape - another application's ``users`` table in a shared
    database, for instance - is left untouched and then fails at query time
    with a raw ``UndefinedColumn``. Checking up front turns that into an
    actionable message.

    ``include_missing=False`` answers the narrower question the destructive
    re-initialisation asks before it drops anything: which tables that already
    exist here would MetrIQ's own DDL take over? An absent table is not a
    conflict, so a database that has never been initialised does not look
    like a shared one.
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
            if include_missing:
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
    """True when the roles and the shipped catalogue are already here.

    Activation is deliberately *not* part of this test. Seeding and activation
    are separate steps now (audit items 6 and 10), so a database whose ruleset
    is still waiting for a metrology review must not look un-seeded and be
    re-seeded on every start-up.
    """
    if db.execute(select(func.count()).select_from(Role)).scalar_one() == 0:
        return False
    return db.execute(select(func.count()).select_from(TestDefinition)).scalar_one() > 0


def active_ruleset_state(db) -> dict:
    """What the instance would actually use, for /health and the boot log.

    A version that is inactive because it is waiting for review has to be
    distinguishable from one that was never seeded at all, otherwise a
    correctly governed deployment looks identical to a broken one.
    """
    active = db.execute(
        select(StandardVersion)
        .where(StandardVersion.is_active.is_(True))
        .order_by(StandardVersion.created_at.desc())
    ).scalars().first()
    if active is not None:
        return {
            "state": active.status,
            "version_label": active.version_label,
            "is_active": True,
            "activation_basis": active.activation_basis,
            "provisional": active.activation_basis == "provisional",
        }
    latest = db.execute(
        select(StandardVersion).order_by(StandardVersion.created_at.desc())
    ).scalars().first()
    if latest is None:
        return {"state": "none", "version_label": None, "is_active": False,
                "activation_basis": None, "provisional": False}
    return {
        "state": latest.status or "draft",
        "version_label": latest.version_label,
        "is_active": False,
        "activation_basis": latest.activation_basis,
        "provisional": False,
    }


def activate_seeded_ruleset(db, standard_version) -> dict:
    """Move the shipped ruleset out of draft, without bypassing the gate.

    Seeding creates a catalogue; it does not make it usable. A fresh instance
    needs an active ruleset or no evaluation case can be created at all, so the
    bootstrap asks the lifecycle to activate the shipped version.

    Outside production that is a **provisional** activation: the version is
    recorded with ``activation_basis='provisional'``, never with a fabricated
    reviewer, and the API, the UI and the report footer label it. In production
    the version is left in draft - an unreviewed rule cannot become a production
    rule, which is the whole point of the gate (audit item 6).
    """
    from app.services import ruleset_lifecycle

    if standard_version.is_active:
        return {
            "state": "already active",
            "basis": standard_version.activation_basis,
            "provisional": standard_version.activation_basis
            == ruleset_lifecycle.BASIS_PROVISIONAL,
        }

    if not settings.allow_provisional_ruleset:
        logger.warning(
            "RULESET AWAITING DOMAIN REVIEW: the seeded ruleset %s is present but "
            "not active. No evaluation case can be created until a qualified "
            "metrology reviewer records a review for every rule and an approver "
            "activates the version (POST /api/v1/rulesets/%s/submit-review, then "
            "/approve and /activate). Set ALLOW_PROVISIONAL_RULESET_ACTIVATION=true "
            "only for a deliberate demonstration deployment.",
            standard_version.version_label,
            standard_version.id,
        )
        return {
            "state": "draft: awaiting domain review",
            "basis": None,
            "provisional": False,
        }

    try:
        ruleset_lifecycle.activate(
            db,
            standard_version,
            actor=None,
            basis=ruleset_lifecycle.BASIS_PROVISIONAL,
            reason="development/demo bootstrap",
            force_provisional=True,
        )
    except ruleset_lifecycle.LifecycleError as exc:
        logger.error("PROVISIONAL RULESET ACTIVATION FAILED: %s", exc.message)
        return {"state": f"refused: {exc.message}", "basis": None, "provisional": False}

    logger.warning(
        "RULESET PROVISIONALLY ACTIVE: %s was activated by the bootstrap without a "
        "metrology review. Its rules are the shipped reference values, not a "
        "verified set. Run the review workflow before relying on its results.",
        standard_version.version_label,
    )
    return {
        "state": "active (provisional)",
        "basis": ruleset_lifecycle.BASIS_PROVISIONAL,
        "provisional": True,
    }


def seed_reference_data(db) -> dict:
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
    activation = activate_seeded_ruleset(db, standard_version)
    return {
        "roles": roles,
        "new_grants": grants,
        "test_definitions": definitions,
        "ruleset_activation": activation,
    }


def initialise_database() -> dict:
    """Start-up hook: create the schema and seed the catalogue when missing."""
    global _LAST_REPORT
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
        _LAST_REPORT = report
        return report

    if settings.AUTO_INIT_DB:
        report["tables"] = ensure_schema()
        report["migrations"] = dict(_LAST_MIGRATION_REPORT)
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

    # Seeding is attempted only against a schema that matches this application.
    # Otherwise the first INSERT fails halfway through, which used to abort
    # start-up entirely: the service would crash-loop behind a stale instance and
    # the deploy looked like it simply never took effect.
    if settings.AUTO_SEED_REFERENCE and not report["schema_problems"]:
        try:
            with session_scope() as db:
                if reference_data_present(db):
                    report["reference_data"] = "present"
                else:
                    report["seeded"] = seed_reference_data(db)
                    report["reference_data"] = "seeded"
                report["ruleset"] = active_ruleset_state(db)
        except SQLAlchemyError as exc:
            report["reference_data"] = "failed"
            report["error"] = f"{type(exc).__name__}: {exc}"
            logger.error(
                "REFERENCE DATA SEEDING FAILED at %s: %s. The API stays up so this is "
                "visible from /health, but cases cannot be created until the reference "
                "catalogue is seeded. Check DB_SCHEMA and DATABASE_URL.",
                report["target"], exc,
            )
    elif settings.AUTO_SEED_REFERENCE:
        report["reference_data"] = "skipped: schema mismatch"

    _LAST_REPORT = report

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


_LAST_REPORT: dict = {}
_LAST_MIGRATION_REPORT: dict = {}


def last_report() -> dict:
    """The most recent start-up report, for /health."""
    return _LAST_REPORT


def schema_source() -> str:
    """Where the effective DB_SCHEMA came from.

    A schema mismatch has one of two causes: the value never reached the
    service, or it reached it and something overrode it. environment variables
    win over every configuration file, so reporting which one supplied the
    value settles the question from a single request.
    """
    if "DB_SCHEMA" in os.environ:
        return "environment"
    if settings.DB_SCHEMA:
        return "configuration file"
    return "default"


def health_summary() -> dict:
    """Configuration facts worth exposing publicly, without error detail."""
    revision = _LAST_MIGRATION_REPORT.get("after")
    head = _LAST_MIGRATION_REPORT.get("head")
    if _LAST_MIGRATION_REPORT.get("error"):
        revision = "migration failed"
    return {
        "schema": settings.DB_SCHEMA or "public",
        "schema_source": schema_source(),
        "schema_problems": len(_LAST_REPORT.get("schema_problems") or []),
        "reference_data": _LAST_REPORT.get("reference_data", "unknown"),
        # Which ruleset this instance would actually evaluate against, and
        # whether it holds a metrology review or was only provisionally
        # activated by the development/demo bootstrap (audit items 6 and 10).
        "ruleset": _LAST_REPORT.get("ruleset") or {"state": "unknown"},
        # The migration revision this deployment booted at. Without it, a
        # schema that is behind the code looks identical to one that is not.
        "schema_revision": revision or "unmanaged",
        "schema_revision_head": head or "unknown",
        "schema_up_to_date": bool(revision) and revision == head,
    }
