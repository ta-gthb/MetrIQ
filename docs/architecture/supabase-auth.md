# Signing in: Supabase Auth as the identity provider

Audit item 4: *"Use Supabase Auth for sign-in, sign-out and password reset in
production... Resolve application user/role from the authenticated identity, not
from browser-supplied role fields. Retain local login only for development/demo
mode."*

Supabase authenticates. MetrIQ authorises. The two are kept apart on purpose: the
provider proves who someone is, and every decision about what they may do is
made from MetrIQ's own `users` row.

## The two flows

| | Production | Development / demonstration |
| --- | --- | --- |
| Who verifies the password | Supabase Auth | this backend (`users.password_hash`, bcrypt) |
| Endpoint | `POST /auth/v1/token?grant_type=password` on the project, then `POST /api/v1/auth/session` | `POST /api/v1/auth/login` |
| Session | MetrIQ access token + HttpOnly refresh cookie | identical |
| Enabled when | Supabase is configured | `ENVIRONMENT != production`, or `DEMO_MODE=true` |

The local path is not hidden in production, it is **refused**: `/auth/login`
answers `403` unless `local_login_allowed`, which is
`ENVIRONMENT != "production" or DEMO_MODE`. Hiding the form would not help, since
a caller can always post to the endpoint directly.

## The exchange, and why there is one

```
Browser --(email + password, apikey header)--> Supabase Auth
        <-- Supabase access token (ES256, verified against the project JWKS)
Browser --(POST /api/v1/auth/session, Bearer <supabase token>)--> MetrIQ
        <-- MetrIQ access token + refresh token in an HttpOnly cookie
```

`/auth/session` verifies the Supabase token, maps it onto a MetrIQ user, and then
issues MetrIQ's own session. Passing the Supabase token straight through to every
API call would have been simpler, and worse:

* the provider's token lifetime would become the session lifetime, so a
  short-lived access token and a revocable refresh token would both be lost;
* the provider's refresh token would have to live in page storage, undoing the
  cookie work in item 13;
* revocation would be the provider's job alone, and `POST /auth/logout` could no
  longer end a session.

The Supabase refresh token is received and **discarded** - nothing stores it, so
there is nothing to replay. When MetrIQ's access token expires, the frontend
refreshes against `/auth/refresh` exactly as it always has.

## What is checked before a token is trusted

A valid signature only proves that a token came from somewhere in this project,
so five things are checked:

* **algorithm** - the token's own header is on an allow-list (`RS256`, `ES256`,
  `HS256`). Without that, a token whose header says `alg: none` is simply
  trusted. This is not hypothetical: taking the algorithm from an unverified
  header is the classic JWT forgery.
* **signature** - asymmetric keys are fetched from the project's JWKS endpoint;
  HS256 uses `SUPABASE_JWT_SECRET` and **never** falls back to `JWT_SECRET`,
  which signs MetrIQ's own tokens. A fallback would let a MetrIQ session be
  presented as a Supabase identity.
* **issuer** - `iss` must name this project. A token minted for a different
  project that shares a signing key is refused. A mismatch says which value was
  expected, because fixing it is one variable (`SUPABASE_JWT_ISSUER`).
* **audience** - `aud` must be the configured `JWT_AUDIENCE`.
* **role** - `anon` and `service_role` are keys, not people, and are refused even
  though their signatures are valid. The publishable anon key is a real HS256
  JWT for the project and is handed to every browser; the service-role key
  bypasses row-level security.

## Roles come from the database, never from the token

Authorisation reads `users.role_code`. Nothing in a token is trusted for it, so
`app_metadata.roles`, `user_metadata.role_code` or a bare `role` claim cannot
elevate a session. `tests/test_supabase_auth.py` signs a token that claims
`SUPER_ADMIN` in every one of those places for an engineer's account, exchanges
it successfully, and asserts the resulting session is still an engineer with an
engineer's permission list.

## Linking a Supabase identity to a MetrIQ account

