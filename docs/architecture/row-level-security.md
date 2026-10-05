# Row-level security

Audit item 14 asks for the protected tables to be constrained by laboratory
scope *in the database*, not only in the application: "application-layer RBAC is
useful, but defense-in-depth is stronger when database access is also
constrained by laboratory/tenant scope."

MetrIQ therefore has two independent layers that must agree:

| Layer | Enforces | Applies to | Code |
| --- | --- | --- | --- |
| Application scope | every query the API builds | every HTTP request | `app/security/scope.py` |
| Row-level security | one row never leaves its laboratory | every database role that is **not** the table owner | `app/security/rls.py`, migration `0007` |

Neither layer depends on the other working. A bug that drops the `WHERE`
laboratory filter from a query still cannot cross laboratories through a
non-owner connection, and a policy that was never installed still cannot cross
laboratories through the API.

## What is protected

One policy, `laboratory_scope`, is created on each of the 18 tables that carry
laboratory data. Tables reach their laboratory through four different routes, so
each route has its own predicate shape:

| Route | Tables | Predicate |
| --- | --- | --- |
| Direct | `evaluation_cases`, `users`, `test_equipment`, `audit_logs` | `table.laboratory_id = current_lab()` |
| Via the case | `test_instances`, `attachments`, `case_assignments`, `environmental_conditions`, `test_equipment_usage`, `workflow_actions`, `ai_events`, `generated_reports` | `table.case_id IN (SELECT id FROM evaluation_cases WHERE laboratory_id = current_lab())` |
| Via the test instance | `test_observations`, `calculation_runs`, `compliance_results`, `manual_overrides` | `EXISTS (SELECT 1 FROM test_instances ...)` |
| Via the report / attachment | `report_revisions`, `attachment_links` | `EXISTS (SELECT 1 FROM generated_reports / attachments ...)` |

The subqueries read their parent table, which carries its own policy. That is
deliberate: the alternative is a `SECURITY DEFINER` helper function, and that is
where policies of this kind usually acquire a privilege-escalation bug. Each
predicate is used for both `USING` (which rows may be read or changed) and
`WITH CHECK` (which rows may be written). Without `WITH CHECK` a connection
scoped to one laboratory could still *write* a row that another laboratory would
then see.

Global reference data - `laboratories`, `roles`, `permissions`, the standard and
ruleset catalogue, the test definitions, the report templates - is deliberately
**not** protected. It is shared by every laboratory, and a tenant policy on it
would either hide the standard or let one laboratory edit another's catalogue.

## The scope setting, and why the default is deny

The policies read their laboratory from a PostgreSQL setting:

```sql
nullif(current_setting('metriq.lab_id', true), '')::uuid
```

`true` means "do not raise if unset", and `nullif` turns an unset or cleared
setting into `NULL`. `NULL = anything` is never true, so a connection that has
not declared a laboratory matches **no rows**. The default is deny: forgetting to
set the scope hides everything rather than exposing everything.

The setting is pushed per request by
`app.security.rls.apply_session_scope`, which `get_current_user` calls once the
principal is resolved:

```python
apply_session_scope(db, user.laboratory_id)
```

It uses `set_config(..., true)`, the transaction-local form. A pooled connection
therefore cannot carry one request's laboratory into the next request, and a
principal without a laboratory - the platform system administrator - is not scoped at all
and is authorised by the application layer instead.

## Why the owner is exempt, and why there is no escape hatch

PostgreSQL exempts a table's owner from that table's policies unless the table
is marked `FORCE ROW LEVEL SECURITY`. MetrIQ's backend connects as the schema
owner (the Supabase `postgres` role), so:

* the API is **unaffected** and keeps working, which is what keeps a deployment
  from going blank after this migration is applied;
* `anon`, `authenticated` (the roles Supabase's PostgREST issues), a reporting
  user, or any future least-privilege application role **are** confined.

`FORCE ROW LEVEL SECURITY` is deliberately not set. Turning it on today would
apply the policies to the API's own connection, which does not set the scope on
every path (background jobs, migrations, the seeding bootstrap) and would start
returning empty result sets.

The owner bypass *is* the service-role separation, so the policies contain no
`metriq.service` flag or similar switch. A flag any role could set would reduce
defense-in-depth to one `SET` statement away from no defense.

Stated plainly: this closes the direct-database path. The API's own path is
constrained by `app/security/scope.py`, not by these policies. Both are needed,
and neither is claimed to substitute for the other.

## Test coverage

`backend/tests/test_row_level_security.py` has two layers.

**Unit (always runs, including the SQLite suite).** Pins the shape of the
policies, checks that every protected table still exists in the models, checks
that each policy has both `USING` and `WITH CHECK`, and - the important one -
compares the runtime policies against the list frozen inside migration `0007`.
That comparison is what forces a *new* migration when a protected table is
added, so a deployed policy and the policy the tests describe cannot drift
apart.

**Integration (opt-in, needs a real PostgreSQL).** SQLite has no row-level
security, so asserting the policies against it would be theatre. Point the suite
at a scratch database:

```powershell
cd backend
$env:METRIQ_RLS_TEST_DATABASE_URL = "postgresql://user:pw@host:5432/scratch"
python -m pytest tests/test_row_level_security.py -q
```

It creates its own schema (`metriq_rls_test`), points `search_path` at it, and
drops only that schema - so it will not touch anything else in the database.
Point it at a scratch database, never at production. CI runs exactly this in the
`rls` job against a `postgres:16` service container, as a freshly created role
that is not the table owner.

The integration tests build two laboratories with one row in every protected
table, then assert:

* a connection scoped to laboratory A sees exactly one row per table, and the
  same for B - no leakage in either direction;
* an unscoped connection sees **nothing** (deny by default);
* a scope that matches no laboratory sees nothing;
* naming another laboratory's case by primary key returns no rows;
* the owner sees both laboratories, because the API depends on that;
* `pg_class.relrowsecurity` is on for all 18 tables, `relforcerowsecurity` is
  off for all of them, and `pg_policies` holds exactly one policy per table;
* an `INSERT` outside the scope is rejected by `WITH CHECK`, an `UPDATE` cannot
  move a row into another laboratory, and a `DELETE` of another laboratory's row
  affects zero rows.

Every one of those assertions is falsifiable, and has been falsified once: with
the policies disabled, all 18 tables leak both laboratories and the tests fail.
A passing run therefore means the policies are doing the work, not that the
fixture happened to be empty.

## Adding a protected table

1. Add the table to the matching map in `app/security/rls.py`.
2. Write a new migration: `python scripts/migrate.py revision "..."` (or
   `alembic revision`), and enable the policy there.
3. `tests/test_row_level_security.py` fails until steps 1 and 2 agree, which is
   the point.

Do not edit migration `0007`: a migration must keep doing what it did on the day
it was written, and `0007` is frozen so that the drift check has something
stable to compare against.