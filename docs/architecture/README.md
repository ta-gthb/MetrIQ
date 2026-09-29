# MetrIQ Architecture

MetrIQ automates OIML R 76 type-evaluation test reports for non-automatic
weighing instruments. The governing principle is that **the compliance result
is produced only by a deterministic engine over versioned rule data**. AI is a
bounded, advisory layer that never decides compliance (PRD 12.5, 27.2).

## 1. System context

```
+----------------+        HTTPS         +---------------------------+
| Engineer /     | -------------------> | MetrIQ (FastAPI + static  |
| Reviewer /     | <------------------- | frontend, same origin)    |
| Approver /     |   JSON + PDF/DOCX    +-------------+-------------+
| Auditor /      |                                    |
| Lab Admin      |                                    |
+----------------+                         +----------+----------+
                                           |                     |
                                  +--------v--------+   +--------v---------+
                                  | PostgreSQL      |   | Object storage   |
                                  | (Supabase) or   |   | (Supabase bucket |
                                  | SQLite for demo |   | or local disk)   |
                                  +-----------------+   +------------------+
                                           |
                                  +--------v------------------+
                                  | AI provider (optional)    |
                                  | stub | OpenAI-compatible  |
                                  +---------------------------+
```

## 2. Containers

| Container | Technology | Responsibility |
| --- | --- | --- |
| Web UI | Vanilla ES modules served by FastAPI | Workflow, observation entry, "how calculated" panel, reports, admin |
| API | FastAPI 0.115 / Pydantic 2 | Auth, RBAC, case workflow, test engine, calculation, compliance, reporting |
| Database | PostgreSQL (Supabase) / SQLite | All structured records, rule data, audit trail |
| Storage | Supabase Storage / local disk | Evidence uploads and generated PDF/DOCX artefacts |
| AI provider | Stub (default) or OpenAI-compatible HTTP | Extraction, anomaly warnings, classification, R 76 assistant |

There is no build step. `backend/app/main.py` mounts `frontend/` at `/`, so the
UI and API share an origin and no CORS or token-in-URL workaround is needed for
normal use.

### Frontend conventions

The whole palette lives in `css/app.css` as custom properties: `:root` is the
dark control surface and `[data-theme="light"]` restates it, so no component
rule branches on the theme. `js/theme.js` owns the switch - the choice is kept
in `localStorage["metriq-theme"]`, the operating-system preference is the
default until the operator chooses, and a tiny inline bootstrap in each page
head sets `<html data-theme>` before the first paint so the theme never
flashes. Any element carrying `data-theme-toggle` becomes a switch, and the
shared topbar rendered by `ui.renderShell()` places one on every application
page.

`js/clock.js` repaints every `[data-clock]` element once a second and corrects
the browser clock by the offset it observes against `GET /health`'s
`server_time_utc`, so a workstation with a wrong system time still displays the
platform's date and time. Record timestamps are stored in UTC and rendered in
the operator's local timezone by `ui.fmtDate`.

### The public home page

`index.html` is the one page served before anyone has signed in. It introduces
the platform (what it evaluates, under which standard, and how the rule set is
governed), lists its capabilities, and shows this deployment's activity.
`js/home.js` reads that activity from `GET /api/v1/platform/statistics`, the
only endpoint that answers without a token, and re-reads it on the interval the
payload publishes (15 seconds), pausing while the tab is hidden.

That endpoint is deliberately aggregate: counts, time windows, and the
configuration facts the platform already publishes (`standards.framework`, the
active ruleset and its basis). Nothing that identifies an evaluation, party,
instrument or person is in the response, and `tests/test_platform_statistics.py`
enforces that by walking the payload and refusing anything that is not a number,
a boolean, an ISO timestamp or one of the published constants.

The page carries the platform's official mark, held in `assets/` as raster
derivatives of the one artwork: `logo.png` is the full lockup and is used where
the name is read with it (the hero and the startup overlay), `logo-emblem.png`
is the emblem alone and is used wherever the mark sits beside the product name
(the page bars, the sidebar and the sign-in card), and `favicon.ico` and
`apple-touch-icon.png` are cut from the same source. The emblem is placed by
height with `width: auto`, so the wide lockup is never squeezed into a square
box.

The startup animation is an overlay in `index.html` moved entirely by
`css/app.css`: it fades itself out and clears itself from hit-testing, so the
page is never left covered even if no script runs, and a visitor whose system
asks for reduced motion never sees it. `js/splash-boot.js` decides before the
first paint that it plays once per session, and `js/home.js` removes the
finished node. `tests/test_brand_assets.py` holds all of that.

## 3. Backend layout

