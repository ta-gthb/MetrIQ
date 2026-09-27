"""MetrIQ FastAPI application entry point."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import __version__
from app.config import settings
from app.database import engine
from app.models import Base
from app.routers import (
    admin,
    ai,
    attachments,
    audit,
    auth,
    cases,
    dashboard,
    masters,
    reports,
    standards,
    tests,
    workflow,
)
from app.services.attachment_service.storage import get_storage

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("metriq")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Local/dev convenience: create the schema when it is absent. Production
    # deployments run scripts/init_db.py as an explicit release step.
    Base.metadata.create_all(bind=engine)
    Path(settings.REPORT_STORAGE_PATH).mkdir(parents=True, exist_ok=True)
    Path(settings.STORAGE_LOCAL_PATH).mkdir(parents=True, exist_ok=True)
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
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ALLOW_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Content-SHA256"],
)


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
        "auth_provider": settings.AUTH_PROVIDER,
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
    }


PREFIX = settings.API_V1_PREFIX
for module in (auth, dashboard, masters, cases, tests, workflow, reports, attachments, audit, standards, ai, admin):
    app.include_router(module.router, prefix=PREFIX)


# Serve the bundled frontend when it is present (single-service local demo).
_frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
