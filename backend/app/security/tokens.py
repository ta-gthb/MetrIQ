"""JWT creation and verification.

Two identity sources are supported and can run simultaneously ("hybrid"):

* ``supabase`` - tokens issued by Supabase Auth, verified with the project JWT
  secret (HS256) or the project JWKS endpoint (RS256/ES256).
* ``local`` - tokens issued by this backend for the built-in demo accounts.

Both paths produce a verified principal that is mapped onto an application
``User`` record so that role and laboratory scope are always enforced by the
backend (PRD 16.3).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import PyJWKClient

from app.config import settings


class TokenError(Exception):
    """Raised when a token cannot be trusted."""


@dataclass(slots=True)
class Principal:
    subject: str
    email: str | None = None
    #: The *provider's* role claim ("authenticated" for a Supabase user), never
    #: an application role. Authorisation reads ``User.role_code`` from the
    #: database, so nothing in a token can elevate a session.
    role: str | None = None
    audience: str | None = None
    issuer: str | None = None
    claims: dict[str, Any] = field(default_factory=dict)
    source: str = "local"
    # "access" or "refresh". Supabase tokens carry no such claim, so anything
    # without an explicit type is treated as an access token, which is what a
    # Supabase-issued JWT for an API call is.
    token_type: str = "access"


#: Signature algorithms a Supabase-issued token may carry. `none` is absent on
#: purpose: taking the algorithm from an unverified header without an allow-list
#: is how a token that was never signed gets accepted.
SUPABASE_ALGORITHMS = ("RS256", "ES256", "HS256")

#: Supabase roles that are keys, not people. The publishable anon key *is* a
#: valid HS256 JWT for this project carrying ``role: anon``, so a service-role
#: or anon token must never be treated as a signed-in user.
SUPABASE_NON_USER_ROLES = frozenset({"anon", "service_role"})

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        url = settings.SUPABASE_JWKS_URL
        if not url and settings.SUPABASE_URL:
            url = settings.SUPABASE_URL.rstrip("/") + "/auth/v1/.well-known/jwks.json"
        if not url:
            raise TokenError("Supabase JWKS URL is not configured")
        _jwks_client = PyJWKClient(url, cache_keys=True, lifespan=300)
    return _jwks_client


def create_access_token(
    *,
    subject: str,
    email: str | None,
    role: str | None,
    extra: dict[str, Any] | None = None,
    ttl_minutes: int | None = None,
) -> str:
    now = int(time.time())
    ttl = (ttl_minutes or settings.ACCESS_TOKEN_TTL_MINUTES) * 60
    payload: dict[str, Any] = {
        "sub": subject,
        "email": email,
        "role": role,
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": str(uuid.uuid4()),
        "iss": settings.JWT_ISSUER or settings.APP_NAME,
        "app_metadata": {"provider": "local", "roles": [role] if role else []},
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(
    subject: str,
    *,
    email: str | None = None,
    jti: str | None = None,
    ttl_days: int | None = None,
) -> str:
    """Mint a refresh token.

    ``jti`` is supplied by the caller so the row that records this token can be
    written with the same identifier: rotation and reuse detection both look the
    token up by it (audit item 13).
    """
    now = int(time.time())
    lifetime = (ttl_days or settings.REFRESH_TOKEN_TTL_DAYS) * 24 * 3600
    return jwt.encode(
        {
            "sub": subject,
            "email": email,
            "type": "refresh",
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER or settings.APP_NAME,
            "iat": now,
            "nbf": now,
            "exp": now + lifetime,
            "jti": jti or str(uuid.uuid4()),
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


def refresh_expiry() -> datetime:
    """When a refresh token minted now stops being accepted."""
    return datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)


def decode_token(token: str, *, expected_type: str | None = None) -> Principal:
    """Verify a token and return the principal, trying the configured providers.

    When ``expected_type`` is given the token's ``type`` claim must match, so an
    access token cannot be replayed against the refresh endpoint (and vice
    versa).
    """
    errors: list[str] = []
    candidates: list[str] = []
    if settings.AUTH_PROVIDER in {"supabase", "hybrid"}:
        candidates.append("supabase")
    # MetrIQ always mints its own session token - including for a Supabase
    # sign-in, which /auth/session exchanges - so this format is always accepted.
    # AUTH_PROVIDER therefore chooses which *identity providers* are trusted; it
    # cannot turn our own sessions off, which is what made AUTH_PROVIDER=supabase
    # reject every access token this API issues. Local *passwords* are governed
    # separately, by settings.local_login_allowed.
    candidates.append("local")

    for source in candidates:
        try:
            principal = _decode(token, source)
        except TokenError as exc:
            errors.append(f"{source}: {exc}")
            continue
        if expected_type and principal.token_type != expected_type:
            errors.append(f"{source}: expected a {expected_type} token")
            continue
        return principal
    raise TokenError("; ".join(errors) or "Unable to verify token")


def _decode(token: str, source: str) -> Principal:
    options = {"verify_aud": bool(settings.JWT_AUDIENCE), "verify_exp": True}
    if source == "local":
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=[settings.JWT_ALGORITHM],
                audience=settings.JWT_AUDIENCE,
                issuer=settings.JWT_ISSUER or settings.APP_NAME,
                options=options,
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise TokenError(str(exc)) from exc
    else:
        payload = _decode_supabase(token)
    return _principal_from_payload(payload, source)


def _decode_supabase(token: str) -> dict[str, Any]:
    """Verify a Supabase-issued access token (audit item 4).

    Four things are checked beyond the signature, because a valid signature only
    proves the token came from somewhere in this project:

    * the algorithm is on an allow-list, so ``alg: none`` and any algorithm the
      project does not use are refused;
    * ``iss`` names this project, so a token minted for a different project that
      happens to share a signing key is not accepted;
    * ``aud`` is the configured audience;
    * the ``role`` is a user session rather than the anon or service-role key.

    The JWKS route is used for asymmetric keys; HS256 uses the project's shared
    secret and never falls back to ``JWT_SECRET``, which signs *our* tokens - a
    fallback would let a MetrIQ session be presented as a Supabase identity.
    """
    # The header read is inside the try: a malformed token must surface as a 401,
    # not escape as an unhandled decode error.
    try:
        header = jwt.get_unverified_header(token)
        algorithm = str(header.get("alg") or "")
        if algorithm not in SUPABASE_ALGORITHMS:
            raise TokenError(
                "unsupported signing algorithm %r; a Supabase token must use one of %s"
                % (algorithm or "none", ", ".join(SUPABASE_ALGORITHMS))
            )
        kwargs: dict[str, Any] = {"algorithms": [algorithm]}
        if settings.JWT_AUDIENCE:
            kwargs["audience"] = settings.JWT_AUDIENCE
        issuer = settings.supabase_issuer
        if issuer:
            kwargs["issuer"] = issuer
        if algorithm.startswith("HS"):
            if not settings.SUPABASE_JWT_SECRET:
                raise TokenError(
                    "SUPABASE_JWT_SECRET is not configured, so an HS256 Supabase token"
                    " cannot be verified. Set it to the project's JWT secret, or use the"
                    " project's asymmetric signing keys so the JWKS endpoint can be used."
                )
            payload = jwt.decode(token, settings.SUPABASE_JWT_SECRET, **kwargs)
        else:
            signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
            payload = jwt.decode(token, signing_key.key, **kwargs)
    except TokenError:
        raise
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    # Named separately from the generic InvalidTokenError below: both are a
    # one-line configuration fix, and the generic message does not say which way
    # the mismatch went. These must stay ahead of it to be reached.
    except jwt.InvalidIssuerError as exc:
        raise TokenError(
            f"issuer mismatch: this deployment expects {settings.supabase_issuer!r}."
            " Set SUPABASE_JWT_ISSUER if the project's issuer differs."
        ) from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenError(
            f"audience mismatch: this deployment expects {settings.JWT_AUDIENCE!r}"
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(str(exc)) from exc
    except Exception as exc:  # network / JWKS failures must not 500 the request
        raise TokenError(f"supabase verification unavailable: {exc}") from exc

    role = str(payload.get("role") or "")
    if role in SUPABASE_NON_USER_ROLES:
        raise TokenError(
            f"a '{role}' key is not a user session; sign in to obtain a user token"
        )
    return payload


def _principal_from_payload(payload: dict[str, Any], source: str) -> Principal:
    subject = payload.get("sub")
    if not subject:
        raise TokenError("token is missing 'sub'")
    metadata = payload.get("app_metadata") or {}
    roles = metadata.get("roles") or payload.get("roles") or []
    role = payload.get("role") or (roles[0] if roles else None)
    return Principal(
        subject=str(subject),
        email=payload.get("email"),
        role=role,
        audience=payload.get("aud"),
        issuer=payload.get("iss"),
        claims=payload,
        source=source,
        token_type=str(payload.get("type") or "access"),
    )
