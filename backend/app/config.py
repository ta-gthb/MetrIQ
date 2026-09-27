"""Application configuration.

All secrets are read from environment variables only (PRD 19.1). A development
`.env` file may be used locally via pydantic-settings, but production values are
expected to be injected by the hosting platform (Render).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
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

    # --- Authentication ---------------------------------------------------
    # "supabase" verifies Supabase-issued RS256/HS256 JWTs against JWKS.
    # "local" issues equivalent JWTs for the built-in demo accounts so the
    # platform is fully usable offline (PRD 12.5 failure behaviour).
    AUTH_PROVIDER: Literal["supabase", "local", "hybrid"] = "hybrid"
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_AUDIENCE: str = "authenticated"
    JWT_ISSUER: str | None = None
    ACCESS_TOKEN_TTL_MINUTES: int = 480

    SUPABASE_URL: str | None = None
    SUPABASE_ANON_KEY: str | None = None
    SUPABASE_SERVICE_ROLE_KEY: str | None = None
    SUPABASE_JWKS_URL: str | None = None
    SUPABASE_JWT_SECRET: str | None = None

    # --- Storage ----------------------------------------------------------
    STORAGE_BACKEND: Literal["local", "supabase"] = "local"
    STORAGE_BUCKET: str = "metriq-evidence"
    STORAGE_LOCAL_PATH: str = "./var/storage"
    REPORT_STORAGE_PATH: str = "./var/reports"
    MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024
    ALLOWED_UPLOAD_EXTENSIONS: list[str] = Field(
        default_factory=lambda: [
            ".pdf", ".docx", ".doc", ".xlsx", ".csv", ".txt",
            ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp",
        ]
    )
    SIGNED_URL_TTL_SECONDS: int = 900

    # Photographic evidence that must be present before a case may be submitted
    # for technical review ("two clear images": the instrument nameplate and the
    # test setup). Order is preserved so the UI lists them in a stable sequence.
    REQUIRED_EVIDENCE_CATEGORIES: list[str] = Field(
        default_factory=lambda: ["nameplate_photograph", "test_setup_photograph"]
    )

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
    AI_MIN_CONFIDENCE: float = 0.75

    # --- Reporting --------------------------------------------------------
    REPORT_LABORATORY_NAME: str = "MetrIQ Regional Legal Metrology Laboratory"
    REPORT_LABORATORY_CODE: str = "LAB-001"
    REPORT_DISCLAIMER: str = (
        "This report is generated from structured records under a versioned OIML "
        "R 76 ruleset. It is a technical type-evaluation record and does not by "
        "itself constitute statutory approval."
    )

    # --- CORS -------------------------------------------------------------
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://localhost:8000",
            "http://127.0.0.1:5500",
            "http://localhost:3000",
        ]
    )
    CORS_ALLOW_ORIGIN_REGEX: str | None = r"https://.*\.vercel\.app"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("DEBUG", "AI_ENABLED", mode="before")
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
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
