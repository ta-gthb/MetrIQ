# Deployment Guide

Target topology:

| Piece | Host | Notes |
| --- | --- | --- |
| API (FastAPI) | **Render** web service | Stateless; also mounts `frontend/` at `/` |
| UI (static HTML/ES modules) | **Vercel** | Proxies `/api/*` to Render so the browser stays same-origin |
| Database (PostgreSQL) | **Supabase** | Use the connection pooler URI |
| Evidence files + report artefacts | **Supabase Storage** | One private bucket, reached with the service-role key |

Nothing durable is written to the Render filesystem. Evidence uploads and the
generated PDF/DOCX reports both go through the configured storage backend, so a
redeploy or restart cannot lose them.

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

The suite (133 tests) covers the calculation engine boundaries, the API
contract, authentication/authorization, the review workflow, artefact storage
and the AI fallback behaviour. It runs against a throwaway SQLite database; no
external services are needed.

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
| `DATABASE_URL` | `sqlite:///./metriq.db` | Supabase **session pooler** URI in production |
| `SQL_ECHO` | `false` | Log SQL |

### Authentication

| Variable | Default | Notes |
| --- | --- | --- |
| `AUTH_PROVIDER` | `hybrid` | `supabase`, `local` or `hybrid`. Keep `hybrid` in production (see below) |
| `JWT_SECRET` | `change-me-in-production` | **Must** be replaced in production |
| `JWT_ALGORITHM` | `HS256` | |
| `ACCESS_TOKEN_TTL_MINUTES` | `480` | |
| `SUPABASE_URL` | - | Project URL |
| `SUPABASE_ANON_KEY` | - | Frontend/anonymous key |
| `SUPABASE_SERVICE_ROLE_KEY` | - | Server-side key; never exposed to the browser |
| `SUPABASE_JWKS_URL` | - | JWKS endpoint used to verify Supabase JWTs |
| `SUPABASE_JWT_SECRET` | - | Only for legacy HS256 Supabase projects |

> **`AUTH_PROVIDER` must stay `hybrid`.** The API mints its own JWTs at
> `/auth/login`. With `AUTH_PROVIDER=supabase` only Supabase-issued tokens are
> accepted, so every account that signs in through this service is rejected with
> 401 as soon as `SUPABASE_JWT_SECRET` is set. `hybrid` verifies both kinds.

### Storage

| Variable | Default | Notes |
| --- | --- | --- |
| `STORAGE_BACKEND` | `local` | `local` or `supabase` |
| `STORAGE_BUCKET` | `metriq-evidence` | Private bucket, used for evidence **and** reports |
| `STORAGE_LOCAL_PATH` | `./var/storage` | `local` backend only |
| `REPORT_STORAGE_PATH` | `./var/reports` | Legacy: only read when a pre-existing row holds an absolute path |
| `MAX_UPLOAD_BYTES` | `26214400` (25 MB) | |
| `SIGNED_URL_TTL_SECONDS` | `900` | Signed download lifetime |
| `REQUIRED_EVIDENCE_CATEGORIES` | `nameplate_photograph,test_setup_photograph` | Photographs that must be attached before submission |

### CORS

| Variable | Default | Notes |
| --- | --- | --- |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:8000,http://localhost:3000` | Comma-separated exact origins. Add the Vercel production URL |
| `CORS_ALLOW_ORIGIN_REGEX` | `https://.*\.vercel\.app` | Convenient for Vercel preview deployments |

CORS is only involved when the browser calls the API **cross-origin**. The
Vercel rewrite described in section 5 makes the calls same-origin, so no CORS
entry is needed for the deployed UI.

### AI

| Variable | Default | Notes |
| --- | --- | --- |
| `AI_ENABLED` | `true` | Master switch |
| `AI_PROVIDER` | `stub` | `stub`, `openai`, `azure_openai`, `custom` |
| `AI_VISION_MODEL` | `gpt-4o-mini` | Nameplate extraction |
| `AI_TEXT_MODEL` | `gpt-4o-mini` | Anomaly/assistant |
| `AI_API_KEY` | - | Only needed for a hosted provider |

The calculation and compliance engines never depend on AI; `stub` is a complete,
offline implementation and is the right default for a demo.

## 3. Supabase: database and storage

### 3.1 Create the project

