"""FastAPI application entry point.

Run (development):  uvicorn app.main:app --reload --port 8001
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    analysis,
    assessments,
    auth,
    borrowers,
    dashboard,
    diligence,
    documents,
    organization,
    reports,
    review,
    system,
)
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.ratelimit import RateLimitMiddleware

log = logging.getLogger("intellicredit")
API_PREFIX = "/api/v1"
UPLOAD_SLACK = 1024 * 1024  # multipart overhead allowance


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
    problems = settings.production_problems()
    if problems:
        for p in problems:
            log.critical("insecure production configuration: %s", p)
        raise RuntimeError("Refusing to start with insecure production settings: " + " ".join(problems))
    from app.core import storage
    from app.services import handlers  # noqa: F401  (register job handlers)
    from app.services.jobs import recover_jobs, shutdown

    if settings.STORAGE_BACKEND == "local":
        storage.storage_root()
    from app.core.ratelimit import limiter

    limiter.configure()
    if settings.JOB_BACKEND == "thread":
        try:
            recover_jobs()
        except Exception as exc:  # noqa: BLE001 - DB may not be migrated yet; readiness will report it
            log.error("job recovery skipped: %s", exc)
    log.info("startup complete", extra={"ctx": {"environment": settings.ENVIRONMENT, "job_backend": settings.JOB_BACKEND}})
    yield
    await limiter.aclose()
    shutdown()
    from app.core.database import engine

    engine.dispose()
    log.info("shutdown complete")


def create_app() -> FastAPI:
    # Also checked here, not only in the lifespan hook: some serverless runtimes never run ASGI startup events.
    problems = settings.production_problems()
    if problems:
        raise RuntimeError("Refusing to start with insecure production settings: " + " ".join(problems))
    app = FastAPI(title="Intelli-Credit API", version=system.VERSION, lifespan=lifespan,
                  description="AI-assisted MSME credit assessment — decision support, subject to human review.",
                  docs_url="/api/docs" if settings.ENVIRONMENT != "production" else None,
                  openapi_url="/api/openapi.json" if settings.ENVIRONMENT != "production" else None, redoc_url=None,
                  swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect" if settings.ENVIRONMENT != "production" else None)

    @app.middleware("http")
    async def _limit_body(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_upload_bytes + UPLOAD_SLACK:
            return JSONResponse(status_code=413, content={"error": {"code": "file_too_large", "details": {},
                                "message": f"Request exceeds the {settings.MAX_UPLOAD_MB} MB upload limit."}})
        return await call_next(request)

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
                       allow_headers=["authorization", "content-type", "x-ic-client", "x-request-id"],
                       expose_headers=["content-disposition", "x-request-id"])
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "details": {},
                            "message": "An unexpected error occurred. The issue has been logged."}})

    app.include_router(system.health_router)  # /health, /ready (unversioned, for orchestrators)
    v1 = APIRouter(prefix=API_PREFIX)
    for r in (system.health_router, system.router, auth.router, organization.router, borrowers.router, assessments.router, documents.router,
              analysis.router, diligence.router, reports.router, review.router, dashboard.router):
        v1.include_router(r)
    app.include_router(v1)
    return app


app = create_app()
