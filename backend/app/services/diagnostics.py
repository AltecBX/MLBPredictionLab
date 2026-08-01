"""Internal diagnostics.

Surfaces failed jobs, missing data, stale sources, prediction failures, model
health and database/cache state (ARCHITECTURE.md §10).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.cache import health as cache_health
from app.core.clock import utcnow
from app.core.config import settings
from app.db.models import (
    BacktestResult,
    BacktestRun,
    DataSourceStatus,
    Game,
    JobRun,
    ModelVersion,
    PlayerGameStat,
    Prediction,
    RawSourcePayload,
    TeamGameStat,
)
from app.db.session import db_health
from app.features.registry import DEFERRED, FS_V1
from app.providers.registry import configured_categories
from app.services.freshness import freshness_report, refresh_freshness

__all__ = ["refresh_freshness", "diagnostics_snapshot"]


def _recent_jobs(session: Session, limit: int = 25) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "job_name": r.job_name,
            "status": r.status,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "duration_ms": (
                int((r.finished_at - r.started_at).total_seconds() * 1000)
                if r.finished_at
                else None
            ),
            "rows_written": r.rows_written,
            "error": r.error,
            "details": r.details or {},
        }
        for r in rows
    ]


def _missing_data(session: Session) -> dict[str, Any]:
    total_games = session.scalar(select(func.count()).select_from(Game)) or 0
    final_games = (
        session.scalar(select(func.count()).select_from(Game).where(Game.is_final.is_(True)))
        or 0
    )
    games_with_box = (
        session.scalar(select(func.count(func.distinct(TeamGameStat.game_id)))) or 0
    )
    upcoming = list(
        session.scalars(
            select(Game).where(
                Game.is_final.is_(False), Game.game_date_utc >= utcnow()
            ).limit(500)
        )
    )
    missing_starters = sum(
        1
        for g in upcoming
        if g.home_probable_pitcher_id is None or g.away_probable_pitcher_id is None
    )
    return {
        "total_games": total_games,
        "final_games": final_games,
        "games_with_boxscore": games_with_box,
        "final_games_missing_boxscore": max(final_games - games_with_box, 0),
        "upcoming_games": len(upcoming),
        "upcoming_missing_probable_starter": missing_starters,
        "player_game_rows": session.scalar(select(func.count()).select_from(PlayerGameStat)) or 0,
        "raw_payloads": session.scalar(select(func.count()).select_from(RawSourcePayload)) or 0,
    }


def _model_health(session: Session) -> dict[str, Any]:
    versions = list(
        session.scalars(
            select(ModelVersion).order_by(ModelVersion.trained_at.desc()).limit(10)
        )
    )
    active = next((v for v in versions if v.is_active), None)
    return {
        "active": (
            {
                "id": active.id,
                "name": active.name,
                "version": active.version,
                "algorithm": active.algorithm,
                "feature_set_version": active.feature_set_version,
                "trained_at": active.trained_at,
                "train_rows": active.train_rows,
                "train_start_date": active.train_start_date,
                "train_end_date": active.train_end_date,
                "calibration_method": active.calibration_method,
                "hyperparameters": active.hyperparameters,
                "out_of_sample": (active.metrics or {}).get("out_of_sample", {}),
                "artifact_sha256": active.artifact_sha256,
                "git_sha": active.git_sha,
            }
            if active
            else None
        ),
        "unavailable_reason": None if active else "No active model version. Run `make train`.",
        "history": [
            {
                "id": v.id,
                "version": v.version,
                "trained_at": v.trained_at,
                "train_rows": v.train_rows,
                "is_active": v.is_active,
                "log_loss": ((v.metrics or {}).get("out_of_sample") or {}).get("log_loss"),
            }
            for v in versions
        ],
    }


def _prediction_health(session: Session) -> dict[str, Any]:
    now = utcnow()
    total = session.scalar(select(func.count()).select_from(Prediction)) or 0
    latest = session.scalar(select(func.max(Prediction.created_at)))
    stale_cutoff = now - timedelta(hours=6)

    upcoming = list(
        session.scalars(
            select(Game).where(
                Game.is_final.is_(False),
                Game.game_date_utc >= now,
                Game.game_date_utc <= now + timedelta(days=2),
            )
        )
    )
    upcoming_ids = [g.id for g in upcoming]
    predicted = set()
    if upcoming_ids:
        predicted = {
            row[0]
            for row in session.execute(
                select(Prediction.game_id).where(
                    Prediction.game_id.in_(upcoming_ids), Prediction.is_latest.is_(True)
                )
            ).all()
        }
    failures = list(
        session.scalars(
            select(JobRun)
            .where(JobRun.job_name == "generate_predictions", JobRun.status == "FAILED")
            .order_by(JobRun.started_at.desc())
            .limit(10)
        )
    )
    return {
        "total_predictions": total,
        "latest_created_at": latest,
        "is_stale": bool(latest and latest < stale_cutoff),
        "upcoming_games_next_48h": len(upcoming_ids),
        "upcoming_games_without_prediction": len(set(upcoming_ids) - predicted),
        "recent_failures": [
            {"id": r.id, "started_at": r.started_at, "error": r.error} for r in failures
        ],
    }


def _backtest_health(session: Session) -> dict[str, Any]:
    run = session.scalar(select(BacktestRun).order_by(BacktestRun.created_at.desc()))
    if run is None:
        return {"available": False, "reason": "No backtest has been run yet."}
    overall = session.scalar(
        select(BacktestResult).where(
            BacktestResult.run_id == run.id, BacktestResult.slice_type == "overall"
        )
    )
    return {
        "available": True,
        "run_id": str(run.id),
        "created_at": run.created_at,
        "n_games": run.n_games,
        "sanity_flags": run.sanity_flags or [],
        "log_loss": float(overall.log_loss) if overall and overall.log_loss else None,
        "brier_score": float(overall.brier_score) if overall and overall.brier_score else None,
        "calibration_error": (
            float(overall.calibration_error)
            if overall and overall.calibration_error
            else None
        ),
        "importance_stability": (run.config or {}).get("importance_stability", {}),
    }


def _api_usage(session: Session) -> dict[str, Any]:
    day_ago = utcnow() - timedelta(days=1)
    rows = session.execute(
        select(RawSourcePayload.source_name, func.count())
        .where(RawSourcePayload.retrieved_at >= day_ago)
        .group_by(RawSourcePayload.source_name)
    ).all()
    return {
        "distinct_payloads_last_24h": {name: int(count) for name, count in rows},
        "note": "Counts distinct payloads stored; identical consecutive responses "
                "deduplicate on content hash, so this understates request volume.",
    }


def diagnostics_snapshot(session: Session) -> dict[str, Any]:
    sources = list(session.scalars(select(DataSourceStatus).order_by(DataSourceStatus.category)))
    configured = configured_categories()
    return {
        "generated_at": utcnow(),
        "environment": settings.environment,
        "database": db_health(),
        "cache": cache_health(),
        "sources": [
            {
                "source_name": s.source_name,
                "category": s.category,
                "status": s.status,
                "freshness": s.freshness,
                "last_success_at": s.last_success_at,
                "last_failure_at": s.last_failure_at,
                "consecutive_failures": s.consecutive_failures,
                "last_error": s.last_error,
                "records_last_run": s.records_last_run,
                "configured_provider": configured.get(s.category),
                "detail": s.detail,
            }
            for s in sources
        ],
        "freshness": freshness_report(session),
        "jobs": _recent_jobs(session),
        "failed_jobs": [j for j in _recent_jobs(session, limit=100) if j["status"] == "FAILED"][:15],
        "missing_data": _missing_data(session),
        "model": _model_health(session),
        "predictions": _prediction_health(session),
        "backtest": _backtest_health(session),
        "api_usage": _api_usage(session),
        "feature_set": {
            "active_version": settings.feature_set_version,
            "active_features": len(FS_V1),
            "deferred_features": len(DEFERRED),
            "deferred_by_phase": {
                str(phase): len([f for f in DEFERRED if f.phase == phase])
                for phase in sorted({f.phase for f in DEFERRED})
            },
        },
        "drift": {
            "available": False,
            "reason": "Feature distribution drift and calibration drift monitoring "
                      "arrive in Phase 4 alongside automated retraining.",
        },
    }