1. Create a project at <https://supabase.com/dashboard>. Note the **project
   reference** (the `<ref>` in the URLs) and the database password you set.
2. **Project Settings -> API** gives you `SUPABASE_URL` and `SUPABASE_ANON_KEY`.
   The **`service_role`** key is on the same page - treat it as a root
   credential and never put it in frontend code.

### 3.2 Database connection string

Use **Connect -> Connection pooling -> Session pooler**:

```
postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

Set that as `DATABASE_URL` on Render.

* Use the **session pooler (port 5432)** for a long-running server. The
  transaction pooler (6543) is aimed at short-lived/serverless clients and
  changes session semantics; the direct `db.<ref>.supabase.co` host is IPv6-only
  unless you add the IPv4 add-on.
* Percent-encode reserved characters in the password (`@` -> `%40`, `:` -> `%3A`,
  `/` -> `%2F`).
* The engine already sets `pool_pre_ping=True`, which is what keeps pooled
  connections healthy through the Supabase pooler.

### 3.3 Storage bucket

**Storage -> New bucket**

* Name: `metriq-evidence` (must match `STORAGE_BUCKET`)
* Public: **off**. All access is server-side; downloads are handed out as
  time-limited signed URLs minted by the API.

No storage policies are required: the backend uses the service-role key, which
bypasses RLS. Object layout:

```
cases/<case_id>/<uuid>/<filename>      evidence uploads
reports/<report_no>-R<n>.pdf|docx      generated report artefacts
```

## 4. Render: the API

1. **New -> Blueprint**, point it at the repository. Render reads `render.yaml`
   and creates the `metriq-api` web service.
2. Render prompts for the `sync: false` variables. Supply:

   | Key | Value |
   | --- | --- |
   | `DATABASE_URL` | the Supabase session pooler URI from 3.2 |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `SUPABASE_ANON_KEY` | from Project Settings -> API |
   | `SUPABASE_SERVICE_ROLE_KEY` | from Project Settings -> API |
   | `SUPABASE_JWT_SECRET` | Project Settings -> API -> JWT Settings (optional; leave blank unless you use Supabase Auth) |
   | `SUPABASE_JWKS_URL` | `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json` |
   | `CORS_ORIGINS` | your Vercel URL, e.g. `https://metriq.vercel.app` (optional) |
   | `AI_API_KEY` | blank for the `stub` provider |

   `JWT_SECRET` is generated by Render. `AUTH_PROVIDER`, `STORAGE_BACKEND` and
   `STORAGE_BUCKET` are already set in the blueprint.
3. Deploy. The pre-deploy command runs `init_db.py` and `seed_rules.py`; both are
   idempotent. Note the service URL Render assigns - if it is not
   `https://metriq-api.onrender.com`, update the rewrite destinations in
   `vercel.json`.
4. *(Optional)* Seed demonstration data. From the Render **Shell**:

   ```bash
   python backend/scripts/seed_identity.py
   python backend/scripts/seed_db.py
   ```

   Create the first real administrator instead of seeding demo accounts:

   ```bash
   python backend/scripts/create_admin.py \
     --email you@lab.example --name "Your Name" --password "<strong-password>"
   ```

The Render filesystem is ephemeral and no disk is attached: nothing is lost
because PostgreSQL, evidence and report artefacts all live in Supabase.

## 5. Vercel: the frontend

The UI is plain HTML/CSS/ES modules with no build step, so Vercel only has to
serve `frontend/`.

1. **Add New -> Project**, import the repository.
2. Leave **Root Directory** as the repository root. `vercel.json` sets
   `"outputDirectory": "frontend"`, so the folder is published as-is.
   *If Vercel reports that no output directory was found*, set **Root
   Directory** to `frontend` and **Output Directory** to `.` instead - the
   rewrites in `vercel.json` still apply because it is read from the repository root.*
3. Set the rewrite destination to your Render host. In `vercel.json`:

   ```json
   { "source": "/api/:path*", "destination": "https://metriq-api.onrender.com/api/:path*" }
   ```

   Replace `metriq-api.onrender.com` with the hostname Render gave you, then
   redeploy. The `/health` rewrite must be changed to match.
4. Deploy. The site is then same-origin: the browser calls `/api/v1/...`, Vercel
   proxies to Render, and no CORS configuration is involved.

