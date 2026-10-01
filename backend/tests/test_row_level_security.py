"""Database-level row-level security (project-audit item 14).

Two layers are tested here.

The **unit layer** runs everywhere, including the SQLite suite: it pins the
shape of the policies, checks that every protected table still exists in the
models, and compares the policies the runtime builds against the ones frozen
inside migration 0007. That last comparison is what forces a new migration when
a protected table is added, so the deployed policies and the ones described
here cannot drift apart.

The **integration layer** needs a real PostgreSQL server, because SQLite has no
row-level security and asserting policies against it would be theatre. It is
opt-in through ``METRIQ_RLS_TEST_DATABASE_URL`` and deliberately
non-destructive: it creates its own schema, points ``search_path`` at it, and
drops only that schema. Point it at a scratch database, never at production::

    $env:METRIQ_RLS_TEST_DATABASE_URL = "postgresql://user:pw@host:5432/scratch"
    python -m pytest tests/test_row_level_security.py -v

It proves the claims the audit asks for:

* a connection scoped to laboratory A sees A's rows and none of laboratory B's;
* the same connection sees nothing at all when no scope is set, so the default
  is deny;
* a write outside the scope is rejected by WITH CHECK rather than merely hidden
  by the SELECT policy;
* the table owner - the backend's service role - is deliberately unaffected,
  which is what keeps the API working while ``anon``/``authenticated`` are
  confined.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app import database as app_database
from app import migrations
from app.models import Base
from app.security import rls

MIGRATION_FILE = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0007_laboratory_row_level_security.py"
)

LEVELS = (
    rls.DIRECT_TENANT_TABLES,
    rls.CASE_SCOPED_TABLES,
    rls.TEST_INSTANCE_SCOPED_TABLES,
    rls.REPORT_SCOPED_TABLES,
    rls.ATTACHMENT_SCOPED_TABLES,
)

TABLES = sorted(rls.TENANT_TABLES)


# ---------------------------------------------------------------------------
# Unit layer: the shape of the policies, and their agreement with migration 0007
# ---------------------------------------------------------------------------


def test_every_protected_table_exists_in_the_models():
    """A renamed or dropped table would leave a policy on nothing, or fail the
    migration outright. Catch it here rather than at deploy time."""
    missing = sorted(set(rls.TENANT_TABLES) - set(Base.metadata.tables))
    assert not missing, f"protected tables missing from Base.metadata: {missing}"


def test_the_protected_tables_cover_every_indirection_level():
    for level in LEVELS:
        assert level, "an empty indirection level would silently protect nothing"
    union = set().union(*(set(level) for level in LEVELS))
    assert union == set(rls.TENANT_TABLES)
    assert len(union) == sum(len(level) for level in LEVELS), (
        "a table must reach its laboratory by exactly one route, or the policies"
        " disagree about what the same row may contain"
    )


def test_every_table_gets_exactly_one_enable_drop_and_create():
    statements = rls.policy_statements()

    assert len(statements) == 3 * len(TABLES)
    for table in TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in statements
        assert f"DROP POLICY IF EXISTS {rls.POLICY_NAME} ON {table}" in statements
        created = [
            statement
            for statement in statements
            if statement.startswith(f"CREATE POLICY {rls.POLICY_NAME} ON {table} ")
        ]
        assert len(created) == 1
        assert "USING (" in created[0], "without USING the policy does not filter reads"
        assert "WITH CHECK (" in created[0], (
            "without WITH CHECK a scoped connection could still write rows another"
            " laboratory would then see"
        )


def test_statements_are_schema_qualified_when_a_schema_is_configured():
    statements = rls.policy_statements("metriq")

    assert "ALTER TABLE metriq.evaluation_cases ENABLE ROW LEVEL SECURITY" in statements
    assert all(
        "metriq." in statement for statement in statements if " ON " in statement
    )


def test_the_drop_statements_match_the_create_statements():
    dropped = set(rls.drop_policy_statements())
    for table in TABLES:
        assert f"DROP POLICY IF EXISTS {rls.POLICY_NAME} ON {table}" in dropped
    assert len(dropped) == len(TABLES)


def test_an_unset_scope_denies_rather_than_grants():
    expression = rls.current_lab_expression()

    assert expression.startswith("nullif("), (
        "nullif turns an unset or reset setting into NULL; without it an empty"
        " string would abort the query instead of returning no rows"
    )
    assert rls.LAB_SETTING in expression
    assert "current_setting" in expression
    assert expression.endswith("::uuid")


def test_every_predicate_reads_the_scope_setting():
    for table in TABLES:
        assert rls.LAB_SETTING in rls.policy_predicate(table)


def test_direct_tables_compare_their_own_laboratory_column():
    for table, column in rls.DIRECT_TENANT_TABLES.items():
        expected = f"{table}.{column} = {rls.current_lab_expression()}"
        assert rls.policy_predicate(table) == expected


def test_indirect_tables_reach_the_laboratory_through_their_parent():
    parents = {
        **{table: "evaluation_cases" for table in rls.CASE_SCOPED_TABLES},
        **{table: "test_instances" for table in rls.TEST_INSTANCE_SCOPED_TABLES},
        **{table: "generated_reports" for table in rls.REPORT_SCOPED_TABLES},
        **{table: "attachments" for table in rls.ATTACHMENT_SCOPED_TABLES},
    }
    assert set(parents) == set(TABLES) - set(rls.DIRECT_TENANT_TABLES)

    for table, parent in parents.items():
        predicate = rls.policy_predicate(table)
        assert f"FROM {parent}" in predicate, f"{table} does not resolve through {parent}"
        # The subqueries are correlated on the protected table's own key, so a
        # row can never be matched against another row's laboratory.
        assert f"{table}." in predicate


def test_an_unprotected_table_is_refused():
    """Global reference data (laboratories, roles, the standard catalogue) is
    intentionally outside the tenant scope; asking for a policy on it is a bug,
    not a silent no-op."""
    with pytest.raises(KeyError):
        rls.policy_predicate("laboratories")


def test_the_migration_is_pinned_to_the_previous_revision():
    module = _migration_module()

    assert module.revision == "0007"
    assert module.down_revision == "0006"
    assert module.POLICY_NAME == rls.POLICY_NAME


def test_the_migration_freezes_exactly_the_runtime_policies():
    """Migration 0007 must keep doing what it did on the day it was written, so
    its policy list is frozen. This compares it with the runtime and fails when
    they diverge - which is the signal to add a new migration rather than edit
    0007, and to keep app.security.rls authoritative."""
    frozen = dict(_migration_module().POLICIES)
    runtime = {table: rls.policy_predicate(table) for table in TABLES}

    drifted = {
        table: (frozen.get(table), runtime.get(table))
        for table in set(frozen) | set(runtime)
        if frozen.get(table) != runtime.get(table)
    }
    assert not drifted, f"runtime policies and migration 0007 disagree: {drifted}"


def test_the_migration_touches_no_other_table():
    frozen = {table for table, _ in _migration_module().POLICIES}
    assert frozen == set(TABLES), (
        "migration 0007 and app.security.rls must protect the same tables,"
        " otherwise a deployment change would need a hand-written migration"
    )


def test_apply_session_scope_is_a_no_op_off_postgres():
    """The demonstration deployment and this suite run on SQLite, which has no
    row-level security; the call must be silently harmless there."""
    session = app_database.SessionLocal()
    try:
        assert session.get_bind().dialect.name == "sqlite"
        rls.apply_session_scope(session, uuid.uuid4())
    finally:
        session.close()


class _FakeBind:
    class dialect:  # noqa: N801 - stands in for a SQLAlchemy dialect
        name = "postgresql"


class _RecordingSession:
    """Minimal stand-in for a PostgreSQL session, to observe the decision to
    emit SQL without needing a server."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get_bind(self):
        return _FakeBind()

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))


