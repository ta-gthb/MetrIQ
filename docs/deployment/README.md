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
python scripts/manage_admin.py create --email admin@metriq.local \
       --name "Platform Administrator" --password "MetrIQ@2026"
```

Only the first two commands are strictly required on a fresh database: the
application runs both automatically at start-up (see the `AUTO_*` flags in
section 2), and `seed_db.py` adds demonstration data.

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
| `scripts/seed_identity.py` | Seed the 35 permissions, 5 roles and 99 grants |
| `scripts/seed_db.py` | Demo laboratory, manufacturer, applicant, instrument, environmental conditions, demo case |
| `scripts/manage_admin.py` | Create and manage **only** Super Admin credentials: `create`, `list`, `set-password`, `set-email`, `enable`, `disable`, `delete` |
| `scripts/create_admin.py` | Shortcut for `manage_admin.py create` |
| `scripts/reinit_db.py` | Drop, recreate and reseed any database reachable from this machine (destructive; needs `--yes`) |

All scripts run from any working directory and read `DATABASE_URL` from the
environment or the nearest `.env`.

`manage_admin.py` and `reinit_db.py` also run with **no arguments at all**, which
is the easiest way to use them from another machine: they ask what you want to do
and prompt for every value they need. Passwords are typed without being echoed,
and `reinit_db.py` makes you type `REINITIALISE` before it drops anything.

```bash
python backend/scripts/manage_admin.py
python backend/scripts/reinit_db.py
```

A value passed as a flag is never asked for again, and when stdin is not a
terminal (a pipe, a CI job) the scripts never prompt - they behave exactly as
they did before, so both styles can be mixed.

### 1.1 Tests

```bash
cd backend
python -m pytest -q
```

The suite (538 tests) covers the calculation engine boundaries, the API
contract, authentication/authorization, the review workflow, artefact storage
and the AI fallback behaviour. It runs against a throwaway SQLite database; no
external services are needed.

Two optional layers are run on demand:

```bash
python -m playwright install chromium    # once
python -m pytest e2e -q                  # browser journey (real Chromium)
python scripts/smoke_deploy.py --api https://metriq-api-vgao.onrender.com \
    --frontend https://metriq.vercel.app