`resolve_user_for_principal` looks up, in order:

1. `users.supabase_user_id` - the identity itself;
2. the MetrIQ user id, when the token's subject is one of ours;
3. `users.email`.

A first-time sign-in that matched on email records the `supabase_user_id` and
`auth_provider`, so later requests resolve by identity. **The email addresses must
match**, and that is the whole provisioning story: create the account in Supabase
Authentication, create the matching MetrIQ user (`manage_admin.py`, or the user
administration screens), and the first sign-in joins them. A Supabase identity
with no MetrIQ user gets `401 No MetrIQ account is linked to this identity` - open
sign-ups in the project do not, by themselves, grant access to anything.

## Password reset

The reset is the provider's, end to end. The sign-in page posts the address to
`/auth/v1/recover`; Supabase emails a link; the link lands on
`frontend/reset-password.html` with a recovery token in the URL fragment, which
that page sends to Supabase (`PUT /auth/v1/user`) and nowhere else. MetrIQ never
sees the password, and the token is stripped from the address bar as soon as it
is read.

Two things must be configured in the project for that link to arrive anywhere
useful, both under Authentication -> URL Configuration:

* **Site URL** - the deployed frontend origin, e.g. `https://metriq.vercel.app`;
* **Redirect URLs** - add `https://metriq.vercel.app/reset-password.html`.

Supabase only redirects to an allow-listed URL, which is also what stops the
recovery link being used as an open redirect.

## Configuration

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` | Project URL; the JWKS and issuer are derived from it |
| `SUPABASE_ANON_KEY` | Publishable key, served to browsers by `GET /auth/config` |
| `AUTH_PROVIDER` | `supabase` (provider only) or `hybrid` (also accepts MetrIQ tokens) |
| `SUPABASE_JWT_SECRET` | Only for a project still signing with the legacy HS256 secret |
| `SUPABASE_JWT_ISSUER` | Only when `iss` is not `<project URL>/auth/v1` |
| `SUPABASE_JWKS_URL` | Only when the key set is not at the default path |

The service-role key is never part of `/auth/config`, and a test asserts the
whole payload does not contain it.

### Content Security Policy

Sign-in calls the project from the browser, so its origin has to be in
`connect-src`. The API derives that from `SUPABASE_URL` automatically. Vercel
serves the static frontend and its policy is declared in `vercel.json`, so that
file lists the origin explicitly - changing project means changing both.

## What this deliberately does not do

* **No sign-up screen.** Accounts are provisioned (Supabase dashboard, the
  admin API, or `manage_admin.py` for the MetrIQ side). `disable_signup` is
  still `false` in the project, so anyone who finds the anon key can create a
  Supabase identity - they simply cannot use it, because no MetrIQ user is
  linked. Turning sign-ups off in the project is still worth doing.
* **No provider-side session revocation on sign-out.** MetrIQ revokes its own
  session and clears its cookie; the provider's refresh token was discarded at
  sign-in, so there is no provider session left to replay. A Supabase access
  token captured in flight stays valid until it expires, which is why the
  exchange is what every later call uses.
* **No SSO or MFA.** The project has the `email` provider enabled only.
* **No email confirmation handling.** Confirmation is required in the project
  (`mailer_autoconfirm=false`), so a new account cannot sign in until the address
  is confirmed; the sign-in page translates that error into a sentence saying so.

## Testing

`backend/tests/test_supabase_auth.py` covers the forgery cases (wrong key,
MetrIQ's own key, `alg: none`, expired, wrong issuer, wrong audience, anon key,
service-role key), the elevation case above, the refusal of the local password in
production, the configuration endpoint, and the derived CSP origin.

What it cannot cover is the asymmetric path, which needs a live project serving a
real key set: the algorithm allow-list and issuer check that guard it are
exercised through the HS256 path instead. Point `SUPABASE_URL` and
`SUPABASE_ANON_KEY` at a real project and sign in once to exercise JWKS.