### Calling the API directly instead

If you would rather not proxy, allow the Vercel origin in `CORS_ORIGINS` on
Render and point the UI straight at the API by adding this before the module
scripts in the page head:

```html
<script>window.METRIQ_API_BASE = 'https://metriq-api.onrender.com';</script>
```

`frontend/js/api.js` accepts either the bare host or a full `/api/v1` URL and
falls back to the relative prefix when the global is absent.

## 6. Verifying the deployment

```bash
# API is up and states the AI-independence contract
curl -s https://metriq-api.onrender.com/health

# Schema is generated (a 500 here means a route annotation is unresolvable)
curl -s -o /dev/null -w '%{http_code}\n' https://metriq-api.onrender.com/openapi.json

# Sign in and read the effective permissions
curl -s -X POST https://metriq-api.onrender.com/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@lab.example","password":"<password>"}'

# Through the Vercel proxy (should return the same health payload)
curl -s https://<your-app>.vercel.app/health
```

Then, in the browser, complete one full evaluation and confirm:

* both mandatory photographs upload and the case becomes submittable;
* finalizing produces a PDF and a DOCX that download successfully;
* the objects appear in the Supabase bucket under `cases/...` and `reports/...`.

## 7. Database initialization, reset and seeding

* **Initialize** (safe, additive): `python backend/scripts/init_db.py`.
* **Reset** (destructive, drops every table): `python backend/scripts/reset_db.py`
  then re-run the seed scripts. Never run it against a production database.
* **Seed** (idempotent): `seed_rules.py`, `seed_identity.py`, `seed_db.py` may be
  re-run; they upsert by natural key.

## 8. Seed data

`seed_rules.py` loads:

* OIML R 76-1:2006 (`r76-1-2006-v1`, active) - 17 rules, MPE bands, tolerances;
* OIML R 76-2:2007 (`r76-2-2007-v1`) - report format sections;
* an inactive placeholder for the R 76 revision (1.1 committee draft);
* the 11-test MVP catalogue plus 6 phase-2 test definitions (inactive);
* the `R76-2-TYPE-EVAL` report template.

`seed_db.py` creates the demo laboratory, one manufacturer, one applicant, one
class III instrument (Max 30000 g, e = d = 10 g), environmental conditions, a
demo case and its two mandatory photographs. All demo accounts use the password
`MetrIQ@2026`:

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

## 9. Backup and recovery

* **Database.** Supabase takes daily backups (point-in-time recovery on paid
  plans). For a manual logical backup:
  `pg_dump "$DATABASE_URL" > metriq-$(date +%F).sql`; restore with `psql`.
* **Storage.** Both evidence and report artefacts are in the `metriq-evidence`
  bucket. `report_revisions.sha256` records the hash of every generated file and
  `attachments.sha256` of every upload, so a restored object can be verified:
  `GET /api/v1/reports/{id}/download` returns the artefact together with
  `X-Content-SHA256`, which must match the stored hash.
* **Recovery order:** restore the database, restore the bucket, then verify a
  sample of reports and attachments against their recorded hashes. Audit logs are
  append-only and are restored with the database.

## 10. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `401` on every request right after login | `AUTH_PROVIDER=supabase` rejects locally issued tokens | Set `AUTH_PROVIDER=hybrid` |
| `500` on `/openapi.json`, `/docs` | A route annotation cannot be resolved at schema-build time | Run `python -c "from app.main import app; app.openapi()"` locally to see the offending type |
| Evidence uploads `500` with "Supabase storage requires ..." | `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` unset | Set both on Render, then redeploy |
| Upload fails with `(400) Bucket not found` | Bucket name mismatch | Create the bucket or fix `STORAGE_BUCKET` |
| Reports download `410` | Artefact missing from the bucket | Confirm `reports/...` objects exist; check the service key |
| `SSL connection has been closed unexpectedly` under load | Direct/IPv6 connection or the wrong pooler port | Use the session pooler (5432) URI |
| Browser calls fail with a CORS error | UI calling Render cross-origin without the origin allowed | Use the Vercel rewrite, or add the exact origin to `CORS_ORIGINS` |
| `/api/*` returns Vercel 404 | Rewrite destination still points at the placeholder host | Update `vercel.json` and redeploy |
