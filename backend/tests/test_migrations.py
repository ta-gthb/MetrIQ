"""Migration behaviour (project-audit item 11).

These run against their own throwaway database: they exercise the paths that
create and reset a schema, and doing that to the suite's shared database would
pull the data out from under every other test.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

from app import database as app_database
from app import migrations
from app.models import Base

MODEL_TABLES = {name.split(".", 1)[-1] for name in Base.metadata.tables}


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """A private database with the application pointed at it."""
    url = f"sqlite:///{(tmp_path / 'scratch.db').as_posix()}"
    engine = create_engine(url, future=True)
    monkeypatch.setattr(app_database, "engine", engine)
    monkeypatch.setattr(app_database, "DATABASE_URL", url)
    # migrations.py imported `engine` by value, so it needs the same patch.
    monkeypatch.setattr(migrations, "engine", engine)
    try:
        yield engine
    finally:
        engine.dispose()


def table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def test_a_fresh_database_migrates_from_zero(scratch):
    assert migrations.current_revision() is None

    report = migrations.apply_migrations()

    assert report["adopted"] is False
    assert report["resynced"] is False
    assert report["error"] is None
    assert report["after"] == migrations.head_revision() == migrations.BASELINE_REVISION
    assert MODEL_TABLES <= table_names(scratch)
    assert migrations.VERSION_TABLE in table_names(scratch)


def test_upgrading_an_already_current_database_changes_nothing(scratch):
    migrations.apply_migrations()

    report = migrations.apply_migrations()

    assert report["adopted"] is False
    assert report["resynced"] is False
    assert report["after"] == migrations.BASELINE_REVISION
    assert migrations.database_revision_summary()["schema_up_to_date"] is True


def test_an_existing_schema_is_adopted_without_touching_its_data(scratch):
    """The deployed path: MetrIQ ran on create_all before migrations existed."""
    Base.metadata.create_all(bind=scratch)
    with scratch.begin() as connection:
        connection.execute(
            Base.metadata.tables["laboratories"].insert().values(name="Pre-migration lab", code="ADOPT-1")
        )

    report = migrations.apply_migrations()

    assert report["adopted"] is True, "an unmanaged but populated schema must be stamped"
    assert report["resynced"] is False
    assert report["after"] == migrations.BASELINE_REVISION
    with scratch.connect() as connection:
        names = connection.execute(sa.text("select name from laboratories")).scalars().all()
    assert names == ["Pre-migration lab"], "adoption must not rebuild the tables"


def test_a_dropped_schema_is_replayed_instead_of_left_empty(scratch):
    """reinit_db drops the tables but not alembic_version; the next upgrade
    would otherwise be a no-op and leave the database empty."""
    migrations.apply_migrations()
    Base.metadata.drop_all(bind=scratch)
    assert migrations.VERSION_TABLE in table_names(scratch), "drop_all leaves the record behind"

    report = migrations.apply_migrations()

    assert report["resynced"] is True
    assert report["after"] == migrations.BASELINE_REVISION
    assert MODEL_TABLES <= table_names(scratch)


def test_the_tree_can_be_replayed_from_base(scratch):
    migrations.apply_migrations()

    migrations.downgrade("base")
    assert migrations.current_revision() is None
    assert not (MODEL_TABLES & table_names(scratch))

    migrations.upgrade("head")
    assert migrations.current_revision() == migrations.BASELINE_REVISION
    assert MODEL_TABLES <= table_names(scratch)


def test_the_baseline_describes_exactly_the_models(scratch):
    """Drift between the models and the migration tree is a release blocker:
    a fresh database would otherwise differ from an upgraded one."""
    from alembic import command

    migrations.apply_migrations()

    command.check(migrations.alembic_config())


def test_health_publishes_the_migration_revision(client):
    body = client.get("/health").json()

    assert body["schema_revision"] == migrations.head_revision()
    assert body["schema_revision_head"] == migrations.head_revision()
    assert body["schema_up_to_date"] is True