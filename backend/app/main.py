"""MetrIQ FastAPI application entry point."""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import __version__
from app.config import settings
from app.routers import (
    admin,
    ai,
    attachments,
    audit,
    auth,
    cases,
    dashboard,
    masters,
    platform,
    reports,
    standards,
    tests,
    workflow,
)
from app.services.attachment_service.storage import get_storage
from app.services.reference_data.bootstrap import (
    database_status,
    health_summary,
    initialise_database,
)

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("metriq")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Self-provisioning start-up: create the schema and seed the reference
    # catalogue when they are missing (AUTO_INIT_DB / AUTO_SEED_REFERENCE).
    initialise_database()
    try:
        storage = get_storage()
        logger.info("storage backend ready: %s", storage.name)
    except Exception as exc:  # a broken bucket must not stop the API from booting
        logger.error(
            "STORAGE UNAVAILABLE (%s): %s. Uploads and report downloads will fail.",
            settings.STORAGE_BACKEND, exc,
        )
    logger.info(
        "MetrIQ %s starting (env=%s, auth=%s, storage=%s, ai=%s)",
        __version__, settings.ENVIRONMENT, settings.AUTH_PROVIDER,
        settings.STORAGE_BACKEND, settings.AI_PROVIDER,
    )
    yield
    logger.info("MetrIQ shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "NAWI OIML R 76 Test Report Automation Platform. Deterministic metrology engine "
        "with a bounded AI assistance layer."
    ),
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # `cors_origins`, not the raw list: the localhost defaults are dropped in
    # production, and no wildcard pattern is accepted at all (audit item 12).
    # The regex property yields None for a wildcard, so a stale host variable
    # is ignored instead of widening the allow-list.
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Content-SHA256"],
)


DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
# The interactive documentation loads its bundles from a CDN, so it gets its own
# policy rather than an 'unsafe-inline' hole in the application's (item 13).
DOCS_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self'"
)


def _application_csp() -> str:
    """The policy for the application, with every origin it must reach.

    Supabase Auth is called directly by the browser to exchange credentials for
    an access token, so its origin is derived from the configuration rather than
    asked for separately - otherwise enabling Supabase sign-in would silently
    fail against a policy that still said ``connect-src 'self'`` (audit item 4).
    """
    extra = list(settings.CSP_EXTRA_CONNECT_SRC.split())
    supabase = settings.SUPABASE_URL
    if settings.supabase_login_enabled and supabase:
        origin = supabase.rstrip("/")
        if origin not in extra:
            extra.append(origin)
    if not extra:
        return settings.CSP_POLICY
    return settings.CSP_POLICY.replace(
        "connect-src 'self'", "connect-src 'self' " + " ".join(extra)
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Security headers on everything this service answers (audit item 13).

    Vercel serves the static frontend, so the same headers are declared again in
    vercel.json; this covers the API and the copy of the UI the backend mounts.
    """
    response = await call_next(request)
    headers = response.headers
    path = request.url.path
    headers.setdefault(
        "Content-Security-Policy", DOCS_CSP if path.startswith(DOCS_PATHS) else _application_csp()
    )
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if settings.ENVIRONMENT == "production":
        headers.setdefault(
            "Strict-Transport-Security",
            f"max-age={settings.HSTS_MAX_AGE_SECONDS}; includeSubDomains",
        )
    return response


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled error [request_id=%s] %s %s", request_id, request.method, request.url.path)
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{duration_ms:.1f}"
    if not request.url.path.startswith(("/docs", "/openapi", "/redoc")):
        logger.info(
            "%s %s -> %s %.1fms [request_id=%s]",
            request.method, request.url.path, response.status_code, duration_ms, request_id,
        )
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "The request payload failed validation.",
            "errors": exc.errors(),
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(IntegrityError)
async def integrity_exception_handler(request: Request, exc: IntegrityError):
    logger.warning("Integrity error [request_id=%s]: %s", getattr(request.state, "request_id", None), exc)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": "The operation conflicts with existing data (for example a duplicate identifier).",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(SQLAlchemyError)
async def database_exception_handler(request: Request, exc: SQLAlchemyError):
    logger.exception("Database error [request_id=%s]", getattr(request.state, "request_id", None))
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "A database error occurred. Please retry; if it persists contact an administrator.",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.get("/health", tags=["Platform"], summary="Liveness and configuration summary")
def health() -> dict:
    storage = "unknown"
    try:
        storage = get_storage().name
    except Exception as exc:  # pragma: no cover - configuration dependent
        storage = f"unavailable: {exc}"
    return {
        "status": "ok",
        "name": settings.APP_NAME,
        "version": __version__,
        "environment": settings.ENVIRONMENT,
        # The platform clock, so the browser can correct a workstation whose
        # system time is wrong instead of misdating what it displays.
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
        "database": database_status(),
        # Which schema the tables were resolved from, plus a count of any that
        # do not match this application - the fastest way to confirm that
        # DB_SCHEMA reached the service.
        **health_summary(),
        # Which commit Render is actually serving. Without it, "the deploy did
        # not take effect" and "the setting did not take effect" look identical.
        "git_commit": (os.environ.get("RENDER_GIT_COMMIT") or "unknown")[:7],
        "auth_provider": settings.AUTH_PROVIDER,
        # Variables the host has set that this build deliberately does not
        # apply, with the reason. A non-empty list is not an error: it is how a
        # leftover `CORS_ALLOW_ORIGIN_REGEX` from the older deployment guide
        # becomes visible instead of silently changing behaviour.
        "ignored_settings": settings.ignored_settings,
        "storage_backend": storage,
        "ai_provider": settings.AI_PROVIDER,
        "ai_enabled": settings.AI_ENABLED,
        "calculation_engine_independent_of_ai": True,
    }


@app.get("/api/v1", tags=["Platform"], summary="API index")
def api_index() -> dict:
    return {
        "name": settings.APP_NAME,
        "subtitle": settings.APP_SUBTITLE,
        "version": __version__,
        "prefix": settings.API_V1_PREFIX,
        "documentation": "/docs",
        "standards": ["OIML R 76-1:2006", "OIML R 76-2:2007"],
        # The one endpoint that answers without a token, so the home page can
        # show this instance's activity before anyone has signed in.
        "statistics": f"{settings.API_V1_PREFIX}/platform/statistics",
    }


PREFIX = settings.API_V1_PREFIX
for module in (auth, dashboard, masters, cases, tests, workflow, reports, attachments, audit, standards, ai, admin, platform):
    app.include_router(module.router, prefix=PREFIX)


# Serve the bundled frontend when it is present (single-service local demo).
_frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