```
backend/app/
  main.py            app factory, middleware, exception handlers, static mount
  config.py          pydantic-settings; every secret comes from the environment
  database.py        engine, session, JSON type (JSONB on PostgreSQL)
  models/            SQLAlchemy 2.0 declarative models
  schemas/           Pydantic request/response contracts
  routers/           HTTP surface (one module per domain)
  dependencies/      get_current_user, permission guards
  security/          passwords, JWT, permission catalogue, record scope, audit helpers
  rules/             rule expression evaluator + versioned rule JSON
  services/
    test_engine/       applicability -> test plan generation
    calculation_engine/ per-test deterministic calculators (decimal.Decimal)
    compliance_engine/ comparators (abs_lte, lte, gte, eq, range) -> PASS/FAIL
    report_engine/     snapshot -> PDF (reportlab) + DOCX (python-docx) -> hash
    ai_service/        provider abstraction: base + stub + openai
    audit_service/     append-only audit log, workflow log, notifications
    metrology_service/ orchestration: resolve MPE, evaluate a test, summarise a case
    platform_statistics/ aggregate figures for the public home page (cached briefly)
```

## 4. Authentication and authorization flow

Production signs in through Supabase Auth (audit item 4); MetrIQ's own password
check survives only for development and the demonstration deployment.

```text
Browser --(password, apikey)--> Supabase Auth
        <-- Supabase access token (ES256, verified against the project JWKS)

Browser --(POST /auth/session, Bearer <supabase token>)--> API
                                          decode_token()   algorithm allow-list,
                                                           signature, iss, aud,
                                                           role not anon/service_role
                                          resolve_user_for_principal()
                                                           by supabase_user_id, else
                                                           email; linked on first use
        <-- MetrIQ access_token (type=access) + refresh_token (HttpOnly cookie)

Client  --(Authorization: Bearer <metrIQ access>)--> API
                                          decode_token(expected_type="access")
                                          require_permission(...) -> 403
                                          laboratory_filter() / case_visible() -> scope
```

Development and demonstration instead post to `/auth/login`, where bcrypt is
checked against `users.password_hash`, and receive the same MetrIQ session.

* Supabase authenticates, MetrIQ authorises. The provider's token is exchanged
  for a MetrIQ session rather than passed through, so the provider's token
  lifetime never becomes the session length and its refresh token is discarded
  instead of being kept in page storage (item 13).
* `AUTH_PROVIDER=hybrid` accepts both Supabase-issued and locally issued JWTs;
  `supabase` accepts only the former. In production without `DEMO_MODE`,
  `/auth/login` answers 403, so a session can only come from the exchange.
* Roles and permissions come from `users.role_code` alone. No claim in a token -
  `role`, `app_metadata` or `user_metadata` - can elevate a session.
* Access and refresh tokens carry a `type` claim so a refresh token can never be
  used as an access token and vice versa.
* Full detail: `docs/architecture/supabase-auth.md`.
* Authorization is enforced in three layers: role permission check, laboratory
  scope, and record scope (engineer may only edit cases assigned to them).
* Those three layers are application-side. A fourth, database-side layer - the
  `laboratory_scope` row-level policies on the 18 laboratory tables - confines
  every database role that is not the table owner, so a connection that bypasses
  the API still cannot read across laboratories. See
  `docs/architecture/row-level-security.md`.
* Permission catalogue: 34 codes across 6 roles (`routers/admin.py` exposes the
  matrix; `scripts/seed_identity.py` seeds it).

## 5. Data model

Key tables (35 total). Full ERD:

```mermaid
erDiagram
  LABORATORIES ||--o{ USERS : employs
  LABORATORIES ||--o{ EVALUATION_CASES : owns
  USERS ||--o{ EVALUATION_CASES : "engineer/reviewer/approver"
  MANUFACTURERS ||--o{ INSTRUMENTS : makes
  INSTRUMENTS ||--o{ INSTRUMENT_RANGES : has
  INSTRUMENTS ||--o{ EVALUATION_CASES : "evaluated by"
  APPLICANTS ||--o{ EVALUATION_CASES : applies
  STANDARDS ||--o{ STANDARD_VERSIONS : has
  STANDARD_VERSIONS ||--o{ RULES : defines
  RULES ||--o{ RULE_VERSIONS : versions
  STANDARD_VERSIONS ||--o{ TEST_DEFINITIONS : catalogue
  STANDARD_VERSIONS ||--o{ EVALUATION_CASES : "evaluated against"
  REPORT_TEMPLATES ||--o{ REPORT_TEMPLATE_VERSIONS : versions
  EVALUATION_CASES ||--o{ CASE_ASSIGNMENTS : history
  EVALUATION_CASES ||--o{ ENVIRONMENTAL_CONDITIONS : records
  EVALUATION_CASES ||--o{ TEST_INSTANCES : contains
  TEST_DEFINITIONS ||--o{ TEST_INSTANCES : instantiates
  TEST_INSTANCES ||--o{ TEST_OBSERVATIONS : records
  TEST_INSTANCES ||--o{ CALCULATION_RUNS : "audit of calculations"
  TEST_INSTANCES ||--o| COMPLIANCE_RESULTS : produces
  TEST_INSTANCES ||--o{ MANUAL_OVERRIDES : "governed exception"
  EVALUATION_CASES ||--o{ ATTACHMENTS : evidence
  EVALUATION_CASES ||--o{ GENERATED_REPORTS : produces
  GENERATED_REPORTS ||--o{ REPORT_REVISIONS : artefacts
  USERS ||--o{ AUDIT_LOGS : acts
  EVALUATION_CASES ||--o{ WORKFLOW_ACTIONS : transitions
  EVALUATION_CASES ||--o{ AI_EVENTS : "advisory outputs"
```