```

`e2e/` boots the application with uvicorn, seeds the demo dataset and checks
the home page, theme switching, sign-in and the evaluation workspace. The smoke
script is read-only and checks the deployed API, the published routes, the
public statistics, the frontend pages and the `/api` rewrite.

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
| `DB_SCHEMA` | `metriq` (see below) | PostgreSQL schema for MetrIQ's tables; set it when the database is shared with another application |

#### Sharing a database with another application

If the database already holds another application's tables, MetrIQ must not use
`public`: names such as `users` and `audit_logs` collide, and `create_all` never
alters a table that already exists - so the failure surfaces later as a raw
`column users.role_code does not exist`. Set `DB_SCHEMA` and MetrIQ keeps its
tables in their own schema, which is created automatically at start-up:

```
DB_SCHEMA=metriq
```

The other application's `public` tables are left completely untouched. Provision
the schema and create the administrator from your machine:

```bash
python backend/scripts/manage_admin.py create --schema metriq --email you@lab.example
```

Leave `DB_SCHEMA` empty for the default `public` schema. A Supabase project
dedicated to MetrIQ avoids the situation entirely and is the simpler choice when
you have one to spare.

#### Where a value comes from

Configuration is resolved in this order, and the last one that sets a value wins:

1. the defaults declared in `backend/app/config.py`
2. `backend/deployment.env` - **committed**, non-secret, and the reason a deploy
   picks up `DB_SCHEMA` without anyone opening a dashboard. It carries
   `DB_SCHEMA=metriq`, `AUTO_INIT_DB` and `AUTO_SEED_REFERENCE`
3. `.env` (gitignored) - your local overrides
4. real environment variables, so anything set on Render overrides every file

Nothing secret may go in `deployment.env`; a test asserts that. Change the
deployed schema by editing that file and pushing, or by setting `DB_SCHEMA` in
the Render dashboard, which takes precedence over it.

The file sits beside the application code rather than at the repository root
because `Dockerfile` copies `backend/` into the image. A copy at the root would
never reach the container, which is exactly how the service ended up running
with the default `public` schema. `/health` reports `schema_source` so this is
visible from a single request.

### Bootstrap

| Variable | Default | Notes |
| --- | --- | --- |
| `AUTO_INIT_DB` | `true` | Create missing tables at start-up (idempotent) |
| `AUTO_SEED_REFERENCE` | `true` | Seed roles/permissions/rules/catalogue at start-up when absent (idempotent) |

A deployed instance provisions itself because there is no release step to hook
into on Render's free plan. Set either flag to `false` to keep the process
read-only at boot and run the scripts in section 7 by hand.

### Authentication

Production signs in through Supabase Auth - see 3.4. MetrIQ's own password check
is used in development and in the demonstration deployment only.

| Variable | Default | Notes |
| --- | --- | --- |
| `AUTH_PROVIDER` | `hybrid` | `supabase`, `local` or `hybrid`: which identity providers may sign in |
| `JWT_SECRET` | `change-me-in-production` | **Must** be replaced in production. Signs MetrIQ's session tokens; never used to verify Supabase tokens |
| `JWT_ALGORITHM` | `HS256` | MetrIQ's own tokens only |
| `ACCESS_TOKEN_TTL_MINUTES` | `60` | Short on purpose: the frontend transparently refreshes a 401 once (item 13) |
| `REFRESH_TOKEN_TTL_DAYS` | `14` | Rotated on every use, in an HttpOnly cookie |
| `SUPABASE_URL` | - | Project URL. The JWKS endpoint and the expected token issuer are derived from it |
| `SUPABASE_ANON_KEY` | - | Publishable key, served to browsers by `GET /api/v1/auth/config` |
| `SUPABASE_SERVICE_ROLE_KEY` | - | Server-side key. It bypasses row-level security, so it must never reach a browser |
| `SUPABASE_JWKS_URL` | - | Only when the key set is not at the default path |
| `SUPABASE_JWT_SECRET` | - | Only for a project still signing with the legacy HS256 shared secret |
| `SUPABASE_JWT_ISSUER` | - | Only when `iss` is not `<project URL>/auth/v1` |
| `DEMO_MODE` | `false` | Re-enables the local password path and the demonstration panel |

> **`AUTH_PROVIDER` selects who may sign in, never whether your own session
> works.** MetrIQ mints its own access token in every case - after a Supabase
> sign-in it exchanges the provider's token at `/auth/session` for one - so that
> token format is always accepted. `hybrid` also accepts Supabase tokens at the
> exchange; `supabase` accepts only those; `local` refuses Supabase sign-in
> outright. Local *passwords* are governed by `DEMO_MODE`, not by this variable.

### Storage

| Variable | Default | Notes |
| --- | --- | --- |
| `STORAGE_BACKEND` | `local` | `local` or `supabase` |
| `STORAGE_BUCKET` | `metriq-evidence` | Private bucket, used for evidence **and** reports |
| `STORAGE_LOCAL_PATH` | `./var/storage` | `local` backend only |
| `REPORT_STORAGE_PATH` | `./var/reports` | Legacy: only read when a pre-existing row holds an absolute path |
| `MAX_UPLOAD_BYTES` | `26214400` (25 MB) | |
| `SIGNED_URL_TTL_SECONDS` | `900` | Signed download lifetime |
| `REQUIRED_EVIDENCE_CATEGORIES` | `nameplate_photograph` | Photographs that must be attached before submission |

### CORS

| Variable | Default | Notes |
| --- | --- | --- |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:8000,http://localhost:3000` | Comma-separated **exact** origins, no trailing slash. In `ENVIRONMENT=production` the localhost defaults are dropped, so this is empty unless you add your UI's origin |
| `CORS_ALLOW_ORIGIN_REGEX` | unset | Leave it unset. A pattern containing `*` is ignored, and reported at `/health` |

CORS is only involved when the browser calls the API **cross-origin**. The
Vercel rewrite described in section 5 makes the calls same-origin, so no CORS
entry is needed for the deployed UI - including a Vercel preview deployment,
which is served through the same rewrite.

