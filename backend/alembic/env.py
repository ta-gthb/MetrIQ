"""Alembic environment for MetrIQ.

Two decisions are worth stating because they are not the Alembic defaults:

* the target URL is taken from :mod:`app.config` rather than from
  ``alembic.ini``, so a migration always runs against the database the code
  would use, and no credential is ever committed;
* only tables declared in ``Base.metadata`` are managed. When MetrIQ shares a
  database with another application (DB_SCHEMA), Alembic must not report - or
  drop - that application's tables as "removed".
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import context

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.database import DATABASE_URL, engine  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config
# Offline mode (--sql) still needs a URL; a literal '%' would be read as an
# interpolation marker, so it is escaped.
config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))

target_metadata = Base.metadata
MANAGED_TABLES = set(target_metadata.tables)
VERSION_TABLE = "alembic_version"


def include_object(object_, name, type_, reflected, compare_to):
    """Keep foreign tables out of autogenerate, in both directions."""
    if type_ == "table":
        if reflected:
            return name in MANAGED_TABLES
        return True
    return True


def _configure(connection=None, **kwargs) -> None:
    """Configure a run. DB_SCHEMA only applies to PostgreSQL: SQLite has no
    schemas, and asking for one there fails at the first CREATE TABLE."""
    dialect = connection.dialect.name if connection is not None else (
        "sqlite" if DATABASE_URL.startswith("sqlite") else "postgresql"
    )
    version_schema = settings.DB_SCHEMA if (settings.DB_SCHEMA and dialect != "sqlite") else None
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        include_schemas=False,
        version_table=VERSION_TABLE,
        version_table_schema=version_schema,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=DATABASE_URL, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _sqlite_foreign_keys(connection, enabled: bool) -> None:
    """Turn SQLite's foreign-key enforcement on or off for this connection.

    SQLite cannot alter a column in place, so a batch migration rebuilds the
    table: it creates a replacement, copies the rows, drops the original and
    renames. ``DROP TABLE`` runs the delete it implies, and with foreign keys
    enforced that delete cascades - rebuilding ``users`` would silently empty
    every table that references it. The pragma is a no-op inside a transaction,
    so it has to be issued before the run's transaction opens, and it is put
    back afterwards because the connection is returned to the application's
    pool and the next request will use it.
    """
    connection.exec_driver_sql(
        f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}"
    )


def run_migrations_online() -> None:
    # The application's engine already sets search_path for DB_SCHEMA, applies
    # pool_pre_ping and carries the JSON serializer used by the models, so the
    # migration connection behaves exactly like a request connection.
    with engine.connect() as connection:
        _configure(
            connection=connection,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            _sqlite_foreign_keys(connection, False)
        try:
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if sqlite:
                _sqlite_foreign_keys(connection, True)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()