Design choices:

* **Versioned rules.** A case stores `standard_version_id`; rule data lives in
  `rules`/`rule_versions` and is loaded from JSON. Historical cases keep the
  version they were created with, so a later rule change cannot silently alter
  an old result.
* **Exact decimals.** Every numeric column is `Numeric`, and the engine computes
  with `decimal.Decimal` (28 significant digits). No float arithmetic touches a
  compliance decision.
* **Immutable results.** `calculation_runs` and `compliance_results` are
  append-only; a manual override writes a new `manual_overrides` row and leaves
  the automated result recoverable.
* **JSON columns** use `JSON` and are mapped to `JSONB` on PostgreSQL via
  `JSON().with_variant(JSONB, "postgresql")`.

## 6. Storage model

| Backend | Used when | Notes |
| --- | --- | --- |
| `local` | demo, tests | files under `STORAGE_LOCAL_PATH`; downloads go through `GET /attachments/{id}/download?token=...` with an HMAC-signed key and TTL |
| `supabase` | production | private bucket `STORAGE_BUCKET`; the API returns short-lived signed URLs |

Uploads are validated by extension, MIME type and size (`MAX_UPLOAD_BYTES`),
hashed with SHA-256 and recorded in `attachments`. Generated report artefacts
are written under `REPORT_STORAGE_PATH` and referenced by `report_revisions`.

## 7. Report-generation pipeline

```
case (approved) 
   -> build_report_snapshot()      frozen JSON: versions, cover, applicant,
                                   instrument, conditions, tests, results,
                                   rules, evidence index
   -> content_hash = sha256(canonical_json(snapshot minus volatile meta))
   -> verification_code = first 12 hex chars, upper-cased
   -> render_pdf / render_docx     deterministic renderers over the snapshot
   -> GeneratedReport + ReportRevision rows (sha256 of each artefact)
   -> finalize: is_immutable = true, locked_at/locked_by set
```

Finalization is **idempotent**: if an immutable report already exists for the
current case revision it is returned unchanged, so the hash and verification
code stay stable. Volatile metadata (`generated_at`) is excluded from the
content hash. A change to an approved case requires a new case revision, which
yields a new report revision.

## 8. AI service boundary

```
router -> get_ai_service(db, actor) -> AIProvider protocol
                                        |- StubProvider (default, offline)
                                        '- OpenAIProvider (HTTP, optional)
```

* The service always writes an `ai_events` row with provider, model, payload
  summary, confidence and (later) the human disposition.
* Every feature is individually toggleable via system settings; failures
  degrade to "AI unavailable" and never block calculation, workflow or
  reporting.
* Feature codes: `nameplate_ocr`, `anomaly_detection`,
  `document_classification`, `r76_assistant`.
* See `docs/ai/` for the full contract.

## 9. Request lifecycle

1. `request_context` middleware assigns `X-Request-ID` and times the request.
2. Authentication dependency resolves the principal (no DB hit beyond the user
   lookup) and rejects inactive users.
3. Permission guard runs before the handler body.
4. Record scope (`laboratory_filter`, `case_visible`, `case_editable_by`) is
   applied inside the handler.
5. Mutations write an `audit_logs` row (and a `workflow_actions` row for status
   transitions) in the same transaction.

## 10. Standards coverage and governance artefacts

| Artefact | Purpose |
| --- | --- |
| `docs/architecture/oiml-coverage-matrix.md` | Generated traceability matrix: clause -> test -> rule -> formula -> limit -> PASS/FAIL logic -> report section -> automated test. Regenerate with `python -m scripts.build_coverage_matrix` from `backend/`; `tests/test_coverage_matrix.py` fails if it drifts from the rule data. |
| `docs/architecture/ruleset-governance.md` | The ruleset lifecycle, the review records a metrology reviewer signs, and the activation gate. |
| `docs/architecture/report-mapping.md` | How clause-to-report mapping is validated, and the golden report fixtures that pin the rendered output. |
| `docs/architecture/supabase-auth.md` | Supabase Auth as the production identity provider: the token exchange, what is verified before a token is trusted, how a Supabase identity is linked to a MetrIQ user, password reset, and what is deliberately out of scope. |
| `docs/architecture/row-level-security.md` | The database-level laboratory policies: what they cover, the deny-by-default scope setting, why the schema owner is deliberately exempt, and the unit and PostgreSQL integration tests (plus the CI job) that prove cross-laboratory reads and writes are refused. |

A test that the engine cannot execute is listed in the catalogue, the matrix and
the test plan with its implementation status and the reason, rather than being
hidden.
