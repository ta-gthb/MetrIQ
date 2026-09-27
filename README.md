# MetrIQ

**NAWI OIML R 76 Test Report Automation Platform.** MetrIQ takes a non-automatic
weighing instrument through the OIML R 76 type-evaluation workflow - application,
test plan, observations, deterministic error and MPE calculation, technical
review, signatory approval and a signed, hash-verified PDF/DOCX report.

It is not a CRUD application with a PDF export. The differentiators are:

- **A deterministic core.** `PASS`/`FAIL` is produced only by a rule-driven
  engine over versioned rule data using exact `decimal.Decimal` arithmetic. AI
  never decides compliance.
- **Versioned standards.** Every case records the rule-set version and report
  template it was evaluated against, so a later rule change cannot silently
  alter a historical result.
- **Traceability by construction.** Every result cites its rule id and clause;
  every calculation run, override and status transition is written to an
  append-only audit trail.
- **Bounded AI.** Nameplate extraction, anomaly warnings, document
  classification and a retrieval-grounded R 76 assistant - all advisory, all
  toggleable, all usable offline via a deterministic stub provider.

## Quick start

Prerequisites: Python 3.12+. No database server is required for the demo
(SQLite).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r backend/requirements.txt

cd backend
python scripts/init_db.py
python scripts/seed_rules.py
python scripts/seed_identity.py
python scripts/seed_db.py
python scripts/create_admin.py --email admin@metriq.local \
       --name "Platform Administrator" --password "MetrIQ@2026"

python -m uvicorn app.main:app --port 8000
```

Open <http://localhost:8000/login.html>. The backend serves the frontend, so
there is no build step and no second server.

Demo accounts all use the password `MetrIQ@2026`:

| Email | Role |
| --- | --- |
| `admin@metriq.local` | Super Admin |
| `labadmin@metriq.local` | Laboratory Admin / Manager |
| `engineer@metriq.local` | Test Engineer / Metrologist |
| `reviewer@metriq.local` | Technical Reviewer / Verifier |
| `approver@metriq.local` | Approving Authority / Signatory |
| `auditor@metriq.local` | Auditor (read-only) |

## Five-minute demo (SIH script)

1. Sign in as `engineer@metriq.local` and open **Evaluations -> Create** (or the
   seeded case `NAWI-2026-000001`).
2. Register a class III instrument: model, Max/Min, verification scale interval
   `e`, actual scale interval `d`, unit. The test plan is generated from the
   active rule set and the instrument characteristics.
3. In **Instrument**, attach the two mandatory photographs (nameplate and test setup)
   and run the AI nameplate extraction; compare
   the AI reading with the recorded values side by side (advisory, nothing is
   written).
4. In **Execution**, enter weighing-performance observations. The engine shows
   the formula `P = I + 0.5e - dL`, `E = P - L`, `E_c = E - E0`, the per-row
   error, the applicable MPE band and the margin - and the governing rule id and
   clause.
5. Enter one implausible value and run **AI anomaly check**: the row is flagged
   for human review, while the compliance result is unchanged. Confirm or
   dismiss the finding.
6. **Calculate & store**, then **Mark complete** for each applicable test.
7. **Submit for technical review**. Switch to `reviewer@metriq.local`, open the
   case and **Verify** (or **Request correction** with a reason).
8. Switch to `approver@metriq.local`, **Approve**, then **Finalize and lock**.
   The content hash and verification code are frozen.
9. Open **Reports**, search by application number, and download the PDF and DOCX.
   The repository shows the hash, verification code and revision history.
10. Open **Administration -> Audit logs** to show the full history with the
    rule-set and template versions used.

## Features

**Evaluation workflow** - application data, parties and assignments, instrument
characteristics, metrological characteristics with an MPE lookup, environmental
conditions, generated test plan, per-test observation capture, validation
summary, technical review, approval and finalization.

**Test engine** - 11 MVP tests (`T-WP`, `T-REP`, `T-ECC`, `T-ZR`, `T-CREEP`,
`T-TEMP-NL`, `T-SENS`, `T-DISC`, `T-STAB`, `T-CHK-CON`, `T-CHK-ID`) plus 6
inactive phase-2 definitions. Each test definition carries its own input schema,
so the UI renders the correct form (table or checklist) without bespoke code.

**Calculation and compliance** - versioned MPE bands per accuracy class
(`m = load / e`, first band whose upper bound is not exceeded wins), exact
decimal arithmetic, comparators `abs_lte`/`lte`/`gte`/`eq`/`range`, statuses
`PASS`, `FAIL`, `NOT_APPLICABLE`, `INCOMPLETE`, `INVALID`, `WAIVED`, `PENDING`.
See `docs/calculations/`.

**Governance** - role-based access control over 34 permissions and 6 roles,
laboratory and record scope, a reviewer-correction loop, an exceptional
override mechanism that can never set `PASS`/`FAIL`, and an append-only audit
trail with before/after values and reasons.

**Evidence** - two photographs are mandatory for every new evaluation (the instrument
nameplate and the test setup); submission is blocked until both are attached. All
uploads are validated (extension, MIME, size), SHA-256 recorded,
signed download URLs, and advisory AI classification.

**Reports** - a frozen JSON snapshot of versions, results, rules and evidence
rendered to PDF (reportlab) and DOCX (python-docx), with a content hash,
verification code and immutable revisions.

**AI assistance** - provider-abstracted (deterministic offline stub by default,
OpenAI-compatible optional), individually toggleable, fully audited, and never
on the critical path.

## Project structure

```
backend/
  app/
    main.py            FastAPI app; serves the frontend and the API
    config.py          environment-driven settings
    database.py        SQLAlchemy engine/session, JSONB-on-Postgres
    models/            35 tables (identity, org, instrument, standards, case, test, evidence, report, audit)
    schemas/           Pydantic contracts
    routers/           HTTP surface
    dependencies/      auth and permission guards
    security/          passwords, JWT, permission catalogue, record scope
    rules/             expression evaluator + versioned rule JSON
    services/          test_engine, calculation_engine, compliance_engine,
                       report_engine, ai_service, audit_service, metrology_service
  scripts/             init_db, reset_db, seed_rules, seed_identity, seed_db, create_admin
  tests/               131 tests
