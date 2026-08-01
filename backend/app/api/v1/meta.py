"""Feature dictionary and model metadata, served from the registry."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.core.config import settings
from app.db.models import ModelVersion
from app.features.registry import CATEGORY_LABELS, DEFERRED, FS_V1

router = APIRouter(prefix="/meta", tags=["meta"])


def _spec_payload(spec: Any) -> dict[str, Any]:
    return {
        "key": spec.key,
        "display_name": spec.display_name,
        "category": str(spec.category),
        "category_label": CATEGORY_LABELS.get(str(spec.category), str(spec.category)),
        "description": spec.description,
        "unit": spec.unit,
        "window": spec.window,
        "min_sample": spec.min_sample,
        "phase": spec.phase,
        "available": spec.available,
        "source_category": spec.source_category,
    }


@router.get("/features", summary="Feature dictionary — active and deferred")
def features() -> dict[str, Any]:
    return {
        "feature_set_version": settings.feature_set_version,
        "active": [_spec_payload(s) for s in FS_V1],
        "deferred": [_spec_payload(s) for s in DEFERRED],
        "categories": CATEGORY_LABELS,
    }


@router.get("/models", summary="Registered model versions")
def models(session: Session = Depends(db_session)) -> dict[str, Any]:
    rows = list(
        session.scalars(select(ModelVersion).order_by(ModelVersion.trained_at.desc()))
    )
    return {
        "count": len(rows),
        "models": [
            {
                "id": r.id,
                "name": r.name,
                "version": r.version,
                "algorithm": r.algorithm,
                "feature_set_version": r.feature_set_version,
                "trained_at": r.trained_at,
                "train_start_date": r.train_start_date,
                "train_end_date": r.train_end_date,
                "train_rows": r.train_rows,
                "hyperparameters": r.hyperparameters,
                "calibration_method": r.calibration_method,
                "metrics": r.metrics,
                "is_active": r.is_active,
                "artifact_sha256": r.artifact_sha256,
                "git_sha": r.git_sha,
                "notes": r.notes,
            }
            for r in rows
        ],
    }