A wildcard is never honoured, because the middleware sends credentials. Setting
`CORS_ALLOW_ORIGIN_REGEX=https://.*\.vercel\.app` - which earlier revisions of
this guide suggested - is ignored rather than fatal: the service still boots, and
`/health` lists the variable under `ignored_settings`:

```json
"ignored_settings": [
  "CORS_ALLOW_ORIGIN_REGEX: contains a wildcard, so it is not used; list the exact origins in CORS_ORIGINS instead"
]
```

Delete that variable from the host's environment (and from any Environment
Group) to clear the entry - nothing else depends on it. To allow a UI that
genuinely calls the API cross-origin, list its exact origin in `CORS_ORIGINS`.

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

### 3.4 Sign-in with Supabase Auth

Supabase Auth is the production identity provider (audit item 4). The design and
what gets verified are in `docs/architecture/supabase-auth.md`; this is the
setup.

1. **Project Settings -> API**: copy the project URL and the `anon` publishable
   key, and set `SUPABASE_URL` and `SUPABASE_ANON_KEY` on Render.
2. **Token verification usually needs nothing else.** A project on today's
   asymmetric signing keys is verified through its JWKS endpoint, which is
   derived from `SUPABASE_URL`. A project still signing with the legacy shared
   secret additionally needs `SUPABASE_JWT_SECRET` (Project Settings -> API ->
   JWT Settings). If it is missing, the 401 says so.
3. **Authentication -> URL Configuration**: set **Site URL** to the deployed
   frontend origin, and add `<origin>/reset-password.html` to **Redirect URLs**.
   Supabase only redirects to an allow-listed URL, so without this the
   password-reset email has nowhere to go.
4. **Create the accounts**: Authentication -> Users -> Add user, using **the same
   email address** as the MetrIQ user. This is only about the address the provider
   verifies: the MetrIQ user ID (`stmadm...`, `labadm...`, `temadm...`, `trvadm...`,
   `apradm...`) is issued by the platform when the account is created
   and needs no counterpart here. The first sign-in links the two and records
   the Supabase identity. A Supabase user with no MetrIQ account is refused with
   `No MetrIQ account is linked to this identity`, so open sign-ups expose no
   data - but turning them off (Providers -> Email) is still worth doing.
5. **Confirm the email.** A new project requires confirmation
   (`mailer_autoconfirm=false`), so an account cannot sign in until the address is
   confirmed; the sign-in page turns that error into a sentence saying so.
6. **Email delivery.** Password-reset mail uses Supabase's built-in SMTP, which
   is rate limited to a handful of messages an hour and is meant for testing.
   Configure a custom SMTP under Project Settings -> Auth before relying on
   resets.
7. **CSP.** The browser calls the project directly, so the origin has to be in
   `connect-src`: the API derives it from `SUPABASE_URL`, and `vercel.json`
   lists it explicitly because Vercel serves the static frontend. Changing
   project means changing both.

A demonstration deployment keeps `DEMO_MODE=true` so the seeded `@metriq.local`
accounts still work. Without it, `/auth/login` answers 403 by design and those
accounts can only sign in if they also exist in Supabase.

## 4. Render: the API

1. **New -> Blueprint**, point it at the repository. Render reads `render.yaml`
   and creates the `metriq-api` web service (`plan: free`).
2. Render prompts for the `sync: false` variables. Supply:

   | Key | Value |
   | --- | --- |
   | `DATABASE_URL` | the Supabase session pooler URI from 3.2 |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `SUPABASE_ANON_KEY` | from Project Settings -> API |
   | `SUPABASE_SERVICE_ROLE_KEY` | from Project Settings -> API |
   | `SUPABASE_JWT_SECRET` | only for a project still signing with the legacy HS256 secret; a project on asymmetric signing keys needs nothing here (see 3.4) |
   | `SUPABASE_JWT_ISSUER` | only when `iss` is not `<SUPABASE_URL>/auth/v1` |
   | `SUPABASE_JWKS_URL` | optional - derived from `SUPABASE_URL` when blank |
   | `CORS_ORIGINS` | your Vercel URL, e.g. `https://metriq.vercel.app` (optional) |
   | `PUBLIC_APP_URL` | your Vercel URL, e.g. `https://metriq.vercel.app`; printed reports then carry a QR code that opens the verification page (optional) |
   | `AI_API_KEY` | blank for the `stub` provider |

   `JWT_SECRET` is generated by Render. `AUTH_PROVIDER=hybrid`,
   `STORAGE_BACKEND=supabase`, `STORAGE_BUCKET=metriq-evidence` and the two
   `AUTO_*` bootstrap flags are already set in the blueprint.