frontend/              vanilla ES modules, no build step
  *.html               index, login, dashboard, evaluations, evaluation,
                       reports, admin, assistant
  css/app.css          dark control-surface design system
  js/                  api.js, ui.js and one module per page
docs/
  architecture/  calculations/  deployment/  ai/
```

## API

Interactive documentation is served at `/docs` (OpenAPI at `/openapi.json`).
The API prefix is `/api/v1`. Selected endpoints:

| Area | Endpoints |
| --- | --- |
| Auth | `POST /auth/login`, `POST /auth/refresh`, `GET /me`, `GET /me/permissions`, `GET /roles` |
| Dashboard | `GET /dashboard/summary`, `GET /dashboard/pending`, `GET /dashboard/ai-review` |
| Cases | `POST/GET /cases`, `GET/PATCH /cases/{id}`, `POST /cases/{id}/assignments`, `GET/POST /cases/{id}/test-plan`, `GET/POST /cases/{id}/conditions` |
| Tests | `GET /cases/{id}/tests`, `GET/PATCH /tests/{id}`, `PUT /tests/{id}/observations`, `POST /tests/{id}/validate`, `POST /tests/{id}/calculate`, `POST /tests/{id}/anomaly-check`, `POST /tests/{id}/anomaly-disposition`, `POST /tests/{id}/override` |
| Workflow | `POST /cases/{id}/submit`, `/verify`, `/request-correction`, `/approve`, `/reject`, `/finalize`, `/cancel` |
| Reports | `POST /cases/{id}/reports/generate`, `GET /cases/{id}/reports`, `GET /reports`, `GET /reports/{id}/snapshot`, `/revisions`, `/download?fmt=pdf\|docx` |
| Evidence | `POST/GET /cases/{id}/attachments`, `GET /cases/{id}/evidence-requirements`, `GET /attachments/{id}/download`, `POST /attachments/{id}/classify`, `PATCH /attachments/{id}` |
| Standards | `GET /standards`, `/rules`, `/rulesets`, `/test-definitions`, `/report-templates`, `POST /rulesets/{id}/activate`, `POST /calculations/mpe`, `/calculations/preview` |
| AI | `GET/PATCH /ai/features`, `POST /ai/nameplate-extract`, `/ai/anomaly-check`, `/ai/classify`, `/ai/knowledge`, `/ai/disposition` |
| Admin | `GET/POST/PATCH /users`, `POST /users/{id}/reset-password`, `GET/POST/PATCH /laboratories`, `GET /admin/roles`, `/admin/permissions`, `GET/PUT /settings`, `GET /audit-logs` |
| Platform | `GET /health`, `GET /api/v1` |

## Tests

```bash
cd backend
python -m pytest -q      # 131 tests
```

Coverage includes calculation-engine boundaries, the API contract, auth and
authorization, the submit -> verify -> approve -> finalize lifecycle, report
immutability and hash stability, and the AI disable/fallback behaviour.

## Deployment

| Piece | Host | Config |
| --- | --- | --- |
| API (FastAPI) | Render | `render.yaml` |
| UI (static, no build step) | Vercel | `vercel.json` |
| Database (PostgreSQL) | Supabase | `DATABASE_URL` (connection pooler) |
| Evidence + report artefacts | Supabase Storage | `STORAGE_BACKEND=supabase`, `STORAGE_BUCKET` |

The deployed service is stateless: evidence uploads and generated PDF/DOCX
reports are both written to the Supabase bucket, so nothing depends on the
Render filesystem. `vercel.json` proxies `/api/*` to the Render service so the
browser stays same-origin and no CORS configuration is needed.

Full runbook, including the Supabase project setup, the exact environment
variables and a verification checklist: `docs/deployment/README.md`.

## Documentation

- `docs/architecture/` - context, containers, auth flow, ERD, storage, report
  pipeline, AI boundary
- `docs/calculations/` - inputs, formulas, units, rounding, clause references,
  worked and boundary examples, expected results
- `docs/deployment/` - local setup, environment variables, Supabase, Render,
  Vercel, seeds, backup/recovery
- `docs/ai/` - features, provider abstraction, data sent, human verification,
  confidence handling, audit events, limitations, fallback

## Standards notice

The rule values shipped with this repository are **configuration data, not
verified metrological truth**. Every rule version is marked
`pending_domain_review`. Before production use, a qualified metrology authority
must confirm every band, tolerance and clause reference against the controlled
copies of OIML R 76-1:2006 and OIML R 76-2:2007 and the laboratory's approved
procedures. This software is a documentation and workflow tool; it does not
constitute statutory approval.