# Deployment Guide

## 1. Local setup

Prerequisites: Python 3.12+, and SQLite for the demo (no server required).

```bash
git clone <repository> metriq && cd metriq
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r backend/requirements.txt
```

Initialise and seed the database, then create the administrator:

```bash
cd backend
python scripts/init_db.py        # create the schema
python scripts/seed_rules.py     # load versioned rule sets and the test catalogue
python scripts/seed_identity.py  # roles, permissions, grants
python scripts/seed_db.py        # demo laboratory, masters, demo case
python scripts/create_admin.py --email admin@metriq.local \
       --name "Platform Administrator" --password "MetrIQ@2026"
```

Run the application (the API also serves the frontend):

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
# open http://localhost:8000/login.html
```

Useful scripts:

| Script | Purpose |
| --- | --- |
| `scripts/init_db.py` | Create all tables (idempotent) |
| `scripts/reset_db.py` | Drop and recreate every table (destructive) |
| `scripts/seed_rules.py` | Load `app/rules/data/*.json` as standards, versions, rules, test definitions, templates |
| `scripts/seed_identity.py` | Seed the 34 permissions, 6 roles and 113 grants |
| `scripts/seed_db.py` | Demo laboratory, manufacturer, applicant, instrument, environmental conditions, demo case |
| `scripts/create_admin.py` | Create or update the platform administrator |

All scripts run from any working directory and read `DATABASE_URL` from the
environment or the nearest `.env`.

### 1.1 Tests

```bash
cd backend
python -m pytest -q
```

The suite (131 tests) covers the calculation engine boundaries, the API
contract, authentication/authorization, the review workflow and the AI
fallback behaviour. It runs against a throwaway SQLite database; no external
services are needed.

## 2. Environment variables

Copy `.env.example` to `.env` and fill in the values. Everything has a safe
development default except the production secrets.

### Core

| Variable | Default | Notes |
| --- | --- | --- |
| `APP_NAME` | `MetrIQ` | Shown in the UI and reports |
| `ENVIRONMENT` | `development` | `development` / `staging` / `production` / `test` |
| `DEBUG` | `true` | Lenient boolean parsing, so `DEBUG=release` does not break startup |
| `API_V1_PREFIX` | `/api/v1` | API mount point |

### Database

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./metriq.db` | Use the Supabase **connection pooler** URL in production (`postgresql://...`) |
| `SQL_ECHO` | `false` | Log SQL |

### Authentication

| Variable | Default | Notes |
| --- | --- | --- |
| `AUTH_PROVIDER` | `hybrid` | `supabase`, `local` or `hybrid` |
| `JWT_SECRET` | `change-me-in-production` | **Must** be replaced in production |
| `JWT_ALGORITHM` | `HS256` | |
| `ACCESS_TOKEN_TTL_MINUTES` | `480` | |
| `SUPABASE_URL` | - | Project URL |
| `SUPABASE_ANON_KEY` | - | Frontend/anonymous key |
| `SUPABASE_SERVICE_ROLE_KEY` | - | Server-side key; never exposed to the browser |
| `SUPABASE_JWKS_URL` | - | JWKS endpoint used to verify Supabase JWTs |
| `SUPABASE_JWT_SECRET` | - | Only for legacy HS256 Supabase projects |

### Storage

| Variable | Default | Notes |
| --- | --- | --- |
| `STORAGE_BACKEND` | `local` | `local` or `supabase` |
| `STORAGE_BUCKET` | `metriq-evidence` | Private bucket |
| `STORAGE_LOCAL_PATH` | `./var/storage` | Local disk path |
| `REPORT_STORAGE_PATH` | `./var/reports` | Generated PDF/DOCX |
| `MAX_UPLOAD_BYTES` | `26214400` (25 MB) | |
| `SIGNED_URL_TTL_SECONDS` | `900` | Signed download lifetime |
| `REQUIRED_EVIDENCE_CATEGORIES` | `nameplate_photograph,test_setup_photograph` | Photographs that must be attached before submission |

### AI

| Variable | Default | Notes |
| --- | --- | --- |
| `AI_ENABLED` | `true` | Master switch |
| `AI_PROVIDER` | `stub` | `stub`, `openai`, `azure_openai`, `custom` |
| `AI_VISION_MODEL` | `gpt-4o-mini` | Nameplate extraction |
| `AI_TEXT_MODEL` | `gpt-4o-mini` | Anomaly/assistant |
| `AI_API_KEY` | - | Only needed for a hosted provider |
| `AI_BASE_URL` | - | OpenAI-compatible gateway |
| `AI_TIMEOUT_SECONDS` | `30` | |
| `AI_MIN_CONFIDENCE` | `0.75` | Below this, output is flagged low-confidence |

### Reporting and CORS

| Variable | Default | Notes |
| --- | --- | --- |
| `REPORT_LABORATORY_NAME` / `REPORT_LABORATORY_CODE` | demo values | Printed on the report cover |
| `REPORT_DISCLAIMER` | built-in text | Printed on the report |
| `CORS_ORIGINS` | localhost origins | Comma-separated list |
| `CORS_ALLOW_ORIGIN_REGEX` | `https://.*\.vercel\.app` | |

## 3. Supabase configuration

1. Create a Supabase project and copy the connection string
   (**Settings -> Database -> Connection pooling**, port `6543`) into
   `DATABASE_URL`.
2. Create a **private** storage bucket named `metriq-evidence`; set
   `STORAGE_BACKEND=supabase` and `STORAGE_BUCKET=metriq-evidence`, and provide
   `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.
3. Enable email/password auth if you want Supabase to issue JWTs, then set
   `AUTH_PROVIDER=supabase` and `SUPABASE_JWKS_URL` to
   `https://<project>.supabase.co/auth/v1/.well-known/jwks.json`.
4. Users created in Supabase must exist in MetrIQ's `users` table with a
   matching `supabase_user_id` (or the same email) before the API will resolve a
   principal. Link them with `POST /api/v1/users` (server-side) and update
   `supabase_user_id` directly, or keep `AUTH_PROVIDER=hybrid` so local
   accounts continue to work.

## 4. Render configuration

`render.yaml` at the repository root defines the service. Create a **Blueprint**
in Render and point it at the repository.

```yaml
services:
  - type: web
    name: metriq-api
    runtime: python
    plan: starter
    buildCommand: pip install -r backend/requirements.txt
    preDeployCommand: python backend/scripts/init_db.py
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT --app-dir backend
    healthCheckPath: /health
```

Set `DATABASE_URL` and `JWT_SECRET` in the Render dashboard (they are marked
`sync: false` / `generateValue: true`). After the first deploy, run the seed
scripts once from the Render shell:

```bash
python backend/scripts/seed_rules.py
python backend/scripts/seed_identity.py
python backend/scripts/seed_db.py          # optional demo data
python backend/scripts/create_admin.py --email you@lab.example --name "Your Name" --password "…"
```

Because the backend serves `frontend/`, the Render URL alone is a complete
deployment. `ALLOWED_HOSTS`/CORS only matter if you add a separate frontend
origin.

## 5. Vercel configuration

`vercel.json` deploys `frontend/` as a static site and proxies `/api/*` and
`/health` to the Render service, so the browser stays same-origin (no CORS, no
token in the URL).

```json
{
  "rewrites": [
    { "source": "/api/:path*", "destination": "https://metriq-api.onrender.com/api/:path*" },
    { "source": "/health", "destination": "https://metriq-api.onrender.com/health" }
  ]
}
```

Change the destination to your Render hostname. Deploy with `vercel --prod`, or
import the repository in the Vercel dashboard and set the root directory to
`frontend`.

## 6. Database initialization and reset

* **Initialize** (safe, additive): `python backend/scripts/init_db.py`. The API
  also calls `Base.metadata.create_all` at startup as a development convenience.
* **Reset** (destructive, drops every table): `python backend/scripts/reset_db.py`
  then re-run the seed scripts. Never run it against a production database.
* **Seed** (idempotent): `seed_rules.py`, `seed_identity.py`, `seed_db.py` may be
  re-run; they upsert by natural key.

## 7. Seed data

`seed_rules.py` loads:

* OIML R 76-1:2006 (`r76-1-2006-v1`, active) - 17 rules, MPE bands, tolerances;
* OIML R 76-2:2007 (`r76-2-2007-v1`) - report format sections;
* an inactive placeholder for the R 76 revision (1.1 committee draft);
* the 11-test MVP catalogue plus 6 phase-2 test definitions (inactive);
* the `R76-2-TYPE-EVAL` report template.

`seed_db.py` creates the demo laboratory, one manufacturer, one applicant, one
class III instrument (Max 30000 g, e = d = 10 g), environmental conditions and a
demo case. All demo accounts use the password `MetrIQ@2026`:

| Email | Role |
| --- | --- |
| `admin@metriq.local` | Super Admin |
| `labadmin@metriq.local` | Laboratory Admin |
| `engineer@metriq.local` | Test Engineer |
| `reviewer@metriq.local` | Technical Reviewer |
| `approver@metriq.local` | Approving Authority |
| `auditor@metriq.local` | Auditor (read-only) |

`seed_identity.py` seeds 34 permission codes, 6 roles and 113 role-permission
grants.

## 8. Backup and recovery

* **Database.** Use the managed backup of Supabase/Render (point-in-time
  recovery on paid plans). For a manual logical backup:
  `pg_dump "$DATABASE_URL" > metriq-$(date +%F).sql`; restore with `psql`.
* **Report artefacts.** `report_revisions.sha256` records the hash of every
  generated file. Back up `REPORT_STORAGE_PATH` (or the storage bucket) together
  with the database so a report can be verified after restore:
  `GET /api/v1/reports/{id}/download` returns the artefact and
  `X-Content-SHA256`, which must match the stored hash.
* **Evidence.** Attachments are content-addressed by SHA-256 in `attachments`;
  after a restore, re-verify by re-hashing the stored file.
* **Recovery order:** restore the database, restore storage, then verify a
  sample of reports against their recorded hashes. Audit logs are append-only
  and are restored with the database.