3. Deploy. There is no pre-deploy step to configure: **the service provisions
   itself as it boots**. The first start creates the 36 tables and seeds the
   roles, permissions, rules, report template and test catalogue; later starts
   notice the data is present and skip that work. Confirm in the log with
   `database ready at ...: 36 tables, reference data seeded`.
4. Note the service URL Render assigns. If it is not
   `https://metriq-api.onrender.com`, update both rewrite destinations in
   `vercel.json` (section 5).
5. Create the first administrator. The free plan has **no shell**, so run the
   operator scripts from your own machine against the same database:

   ```bash
   # macOS / Linux / Git Bash
   DATABASE_URL="postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres" \
     python backend/scripts/manage_admin.py create --email you@lab.example --name "Your Name"
   ```

   ```powershell
   # Windows PowerShell
   $env:DATABASE_URL="postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres"
   python backend/scripts/manage_admin.py create --email you@lab.example --name "Your Name"
   ```

   A strong password is generated and printed once, together with the **user ID**
   the platform issues the account - `stmadm<year><nnn>`, the identifier it signs
   in with. `manage_admin.py list` shows the accounts and their user IDs, and
   `set-password`, `set-email`, `enable`, `disable` and `delete` cover the rest;
   the lookup commands accept either the user ID or the address. Every command
   also accepts `--url "<connection string>"`, so exporting the variable is
   optional.
6. *(Optional)* Demonstration data, from the same machine:

   ```bash
   python backend/scripts/seed_db.py
   ```

   The sign-in page lists six demonstration accounts and fills in the password
   `MetrIQ@2026` when one is picked. They exist **only** after this command has run
   against that database - otherwise every button on the page reports a failed
   sign-in, which looks like a broken deployment but is a missing seed.

   Seeding never overwrites an existing account, so the Super Admin created in step
   5 keeps its own generated password and the `admin@metriq.local` button will keep
   failing until the two are aligned:

   ```bash
   python backend/scripts/manage_admin.py set-password --email admin@metriq.local
   ```

   These credentials are published in the UI. Change them before the instance is
   anything other than a demonstration.

The Render filesystem is ephemeral and no disk is attached: nothing is lost
because PostgreSQL, evidence and report artefacts all live in Supabase.

> **Free-plan notes.** The instance sleeps after roughly 15 minutes of
> inactivity and the next request wakes it, which takes a few seconds. There is
> no shell and no pre-deploy command, so `render.yaml` requests `plan: free` and
> the application provisions itself at start-up. Move to `plan: starter` for an
> always-on instance.

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
# API is up: expect "database":"ok" and "storage_backend":"supabase"
curl -s https://metriq-api-vgao.onrender.com/health

# Schema is generated (a 500 here means a route annotation is unresolvable)
curl -s -o /dev/null -w '%{http_code}\n' https://metriq-api-vgao.onrender.com/openapi.json

# Sign in and read the effective permissions
curl -s -X POST https://metriq-api-vgao.onrender.com/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@lab.example","password":"<password>"}'

