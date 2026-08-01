"""FastAPI application entry point."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core.config import settings
from app.core.errors import DataUnavailableError, JerryError, ModelNotFoundError
from app.core.logging import configure_logging, get_logger

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info(
        "api.startup",
        environment=settings.environment,
        model=settings.active_model_name,
        feature_set=settings.feature_set_version,
    )
    if settings.sentry_dsn:
        try:  # pragma: no cover - optional dependency
            import sentry_sdk

            sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)
            log.info("api.sentry_enabled")
        except ImportError:
            log.warning("api.sentry_unavailable", detail="sentry-sdk is not installed")
    yield
    log.info("api.shutdown")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Transparent, historically validated MLB win probabilities. Every "
        "prediction is an immutable, timestamped record with an explicit "
        "completeness and freshness state."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging(request: Request, call_next):  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    if request.url.path.startswith(settings.api_prefix):
        log.info(
            "api.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
    return response


@app.exception_handler(ModelNotFoundError)
async def model_not_found_handler(request: Request, exc: ModelNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc), "code": "MODEL_UNAVAILABLE"})


@app.exception_handler(DataUnavailableError)
async def data_unavailable_handler(
    request: Request, exc: DataUnavailableError
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc), "code": "DATA_UNAVAILABLE"})


@app.exception_handler(JerryError)
async def domain_error_handler(request: Request, exc: JerryError) -> JSONResponse:
    log.error("api.domain_error", error=str(exc), type=type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": str(exc), "code": "DOMAIN_ERROR"})


@app.get("/health", tags=["ops"], summary="Process liveness")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


app.include_router(api_router, prefix=settings.api_prefix)