def test_a_principal_without_a_laboratory_is_not_scoped():
    """The platform super admin is authorised by the application layer, not by
    a tenant scope, so no setting is pushed."""
    session = _RecordingSession()

    rls.apply_session_scope(session, None)

    assert session.calls == []


def test_a_principal_with_a_laboratory_sets_the_scope_locally():
    session = _RecordingSession()
    laboratory = uuid.uuid4()

    rls.apply_session_scope(session, laboratory)

    assert len(session.calls) == 1
    statement, parameters = session.calls[0]
    # `true` is the third set_config argument: local to the transaction, so a
    # pooled connection cannot carry one request's laboratory into the next.
    assert "set_config" in statement
    assert parameters == {"setting": rls.LAB_SETTING, "value": str(laboratory)}


def test_describe_lists_every_protected_table():
    described = rls.describe()

    listed = set().union(
        *(
            set(described[key])
            for key in ("direct", "via_case", "via_test_instance", "via_report", "via_attachment")
        )
    )
    assert listed == set(TABLES)
    assert described["setting"] == rls.LAB_SETTING
    assert described["policy"] == rls.POLICY_NAME


def _migration_module():
    spec = importlib.util.spec_from_file_location("metriq_rls_migration_0007", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Integration layer: real PostgreSQL, positive and negative cross-laboratory
# ---------------------------------------------------------------------------

PG_URL = os.environ.get("METRIQ_RLS_TEST_DATABASE_URL", "").strip()
TEST_SCHEMA = "metriq_rls_test"
PROBE_ROLE = "metriq_rls_probe"


class World:
    """The scratch world the integration tests interrogate."""

    def __init__(self, engine, built, role_code):
        self.engine = engine
        self._built = built
        self.laboratory_a = built["laboratory_a"]
        self.laboratory_b = built["laboratory_b"]
        self.case_a = built["case_a"]
        self.instrument_a = built["instrument_a"]
        self.role_code = role_code

    def other_case(self):
        """Laboratory B's case: the row laboratory A must never reach."""
        return self._built["case_b"]


@pytest.fixture(scope="module")
def world():
    if not PG_URL:
        pytest.skip(
            "set METRIQ_RLS_TEST_DATABASE_URL to a scratch PostgreSQL database to"
            " exercise row-level security; SQLite cannot prove it"
        )

    engine = create_engine(PG_URL, future=True)

    # The application's metadata was built with DB_SCHEMA empty for this suite,
    # so tables are unqualified and resolve through search_path. Pinning it to a
    # private schema keeps this test from touching anything else in the
    # database - including a database that turns out not to be as scratch as the
    # operator thought.
    #
    # It must be registered before the first connection is checked out. A
    # connection made earlier would be returned to the pool without the path,
    # and the migrations - plus every seed row - would land in `public` while
    # the assertions looked at the private schema and proved nothing.
    @event.listens_for(engine, "connect")
    def _search_path(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f'SET search_path TO "{TEST_SCHEMA}"')
        finally:
            cursor.close()
        dbapi_connection.commit()

    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        engine.dispose()
        pytest.skip(f"cannot reach METRIQ_RLS_TEST_DATABASE_URL: {exc}")

    original_engine = app_database.engine
    original_migration_engine = migrations.engine
    app_database.engine = engine
    migrations.engine = engine
    try:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
            connection.execute(text(f'CREATE SCHEMA "{TEST_SCHEMA}"'))

        migrations.upgrade("head")
        assert migrations.current_revision() == migrations.head_revision(), (
            "the RLS migration must be part of the tree that a fresh database gets"
        )

        with engine.connect() as connection:
            landed = connection.execute(
                text(
                    "select count(*) from information_schema.tables"
                    " where table_schema = :schema and table_name = 'evaluation_cases'"
                ),
                {"schema": TEST_SCHEMA},
            ).scalar_one()
            in_public = connection.execute(
                text(
                    "select count(*) from information_schema.tables"
                    " where table_schema = 'public' and table_name = 'evaluation_cases'"
                )
            ).scalar_one()
        assert landed == 1, "the migrations did not land in the private test schema"
        assert not in_public, (
            "the migrations leaked into `public`, so every assertion below would be"
            " reading the wrong schema"
        )

        from app.services.reference_data import bootstrap

        session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
        try:
            bootstrap.seed_reference_data(session)
            role_code = session.execute(
                text("select code from roles order by rank desc, code limit 1")
            ).scalar_one()
            test_definition_id = session.execute(
                text("select id from test_definitions order by sequence_no limit 1")
            ).scalar_one()
            built = _build_laboratories(session, role_code, test_definition_id)
            session.commit()
        finally:
            session.close()

        _grant_probe_role(engine)

        yield World(engine, built, role_code)
    finally:
        app_database.engine = original_engine
        migrations.engine = original_migration_engine
        try:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
                connection.execute(text(f'DROP ROLE IF EXISTS "{PROBE_ROLE}"'))
        finally:
            engine.dispose()


def _build_laboratories(session, role_code, test_definition_id) -> dict:
    """Two laboratories, each with one row in every protected table.

    One row per table per laboratory is what makes the assertions below exact:
    a scope that leaks shows 2, and a scope that denies shows 0.
    """
    built: dict = {}
    metadata = Base.metadata

    def insert(table: str, **values):
        session.execute(metadata.tables[table].insert().values(**values))

    for suffix in ("A", "B"):
        laboratory_id = uuid.uuid4()
        insert("laboratories", id=laboratory_id, name=f"RLS laboratory {suffix}", code=f"RLS-{suffix}", is_active=True)

        user_id = uuid.uuid4()
        insert(
            "users",
            id=user_id,
            email=f"rls-{suffix.lower()}@example.test",
            full_name=f"RLS user {suffix}",
            user_code=f"rls{suffix.lower()}2026{1 if suffix == 'A' else 2:03d}",
            role_code=role_code,
            is_active=True,
            is_demo=False,
            is_email_verified=True,
            auth_provider="local",
            laboratory_id=laboratory_id,
        )

        instrument_id = uuid.uuid4()
        insert(
            "instruments",
            id=instrument_id,
            model=f"RLS-{suffix}-30000",
            instrument_class="III",
            max_capacity=30000,
            min_capacity=200,
            e=10,
            d=10,
            unit="g",
            is_electronic=True,
            is_multi_range=False,
            is_multi_interval=False,
            has_tare_device=False,
            has_zero_device=True,
            has_level_indicator=False,
            configuration={},
        )

        case_id = uuid.uuid4()
        insert(
            "evaluation_cases",
            id=case_id,
            application_no=f"RLS-CASE-{suffix}",
            title=f"RLS case {suffix}",
            instrument_id=instrument_id,
            laboratory_id=laboratory_id,
            status="DRAFT",
            revision_no=1,
            priority="NORMAL",
        )

        test_instance_id = uuid.uuid4()
        insert(
            "test_instances",
            id=test_instance_id,
            case_id=case_id,
            test_definition_id=test_definition_id,
            sequence_no=1,
            applicability_status="APPLICABLE",
            status="PENDING",
            result_status="PENDING",
            is_waived=False,
        )

        attachment_id = uuid.uuid4()
        insert(
            "attachments",
            id=attachment_id,
            case_id=case_id,
            original_filename=f"rls-{suffix}.png",
            storage_key=f"rls/{suffix}.png",
            storage_backend="local",
            category="nameplate_photograph",
            category_source="manual",
            is_malware_scanned=False,
        )

        report_id = uuid.uuid4()
        insert(
            "generated_reports",
            id=report_id,
            case_id=case_id,
            report_no=f"RLS-REPORT-{suffix}",
            revision_no=1,
            case_revision_no=1,
            status="DRAFT",
            is_immutable=False,
            data_snapshot={},
            signature_status="NOT_SIGNED",
        )

        equipment_id = uuid.uuid4()
        insert(
            "test_equipment",
            id=equipment_id,
            code=f"RLS-EQ-{suffix}",
            name=f"RLS weights {suffix}",
            equipment_type="WEIGHT_SET",
            is_active=True,
            laboratory_id=laboratory_id,
        )

        insert("test_observations", test_instance_id=test_instance_id, observation_no=1, input_payload={"load": "10000"})
        insert(
            "calculation_runs",
            test_instance_id=test_instance_id,
            test_code="T-WP",
            engine_version="rls-test",
            input_snapshot={},
            intermediates={},
            outputs={},
            is_valid=True,
        )
        insert("compliance_results", test_instance_id=test_instance_id, status="PASS")
        insert(
            "manual_overrides",
            test_instance_id=test_instance_id,
            previous_status="FAIL",
            new_status="PASS",
            reason="RLS fixture",
            is_active=True,
        )
        insert("environmental_conditions", case_id=case_id, label=f"RLS bench {suffix}")
        insert("workflow_actions", case_id=case_id, action="CREATED")
        insert("ai_events", case_id=case_id, feature_code="summarise", provider="stub", degraded=False)
        insert("case_assignments", case_id=case_id, user_id=user_id, role_code=role_code, is_active=True)
        insert("report_revisions", report_id=report_id, revision_no=1, format="PDF", storage_key=f"rls/{suffix}.pdf")
        insert("attachment_links", attachment_id=attachment_id, case_id=case_id)
        insert("test_equipment_usage", case_id=case_id, equipment_id=equipment_id)
        insert("audit_logs", event_type="CASE_CREATED", entity_type="evaluation_case", laboratory_id=laboratory_id)

        built[f"laboratory_{suffix.lower()}"] = laboratory_id
        built[f"case_{suffix.lower()}"] = case_id
        built[f"instrument_{suffix.lower()}"] = instrument_id

    return built


def _grant_probe_role(engine) -> None:
    """A stand-in for Supabase's `anon`/`authenticated`: a role that is not the
    table owner and therefore subject to every policy."""
    with engine.begin() as connection:
        connection.execute(text(f'DROP ROLE IF EXISTS "{PROBE_ROLE}"'))
        connection.execute(text(f'CREATE ROLE "{PROBE_ROLE}"'))
        current_role = connection.execute(text("select current_user")).scalar_one()
        connection.execute(text(f'GRANT "{PROBE_ROLE}" TO "{current_role}"'))
        connection.execute(text(f'GRANT USAGE ON SCHEMA "{TEST_SCHEMA}" TO "{PROBE_ROLE}"'))
        connection.execute(
            text(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA'
                f' "{TEST_SCHEMA}" TO "{PROBE_ROLE}"'
            )
        )


class Probe:
    """A connection acting as the probe role, scoped to a laboratory."""

    def __init__(self, engine, laboratory_id):
        self.connection = engine.connect()
        self.transaction = self.connection.begin()
        self.connection.execute(text(f'SET LOCAL ROLE "{PROBE_ROLE}"'))
        self.connection.execute(text(f'SET LOCAL search_path TO "{TEST_SCHEMA}"'))
        self.set_scope(laboratory_id)

    def set_scope(self, laboratory_id) -> None:
        # The three-argument form is transaction-local, exactly as
        # apply_session_scope uses it, so a pooled connection cannot carry one
        # request's laboratory into the next.
        self.connection.execute(
            text("select set_config(:setting, :value, true)"),
            {"setting": rls.LAB_SETTING, "value": "" if laboratory_id is None else str(laboratory_id)},
        )

    def count(self, table: str) -> int:
        return self.connection.execute(text(f"select count(*) from {table}")).scalar_one()

    def counts(self) -> dict[str, int]:
        return {table: self.count(table) for table in TABLES}

    def close(self) -> None:
        try:
            self.transaction.rollback()
        finally:
            self.connection.close()


def probe(engine, laboratory_id) -> Probe:
    return Probe(engine, laboratory_id)


def test_the_policies_are_installed_in_the_catalogue(world):
    with world.engine.connect() as connection:
        rows = connection.execute(
            text(
                "select c.relname, c.relrowsecurity, c.relforcerowsecurity"
                " from pg_class c join pg_namespace n on n.oid = c.relnamespace"
                " where n.nspname = :schema and c.relname = any(:tables)"
            ),
            {"schema": TEST_SCHEMA, "tables": TABLES},
        ).all()
        policies = connection.execute(
            text(
                "select tablename, policyname, cmd from pg_policies"
                " where schemaname = :schema"
            ),
            {"schema": TEST_SCHEMA},
        ).all()

    protected = {name: (enabled, forced) for name, enabled, forced in rows}
    assert set(protected) == set(TABLES)
    assert all(enabled for enabled, _ in protected.values()), "every table must have RLS on"
    assert not any(forced for _, forced in protected.values()), (
        "FORCE ROW LEVEL SECURITY would apply the policies to the owner too and"
        " break the API; the owner bypass is the service-role separation"
    )

    installed = {}
    for tablename, policyname, _cmd in policies:
        installed.setdefault(tablename, []).append(policyname)
    assert set(installed) == set(TABLES)
    for table, names in installed.items():
        assert names == [rls.POLICY_NAME], f"{table} has unexpected policies: {names}"


def test_a_scoped_connection_sees_only_its_own_laboratory(world):
    for laboratory_id, label in ((world.laboratory_a, "A"), (world.laboratory_b, "B")):
        connection = probe(world.engine, laboratory_id)
        try:
            seen = connection.counts()
        finally:
            connection.close()
        wrong = {table: count for table, count in seen.items() if count != 1}
        assert not wrong, f"scope {label} should see exactly its own row: {wrong}"


def test_an_unscoped_connection_sees_nothing(world):
    connection = probe(world.engine, world.laboratory_a)
    try:
        connection.set_scope(None)
        seen = connection.counts()
    finally:
        connection.close()

    leaked = {table: count for table, count in seen.items() if count != 0}
    assert not leaked, f"an unset scope must deny, not default to everything: {leaked}"


def test_a_scope_that_matches_nothing_sees_nothing(world):
    connection = probe(world.engine, uuid.uuid4())
    try:
        seen = connection.counts()
    finally:
        connection.close()

    assert set(seen.values()) == {0}


def test_the_owner_is_deliberately_unaffected(world):
    """The backend connects as the schema owner. If this ever started returning
    0 the API would go blank, which is why the policies are not FORCEd."""
    with world.engine.connect() as connection:
        for table in TABLES:
            count = connection.execute(text(f"select count(*) from {table}")).scalar_one()
            assert count == 2, f"{table}: the owner must see both laboratories, saw {count}"


def test_a_read_of_another_laboratorys_row_returns_nothing(world):
    other = world.other_case()
    connection = probe(world.engine, world.laboratory_a)
    try:
        found = connection.connection.execute(
            text("select application_no from evaluation_cases where id = :id"),
            {"id": other},
        ).all()
        visible = connection.count("evaluation_cases")
    finally:
        connection.close()

    assert found == [], "laboratory A must not be able to name laboratory B's case"
    assert visible == 1, "and the row is invisible, not merely unnamed"


def test_a_write_inside_the_scope_is_allowed(world):
    connection = probe(world.engine, world.laboratory_a)
    try:
        with connection.connection.begin_nested():
            connection.connection.execute(
                text(
                    "insert into evaluation_cases"
                    " (id, application_no, title, instrument_id, laboratory_id, status,"
                    "  revision_no, priority, created_at, updated_at)"
                    " values (:id, 'RLS-WRITE-A', 'Written under scope', :instrument,"
                    " :laboratory, 'DRAFT', 1, 'NORMAL', now(), now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "instrument": world.instrument_a,
                    "laboratory": world.laboratory_a,
                },
            )
        assert connection.count("evaluation_cases") == 2, (
            "the new row is visible alongside laboratory A's own row, and still"
            " not laboratory B's"
        )
        own = connection.connection.execute(
            text("select count(*) from evaluation_cases where application_no = 'RLS-WRITE-A'")
        ).scalar_one()
        assert own == 1
    finally:
        connection.close()


def test_a_write_outside_the_scope_is_rejected(world):
    """WITH CHECK, not just USING: a scoped connection cannot plant a row that
    belongs to another laboratory."""
    connection = probe(world.engine, world.laboratory_a)
    try:
        with pytest.raises(sa.exc.ProgrammingError) as error:
            with connection.connection.begin_nested():
                connection.connection.execute(
                    text(
                        "insert into evaluation_cases"
                        " (id, application_no, title, instrument_id, laboratory_id, status,"
                        "  revision_no, priority, created_at, updated_at)"
                        " values (:id, 'RLS-WRITE-B', 'Written for another laboratory',"
                        " :instrument, :laboratory, 'DRAFT', 1, 'NORMAL', now(), now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "instrument": world.instrument_a,
                        "laboratory": world.laboratory_b,
                    },
                )
        assert "row-level security" in str(error.value), str(error.value)
    finally:
        connection.close()


def test_a_row_cannot_be_moved_to_another_laboratory(world):
    connection = probe(world.engine, world.laboratory_a)
    try:
        with pytest.raises(sa.exc.ProgrammingError) as error:
            with connection.connection.begin_nested():
                connection.connection.execute(
                    text("update evaluation_cases set laboratory_id = :other where id = :id"),
                    {"other": world.laboratory_b, "id": world.case_a},
                )
        assert "row-level security" in str(error.value), str(error.value)
    finally:
        connection.close()


def test_a_delete_outside_the_scope_touches_nothing(world):
    connection = probe(world.engine, world.laboratory_b)
    try:
        with connection.connection.begin_nested():
            result = connection.connection.execute(
                text("delete from evaluation_cases where id = :id"), {"id": world.case_a}
            )
        assert result.rowcount == 0
        assert connection.count("evaluation_cases") == 1
    finally:
        connection.close()