# Through the Vercel proxy (should return the same health payload)
curl -s https://<your-app>.vercel.app/health
```

`/health` reports the two conditions that silently break a deployment:

* `"database"` - `"ok"` when the round trip succeeds, otherwise
  `"unavailable: <reason>"`. Anything other than `ok` explains a `503` on
  sign-in.
* `"storage_backend"` - must be `"supabase"` in production. `"local"` means
  uploads and report artefacts are being written to the ephemeral container
  filesystem and will disappear on the next restart.

Then, in the browser, complete one full evaluation and confirm:

* the mandatory nameplate photograph uploads and the case becomes submittable;
* finalizing produces a PDF and a DOCX that the Laboratory Admin / Manager can download;
* the objects appear in the Supabase bucket under `cases/...` and `reports/...`.

## 7. Database initialization, reset and seeding

A deployed instance provisions itself on every start, so a normal deploy needs
no manual step (`AUTO_INIT_DB`, `AUTO_SEED_REFERENCE` in section 2). The scripts
below are for local work and for operating a deployed database from your own
machine: each reads `DATABASE_URL` and also accepts
`--url "<connection string>"` to target a database directly.

* **Provision** (what start-up runs): `app/services/reference_data/bootstrap.py`
  creates missing tables, then seeds roles, permissions, rules, the report
  template and the test catalogue when they are absent. Re-running is harmless.
* **Initialize** (additive, safe): `python backend/scripts/init_db.py`.
* **Reinitialize remotely** (destructive): drops every table, recreates the
  schema, reseeds the catalogue and re-creates a Super Admin in one pass.

  ```bash
  python backend/scripts/reinit_db.py --yes --admin-email you@lab.example
  ```

  Run it with **no arguments** instead and it asks for the connection string, the
  schema and the new Super Admin account, then requires the word `REINITIALISE`
  before touching anything. If the target schema already holds tables that do not
  match MetrIQ's own - the signature of a database shared with another application
  - the guided run stops instead of dropping them.

  A connection string kept in `backend/.env` (gitignored) is picked up
  automatically: the guided run prints the target and asks only `Use this
  database? [Y/n]`, so the URL is typed once and never again. Anything typed at a
  prompt is offered back to that file. The same file makes the other scripts
  target the deployed database with no flags at all:

  ```bash
  # backend/.env - gitignored, so the password never reaches the repository
  DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
  DB_SCHEMA=metriq
  ENVIRONMENT=production
  ```

  `ENVIRONMENT=production` is worth setting alongside a remote `DATABASE_URL`: it
  makes `reset_db.py` refuse outright and makes `reinit_db.py` confirm before it
  drops anything.

  It refuses to run without `--yes`, and refuses when `ENVIRONMENT=production`
  unless `--force` is added. `--no-admin` skips the account and
  `--admin-password` chooses the password. Objects already in the storage bucket
  are left in place; they simply become unreferenced.
* **Reset locally** (destructive, development only):
  `python backend/scripts/reset_db.py --yes`.
* **Seed** (idempotent): `seed_rules.py`, `seed_identity.py` and `seed_db.py`
  may be re-run; they upsert by natural key.
* **Super Admin credentials** - only accounts whose role is `SUPER_ADMIN` are
  touched:

  ```bash
  python backend/scripts/manage_admin.py create       --email you@lab.example
  python backend/scripts/manage_admin.py list
  python backend/scripts/manage_admin.py set-password --email you@lab.example
  python backend/scripts/manage_admin.py set-email    --email you@lab.example --new-email new@lab.example
  python backend/scripts/manage_admin.py disable      --email you@lab.example
  python backend/scripts/manage_admin.py delete       --email you@lab.example --yes
  ```

  Run it with **no arguments** for a menu of the same actions, or give a command
  without its flags (`manage_admin.py set-password`) and it prompts for what is
  missing:

  ```bash
  python backend/scripts/manage_admin.py
  ```

  Passwords must be at least 8 characters; omitting `--password` - or leaving the
  prompt blank - generates one and prints it once. `disable` and `delete` refuse to touch the last active
  Super Admin, so you cannot lock yourself out. `create_admin.py` remains as a
  shortcut for `manage_admin.py create`.

## 8. Seed data

`seed_rules.py` loads:

* OIML R 76-1:2006 (`r76-1-2006-v1`, active) - 17 rules, MPE bands, tolerances;
* OIML R 76-2:2007 (`r76-2-2007-v1`) - report format sections;
* an inactive placeholder for the R 76 revision (1.1 committee draft);
* the 11-test MVP catalogue plus 6 phase-2 test definitions (inactive);
* the `R76-2-TYPE-EVAL` report template.

`seed_db.py` creates the demo laboratory, one manufacturer, one applicant, one
class III instrument (Max 30000 g, e = d = 10 g), environmental conditions, a
demo case and its mandatory nameplate photograph. All demo accounts use the password
`MetrIQ@2026`:

| Email | Role |
| --- | --- |
| `admin@metriq.local` | Super Admin |
| `labadmin@metriq.local` | Laboratory Admin |
| `engineer@metriq.local` | Test Engineer |
| `reviewer@metriq.local` | Technical Reviewer |
| `approver@metriq.local` | Approving Authority |

`seed_identity.py` seeds 35 permission codes, 5 roles and 99 role-permission
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
| Sign-in returns `403` "Password sign-in is not enabled on this deployment" | `ENVIRONMENT=production` without `DEMO_MODE`, so the local password path is refused - by design | Sign in through Supabase, or set `DEMO_MODE=true` if this is a demonstration deployment |
| `/auth/session` returns `401` "The sign-in token could not be verified" | The API refused the token. The reply is deliberately generic, so the reason is only in the service log | Read the `metriq.auth` log line (`Sign-in token rejected: ...`). `SUPABASE_JWT_SECRET is not configured` is the legacy HS256 project with no secret - set it from Project Settings -> API, or move the project to asymmetric signing keys so JWKS is used |
| That log line says `issuer mismatch` | The project's `iss` is not `<SUPABASE_URL>/auth/v1`, usually a custom domain | Set `SUPABASE_JWT_ISSUER` to the value the log line names. `audience mismatch` is the same shape of fix, via `JWT_AUDIENCE` |
| `401` "No MetrIQ account is linked to this identity" | A Supabase user exists with no matching MetrIQ user | Create the MetrIQ user with the same email address (`manage_admin.py`); the next sign-in links them |
| Sign-in fails with "Confirm your email address first" | A new Supabase project requires confirmation | Confirm from the email, or turn confirmation off for a demo project |
| Nothing arrives after "Forgot your password?" | Supabase's built-in SMTP is rate limited, or no custom SMTP is configured | Wait, or configure Project Settings -> Auth -> SMTP |
| Browser calls fail with a CORS error | UI calling Render cross-origin without the origin allowed | Use the Vercel rewrite, or add the exact origin to `CORS_ORIGINS` |
| `/health` lists `ignored_settings` with `CORS_ALLOW_ORIGIN_REGEX` | A wildcard origin pattern (`https://.*\.vercel\.app`) is still set on the host, from an earlier revision of this guide | Harmless: the value is ignored and the service boots. Delete `CORS_ALLOW_ORIGIN_REGEX` from the service's environment variables (and any Environment Group) to clear it. The bundled UI is same-origin through the Vercel rewrite, so production needs no CORS entry at all |
| Deploy crash-loops with `ValidationError ... CORS_ALLOW_ORIGIN_REGEX` | A build older than the current `main` is being served | Redeploy from the latest commit: `/health` reports the served `git_commit`, and current builds ignore the value instead of raising |
| `/api/*` returns Vercel 404 | Rewrite destination still points at the placeholder host | Update `vercel.json` and redeploy |
| Sign-in returns `503` "A database error occurred" | The API cannot reach the database at all | `curl /health`; if `"database"` is not `"ok"`, fix `DATABASE_URL` (session pooler, port 5432, percent-encoded password) and redeploy |
| `/health` shows `"storage_backend":"local"` in production | `STORAGE_BACKEND` was never set on the service | Set `STORAGE_BACKEND=supabase` and `STORAGE_BUCKET=metriq-evidence`, then redeploy - local files do not survive a restart |
| `401` for an account just created with `manage_admin.py` | The script and the service are pointing at different databases | Compare the `target:` line printed by the script with the `DATABASE_URL` set on Render |
| `column users.role_code does not exist` | Another application's `users` table already exists in `public`, and `create_all` never alters an existing table | Set `DB_SCHEMA=metriq` on the service and in your scripts, then re-run `manage_admin.py create --schema metriq` |
| Seeding fails with `value too long for type character varying(N)` | A seeded value is wider than its declared column; SQLite does not enforce widths | Already covered by `tests/test_ops_scripts.py`, which asserts the shipped data fits - run the suite after editing the seed JSON |
