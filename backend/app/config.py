"""Application configuration.

All secrets are read from environment variables only (PRD 19.1). A development
`.env` file may be used locally via pydantic-settings, but production values are
expected to be injected by the hosting platform (Render).

Values are resolved in this order, last one wins:

1. the defaults declared on Settings below
2. `backend/deployment.env` - committed, non-secret, and the reason a deployed
   service needs no dashboard round-trip for values such as DB_SCHEMA
3. `.env` (gitignored, local only)
4. real environment variables, which always win - so anything set on Render
   overrides every file here
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


# Resolved next to the application code rather than the working directory, so it
# is found wherever the app is started from - and, unlike a copy at the
# repository root, it travels with backend/ into the deployment image.
DEPLOYMENT_DEFAULTS = Path(__file__).resolve().parents[1] / "deployment.env"

# A workstation origin. Used to keep the development defaults out of a
# production deployment's allow-list (audit item 12).
_LOCALHOST_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", re.IGNORECASE)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(DEPLOYMENT_DEFAULTS, ".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core -------------------------------------------------------------
    APP_NAME: str = "MetrIQ"
    APP_SUBTITLE: str = "NAWI OIML R 76 Test Report Automation Platform"
    ENVIRONMENT: Literal["development", "staging", "production", "test"] = "development"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = True

    # --- Database ---------------------------------------------------------
    # Postgres in production (Supabase). SQLite keeps the demo and the test
    # suite runnable without external infrastructure.
    DATABASE_URL: str = "sqlite:///./metriq.db"
    SQL_ECHO: bool = False
    # Optional PostgreSQL schema for MetrIQ's own tables. Leave empty for the
    # default (public / SQLite behaviour). Set it when the database is shared
    # with another application: tables are then created in - and resolved from -
    # this schema, so a name collision such as another app's `users` table
    # cannot shadow MetrIQ's.
    DB_SCHEMA: str | None = None

    # --- Authentication ---------------------------------------------------
    # "supabase" verifies Supabase-issued RS256/HS256 JWTs against JWKS.
    # "local" issues equivalent JWTs for the built-in demo accounts so the
    # platform is fully usable offline (PRD 12.5 failure behaviour).
    AUTH_PROVIDER: Literal["supabase", "local", "hybrid"] = "hybrid"
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_AUDIENCE: str = "authenticated"
    JWT_ISSUER: str | None = None
    # Short on purpose (audit item 13): the frontend transparently refreshes a
    # 401 once, so a stolen access token is worth an hour at most rather than a
    # working day. The refresh token is rotated on every use and lives in an
    # HttpOnly cookie, so it is not readable from page JavaScript.
    ACCESS_TOKEN_TTL_MINUTES: int = 60
    REFRESH_TOKEN_TTL_DAYS: int = 14
    REFRESH_COOKIE_NAME: str = "metriq_refresh"
    REFRESH_COOKIE_PATH: str = "/"

    SUPABASE_URL: str | None = None
    SUPABASE_ANON_KEY: str | None = None
    SUPABASE_SERVICE_ROLE_KEY: str | None = None
    SUPABASE_JWKS_URL: str | None = None
    # Only needed for a project still signing with the legacy HS256 shared
    # secret; a project on asymmetric signing keys is verified through JWKS.
    SUPABASE_JWT_SECRET: str | None = None
    # The `iss` a Supabase token must carry, when it is not the project URL plus
    # /auth/v1 - a project on a custom domain, or behind a different external URL.
    SUPABASE_JWT_ISSUER: str | None = None

    # --- Demonstration mode -----------------------------------------------
    # Sign-in for the seeded demonstration accounts. The frontend bundle
    # contains no credential: it asks the API for the account list, and the API
    # answers only while DEMO_MODE is on (audit item 3). Rotate the accounts by
    # setting DEMO_PASSWORD here - seed_db.py hashes this same value, so one
    # variable changes both the seeded password and what the demo panel offers.
    DEMO_MODE: bool = False
    DEMO_PASSWORD: str = "MetrIQ@2026"

    # --- Storage ----------------------------------------------------------
    STORAGE_BACKEND: Literal["local", "supabase"] = "local"
    STORAGE_BUCKET: str = "metriq-evidence"
    STORAGE_LOCAL_PATH: str = "./var/storage"
    REPORT_STORAGE_PATH: str = "./var/reports"
    MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024
    ALLOWED_UPLOAD_EXTENSIONS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            ".pdf", ".docx", ".doc", ".xlsx", ".csv", ".txt",
            ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp",
        ]
    )
    SIGNED_URL_TTL_SECONDS: int = 900

    # Photographic evidence that must be present before a case may be submitted
    # for technical review: the instrument nameplate is mandatory. The order is
    # preserved so the UI lists the categories in a stable sequence.
    REQUIRED_EVIDENCE_CATEGORIES: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["nameplate_photograph"]
    )

    # --- Bootstrap --------------------------------------------------------
    # A deployed instance provisions itself at start-up because Render's free
    # plan offers neither a pre-deploy command nor a shell. Create any missing
    # table, then seed roles/rules/catalogue when they are absent. Both steps
    # are idempotent; seeding is skipped once the data is present.
    AUTO_INIT_DB: bool = True
    AUTO_SEED_REFERENCE: bool = True

    # --- AI ---------------------------------------------------------------
    AI_ENABLED: bool = True
    AI_PROVIDER: Literal["stub", "openai", "azure_openai", "custom"] = "stub"
    AI_VISION_MODEL: str = "gpt-4o-mini"
    AI_TEXT_MODEL: str = "gpt-4o-mini"
    AI_API_KEY: str | None = None
    AI_BASE_URL: str | None = None
    AI_TIMEOUT_SECONDS: float = 30.0
    AI_NAME_PLATE_EXTRACT: bool = True
    AI_ANOMALY_DETECTION: bool = True
    AI_DOCUMENT_CLASSIFICATION: bool = True
    AI_KNOWLEDGE_ASSISTANT: bool = True
    AI_REPORT_CONSISTENCY: bool = True
    AI_MIN_CONFIDENCE: float = 0.75

    # --- Reporting --------------------------------------------------------
    REPORT_LABORATORY_NAME: str = "MetrIQ Regional Legal Metrology Laboratory"
    REPORT_LABORATORY_CODE: str = "LAB-001"
    REPORT_DISCLAIMER: str = (
        "This report is generated from structured records under a versioned OIML "
        "R 76 ruleset. It is a technical type-evaluation record and does not by "
        "itself constitute statutory approval."
    )
    # The address at which a printed report can be checked, for example
    # "https://metriq.vercel.app". Reports then carry a QR code that opens
    # <PUBLIC_APP_URL>/verification.html?code=..., and the verification page
    # answers from GET /api/v1/verify/{code}. Left empty, reports are rendered
    # without a QR code and the verification page can still be reached directly.
    PUBLIC_APP_URL: str | None = None

    # --- CORS -------------------------------------------------------------
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://localhost:8000",
            "http://127.0.0.1:5500",
            "http://localhost:3000",
        ]
    )
    # Exact origins only. A wildcard pattern here would be broader than any real
    # deployment needs, because the middleware also sends credentials (audit
    # item 12). A value that cannot be honoured is ignored rather than fatal:
    # see `cors_allow_origin_regex`. The field stays so the host's variable is
    # still read and can be reported by `ignored_settings` at /health, instead
    # of disappearing silently into the pydantic "extra" bucket.
    CORS_ALLOW_ORIGIN_REGEX: str | None = None

    # --- Security headers -------------------------------------------------
    # Applied to every API response, including the copy of the frontend this
    # service serves. Scripts must come from this origin: every page's inline
    # bootstrap was moved into js/theme-boot.js so no exception is needed.
    # Styles keep 'unsafe-inline' because the pages set style attributes.
    CSP_POLICY: str = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "manifest-src 'self'"
    )
    # Extra origins for connect-src, space separated. Only needed when the UI
    # calls this API cross-origin instead of through the host's rewrite proxy,
    # e.g. "https://metriq-api-vgao.onrender.com".
    CSP_EXTRA_CONNECT_SRC: str = ""
    HSTS_MAX_AGE_SECONDS: int = 31536000

    @property
    def cors_origins(self) -> list[str]:
        """The origins actually allowed, restricted by environment.

        The bundled frontend reaches the API through the host's same-origin
        rewrite - vercel.json proxies /api to this service - so a production
        deployment needs no CORS entry at all. The development defaults are
        dropped there so a localhost origin can never be used against it.
        """
        if self.ENVIRONMENT == "production":
            return [origin for origin in self.CORS_ORIGINS if not _LOCALHOST_ORIGIN.match(origin)]
        return list(self.CORS_ORIGINS)

    @property
    def cors_allow_origin_regex(self) -> str | None:
        """The extra origin pattern this deployment may match, or None.

        Audit item 12: the middleware also sends credentials, so a pattern that
        matches more than a named origin - anything containing ``*`` - is never
        honoured. It is ignored rather than refused, because a validator that
        raises here runs while this module is being imported: the service would
        crash-loop and `/health` could never say why. A host variable left over
        from an earlier revision of the deployment guide is therefore harmless,
        and shows up in `ignored_settings` instead.
        """
        pattern = (self.CORS_ALLOW_ORIGIN_REGEX or "").strip()
        if not pattern or "*" in pattern:
            return None
        return pattern

    @property
    def ignored_settings(self) -> list[str]:
        """Variables that were configured but deliberately not applied.

        Published by ``GET /health``, so a stale value on the host is visible as
        "set, and knowingly ignored" rather than as a silent no-op - or a crash
        loop that hides its own cause.
        """
        ignored: list[str] = []
        pattern = (self.CORS_ALLOW_ORIGIN_REGEX or "").strip()
        if pattern and "*" in pattern:
            ignored.append(
                "CORS_ALLOW_ORIGIN_REGEX: contains a wildcard, so it is not used; "
                "list the exact origins in CORS_ORIGINS instead"
            )
        return ignored

    @field_validator("DB_SCHEMA", mode="before")
    @classmethod
    def _validate_schema(cls, value):
        if value is None:
            return None
        name = str(value).strip()
        if not name:
            return None
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(
                "DB_SCHEMA must be a plain SQL identifier (letters, digits and underscore)"
            )
        return name

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "DEBUG", "AI_ENABLED", "AUTO_INIT_DB", "AUTO_SEED_REFERENCE", "DEMO_MODE", mode="before"
    )
    @classmethod
    def _lenient_bool(cls, value):
        """Interpret common deployment flags without failing startup.

        Hosting platforms often export generic variables such as DEBUG with
        values like "release"; an unparseable flag must not stop the service.
        """
        if isinstance(value, str):
            normalised = value.strip().lower()
            if normalised in {"1", "true", "yes", "on", "y", "t", "debug", "development", "dev"}:
                return True
            if normalised in {"0", "false", "no", "off", "n", "f", "release", "production", "prod"}:
                return False
            return False
        return value

    @field_validator("ALLOWED_UPLOAD_EXTENSIONS", "REQUIRED_EVIDENCE_CATEGORIES", mode="before")
    @classmethod
    def _split_extensions(cls, value):
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value

    @property
    def public_app_url(self) -> str | None:
        """The canonical origin of the deployed frontend, without a trailing slash."""
        if not self.PUBLIC_APP_URL:
            return None
        url = self.PUBLIC_APP_URL.strip().rstrip("/")
        if not url:
            return None
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def supabase_auth_url(self) -> str | None:
        """The project's Supabase Auth (GoTrue) base URL, or None if unset."""
        if not self.SUPABASE_URL:
            return None
        return self.SUPABASE_URL.rstrip("/") + "/auth/v1"

    @property
    def supabase_issuer(self) -> str | None:
        """The ``iss`` a Supabase token for this project has to carry.

        Derived from the project URL, which is what Supabase uses, but
        overridable: a project on a custom domain, or one whose external URL
        differs, would otherwise have every otherwise-valid token refused.
        """
        if self.SUPABASE_JWT_ISSUER:
            return self.SUPABASE_JWT_ISSUER.rstrip("/")
        return self.supabase_auth_url

    @property
    def supabase_login_enabled(self) -> bool:
        """Whether sign-in through Supabase Auth is offered at all.

        Needs the project URL and the publishable anon key. Both are public by
        design - the anon key is what a browser is expected to carry, and
        row-level security plus this API's own checks are what protect the data.
        """
        return bool(
            self.AUTH_PROVIDER in {"supabase", "hybrid"}
            and self.SUPABASE_URL
            and self.SUPABASE_ANON_KEY
        )

    @property
    def local_login_allowed(self) -> bool:
        """May a password be checked against MetrIQ's own password hash?

        Audit item 4: production sign-in is Supabase's job, and local sign-in
        survives only for development and the demonstration deployment. This is
        enforced in the endpoint, not by hiding the form - a caller can always
        post to /auth/login directly.
        """
        return self.ENVIRONMENT != "production" or self.DEMO_MODE


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
