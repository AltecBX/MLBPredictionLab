"""Diagnostics and metadata endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.core.cache import health as cache_health
from app.core.clock import utcnow
from app.core.config import settings
from app.db.session import db_health
from app.features.registry import DEFERRED, FS_V1, CATEGORY_LABELS
from app.providers.registry import configured_categories
from app.services.diagnostics import diagnostics_snapshot
from app.services.freshness import freshness_report

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.get("/health", summary="Service, database, cache and source health")
def health(session: Session = Depends(db_session)) -> dict[str, Any]:
    database = db_health()
    cache = cache_health()
    freshness = freshness_report(session)
    degraded = [f for f in freshness if f["freshness"] in ("STALE", "UNAVAILABLE")]
    return {
        "status": "ok" if database.get("reachable") else "degraded",
        "generated_at": utcnow(),
        "environment": settings.environment,
        "database": database,
        "cache": cache,
        "degraded_categories": [f["category"] for f in degraded],
    }


@router.get("", summary="Full diagnostics snapshot")
def snapshot(session: Session = Depends(db_session)) -> dict[str, Any]:
    return diagnostics_snapshot(session)


@router.get("/sources", summary="Per-category source status and freshness")
def sources(session: Session = Depends(db_session)) -> dict[str, Any]:
    return {
        "generated_at": utcnow(),
        "configured": configured_categories(),
        "freshness": freshness_report(session),
    }
