"""Schema migrations.

The deployed instance has no release step - Render's free plan offers neither a
pre-deploy command nor a shell - so the schema is brought to the current
revision by the process as it boots. This module is the single entry point for
that, and for the ``scripts/migrate.py`` CLI used locally and in CI.

Two details are deliberate:

* every catalogue query names ``DB_SCHEMA`` explicitly instead of relying on the
  connection's ``search_path``. On a database shared with another application
  (this project's Supabase instance has one owning ``public``) an unqualified
  lookup can answer about the wrong schema, which is worse than failing;
* reading the current revision must not create anything. Alembic's own helper
  creates the version table when it is absent, so a read-only check would write
  to a database it does not own. The single row is read directly instead.

Adopting a pre-existing database matters here: MetrIQ shipped for a while with
``Base.metadata.create_all`` as its only schema mechanism, so a database may
already contain the baseline tables with no recorded revision. Upgrading such a
database from scratch would fail on the first CREATE TABLE, so
:func:`apply_migrations` stamps the baseline first and applies later revisions
on top.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.models import Base

logger = logging.getLogger("metriq.migrations")

BACKEND_DIR = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "0001"
VERSION_TABLE = "alembic_version"


def alembic_config() -> Config:
    """An Alembic config whose URL comes from the application settings."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    url = engine.url.render_as_string(hide_password=False)
    # A literal '%' would be read as an interpolation marker by ConfigParser.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def head_revision() -> str | None:
    """The newest revision in the migration tree."""
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def _schema() -> str | None:
    """The schema MetrIQ's tables live in, or None for the database default."""
    if engine.dialect.name == "sqlite":
        return None
    return settings.DB_SCHEMA or None


def table_names() -> set[str]:
    """Table names in MetrIQ's schema, straight from the catalogue."""
    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            rows = connection.execute(
                text("select name from sqlite_master where type = 'table'")
            ).all()
        return {row[0] for row in rows}
    schema = _schema()
    statement = "select table_name from information_schema.tables where "
    params: dict[str, str] = {}
    if schema:
        statement += "table_schema = :schema"
        params["schema"] = schema
    else:
        statement += "table_schema = current_schema()"
    with engine.connect() as connection:
        rows = connection.execute(text(statement), params).all()
    return {row[0] for row in rows}


def known_tables() -> set[str]:
    """MetrIQ's table names, without the schema qualification.

    ``Base.metadata`` carries the schema (``app/models/base.py`` sets it when a
    shared database needs one), so its keys look like ``metriq.users`` while the
    catalogue returns bare names. Comparing the two directly would always be
    empty, which would make a populated schema look unmanaged and then fail on
    the first CREATE TABLE.
    """
    return {name.split(".", 1)[-1] for name in Base.metadata.tables}


def existing_managed_tables() -> set[str]:
    """MetrIQ tables that already exist in the configured schema."""
    return table_names() & known_tables()


def current_revision() -> str | None:
    """The applied revision, or None when the database is not managed yet."""
    if VERSION_TABLE not in table_names():
        return None
    schema = _schema()
    target = f'"{schema}".{VERSION_TABLE}' if schema else VERSION_TABLE
    with engine.connect() as connection:
        row = connection.execute(text(f"select version_num from {target}")).first()
    return row[0] if row else None


def is_managed() -> bool:
    """True when a revision has actually been recorded."""
    return current_revision() is not None


def clear_version_records() -> None:
    """Forget the recorded revision, leaving every table alone.

    The destructive re-initialisation scripts drop the application's tables
    with ``Base.metadata.drop_all``, which does not touch
    ``alembic_version``. A stale revision would make the next upgrade a
    no-op and leave the database empty, so the record has to go too.
    """
    if VERSION_TABLE not in table_names():
        return
    schema = _schema()
    target = f'"{schema}".{VERSION_TABLE}' if schema else VERSION_TABLE
    with engine.begin() as connection:
        connection.execute(text(f"drop table {target}"))


def stamp(revision: str = BASELINE_REVISION) -> None:
    command.stamp(alembic_config(), revision)


def upgrade(revision: str = "head") -> None:
    command.upgrade(alembic_config(), revision)


def downgrade(revision: str) -> None:
    command.downgrade(alembic_config(), revision)


def apply_migrations() -> dict:
    """Bring the database to head, adopting an unmanaged schema if needed.

    Three cases are handled: a database Alembic has never touched but that
    already holds MetrIQ's tables is adopted (stamped at the baseline, then
    upgraded) instead of failing on the first CREATE TABLE; a database whose
    tables were dropped while the revision record survived is reset so the
    tree replays; and everything else simply upgrades.

    Returns a report for ``/health``, so the revision a deployment is actually
    running on is visible without shell access.
    """
    report: dict = {
        "before": current_revision(),
        "adopted": False,
        "resynced": False,
        "error": None,
    }

    if report["before"] is not None and not existing_managed_tables():
        # A revision is recorded but none of its tables exist: usually the
        # schema was dropped by the re-initialisation scripts, which
        # `drop_all` leaves `alembic_version` untouched by. Upgrading from a
        # stale revision would be a no-op and leave the database empty, so
        # the record is cleared and the tree replays from the start.
        clear_version_records()
        report["before"] = None
        report["resynced"] = True
        logger.warning(
            "a revision was recorded but no MetrIQ tables exist; clearing it and "
            "replaying the migration tree from %s", BASELINE_REVISION,
        )

    if not is_managed():
        existing = existing_managed_tables()
        if existing:
            stamp(BASELINE_REVISION)
            report["adopted"] = True
            logger.info(
                "adopted the existing schema as revision %s (%s tables already present); "
                "later migrations now apply on top",
                BASELINE_REVISION, len(existing),
            )

    upgrade("head")
    report["after"] = current_revision()
    report["head"] = head_revision()
    return report


def database_revision_summary() -> dict:
    """Migration state, for ``/health``. Never raises."""
    try:
        current = current_revision()
        head = head_revision()
    except Exception as exc:  # pragma: no cover - depends on the deployment
        return {"schema_revision": f"unknown: {exc}"}
    return {
        "schema_revision": current or "unmanaged",
        "schema_revision_head": head or "unknown",
        "schema_up_to_date": bool(current) and current == head,
    }