"""Backtest reporting endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.backtest.metrics import ALWAYS_FIFTY_LOG_LOSS
from app.db.models import BacktestResult, BacktestRun

router = APIRouter(prefix="/backtest", tags=["backtest"])


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _slice_payload(row: BacktestResult) -> dict[str, Any]:
    return {
        "slice_type": row.slice_type,
        "slice_key": row.slice_key,
        "n_games": row.n_games,
        "accuracy": _f(row.accuracy),
        "log_loss": _f(row.log_loss),
        "brier_score": _f(row.brier_score),
        "calibration_error": _f(row.calibration_error),
        "max_calibration_error": _f(row.max_calibration_error),
        "roc_auc": _f(row.roc_auc),
        # ROI and CLV are omitted rather than zeroed when no licensed odds
        # provider has supplied timestamped historical prices.
        "roi": _f(row.roi),
        "clv": _f(row.clv),
        "extra": row.extra or {},
    }


def _run_payload(session: Session, run: BacktestRun) -> dict[str, Any]:
    rows = list(
        session.scalars(select(BacktestResult).where(BacktestResult.run_id == run.id))
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    overall: dict[str, Any] | None = None
    for row in rows:
        payload = _slice_payload(row)
        if row.slice_type == "overall":
            overall = payload
        grouped.setdefault(row.slice_type, []).append(payload)

    for key in grouped:
        grouped[key].sort(key=lambda p: p["slice_key"])

    calibration_bins = (overall or {}).get("extra", {}).get("bins", [])

    return {
        "run_id": str(run.id),
        "model_name": run.model_name,
        "algorithm": run.algorithm,
        "feature_set_version": run.feature_set_version,
        "as_of_policy": run.as_of_policy,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "step_days": run.step_days,
        "validation_days": run.validation_days,
        "min_train_rows": run.min_train_rows,
        "seed": run.seed,
        "git_sha": run.git_sha,
        "n_games": run.n_games,
        "n_steps": run.n_steps,
        "n_steps_skipped": run.n_steps_skipped,
        "created_at": run.created_at,
        "sanity_flags": run.sanity_flags or [],
        "config": run.config or {},
        "baseline_log_loss": ALWAYS_FIFTY_LOG_LOSS,
        "overall": overall,
        "calibration_bins": calibration_bins,
        "slices": grouped,
        "odds_dependent_metrics": {
            "available": False,
            "reason": "ROI and closing-line value require a licensed odds provider "
                      "with timestamped historical prices. They are omitted rather "
                      "than reported as zero.",
        },
    }


@router.get("/latest", summary="Most recent walk-forward backtest")
def latest_backtest(session: Session = Depends(db_session)) -> dict[str, Any]:
    run = session.scalar(select(BacktestRun).order_by(BacktestRun.created_at.desc()))
    if run is None:
        raise HTTPException(
            status_code=404,
            detail="No backtest has been run yet. Run `make backtest`.",
        )
    return _run_payload(session, run)


@router.get("/runs", summary="Backtest run history")
def list_runs(session: Session = Depends(db_session), limit: int = 20) -> dict[str, Any]:
    runs = list(
        session.scalars(
            select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
        )
    )
    return {
        "count": len(runs),
        "runs": [
            {
                "run_id": str(r.id),
                "created_at": r.created_at,
                "model_name": r.model_name,
                "feature_set_version": r.feature_set_version,
                "start_date": r.start_date,
                "end_date": r.end_date,
                "n_games": r.n_games,
                "n_steps": r.n_steps,
                "sanity_flags": r.sanity_flags or [],
            }
            for r in runs
        ],
    }


@router.get("/runs/{run_id}", summary="A specific backtest run")
def get_run(run_id: str, session: Session = Depends(db_session)) -> dict[str, Any]:
    try:
        identifier = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid run id.") from exc
    run = session.get(BacktestRun, identifier)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Backtest run {run_id} not found.")
    return _run_payload(session, run)
