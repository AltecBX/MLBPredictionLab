"""Walk-forward backtest engine.

Trains only on games before each prediction date, stores row-level output so
any slice can be recomputed, and runs the ablation suite and sanity gates
(BACKTEST_PLAN.md).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.backtest.ablation import run_ablation, sanity_flags
from app.backtest.metrics import evaluate
from app.backtest.slices import compute_slices
from app.backtest.walkforward import (
    collect_predictions,
    importance_stability,
    make_steps,
    run_walk_forward,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import BacktestPrediction, BacktestResult, BacktestRun
from app.db.upsert import upsert
from app.ingestion.status import job_run
from app.modeling.dataset import build_dataset
from app.modeling.train import dominant_feature_share, select_hyperparameters
from app.modeling.logistic import LogisticWinModel
from app.modeling.dataset import LABEL_COLUMN

log = get_logger(__name__)


def run_backtest(
    session: Session,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 30,
    seasons: list[int] | None = None,
    ablation: bool = True,
    C: float | None = None,
) -> dict[str, Any]:
    with job_run(session, "run_backtest", step_days=step_days) as job:
        dataset = build_dataset(session, seasons=seasons)
        if dataset.frame.empty:
            raise ValueError(
                "No backtest rows available. Ingest schedule and boxscore history first."
            )

        if C is None:
            C, _search = select_hyperparameters(dataset, step_days=45)

        steps = make_steps(dataset.labelled, start=start, end=end, step_days=step_days)
        results = run_walk_forward(dataset, steps, C=C, min_train_rows=settings.min_train_rows)
        frame = collect_predictions(results)
        if frame.empty:
            raise ValueError(
                "Walk-forward produced no predictions. The training window may be "
                f"shorter than the {settings.min_train_rows}-row minimum."
            )

        metrics = evaluate(frame["actual"].to_numpy(), frame["prob"].to_numpy())

        # A full-data reference fit, used only to measure how concentrated the
        # model's weight is. Never reported as performance.
        reference = LogisticWinModel(feature_names=list(dataset.feature_names), C=C)
        reference.fit(dataset.labelled, LABEL_COLUMN)
        flags = sanity_flags(frame, metrics, dominant_feature_share(reference))

        run_id = uuid.uuid4()
        skipped = [r for r in results if r.skipped]
        run_row = BacktestRun(
            id=run_id,
            model_name=settings.active_model_name,
            algorithm=reference.algorithm,
            feature_set_version=dataset.feature_set_version,
            as_of_policy=dataset.as_of_policy,
            start_date=min(frame["official_date"]),
            end_date=max(frame["official_date"]),
            step_days=step_days,
            validation_days=settings.backtest_validation_days,
            min_train_rows=settings.min_train_rows,
            seed=settings.random_seed,
            git_sha=settings.git_sha,
            n_games=int(len(frame)),
            n_steps=len(results) - len(skipped),
            n_steps_skipped=len(skipped),
            sanity_flags=flags,
            config={
                "C": C,
                "skipped_steps": [
                    {
                        "test_start": str(r.step.test_start),
                        "test_end": str(r.step.test_end),
                        "reason": r.reason,
                    }
                    for r in skipped
                ],
                "importance_stability": importance_stability(results),
            },
        )
        session.add(run_row)
        session.flush()

        _store_predictions(session, run_id, frame)
        _store_slices(session, run_id, compute_slices(frame))

        if ablation:
            rows = run_ablation(
                dataset, steps, C, frame, min_train_rows=settings.min_train_rows
            )
            _store_ablation(session, run_id, rows)

        job.rows_written = int(len(frame))

    summary: dict[str, Any] = {
        "run_id": str(run_id),
        "n_games": int(len(frame)),
        "start_date": str(min(frame["official_date"])),
        "end_date": str(max(frame["official_date"])),
        "steps": len(results) - len(skipped),
        "steps_skipped": len(skipped),
        "C": C,
        "metrics": {
            "log_loss": metrics.log_loss,
            "brier_score": metrics.brier_score,
            "calibration_error": metrics.calibration_error,
            "max_calibration_error": metrics.max_calibration_error,
            "accuracy": metrics.accuracy,
            "roc_auc": metrics.roc_auc,
            "baseline_log_loss": metrics.baseline_log_loss,
        },
        "sanity_flags": flags,
    }
    log.info("backtest.complete", **{k: v for k, v in summary.items() if k != "metrics"})
    return summary


def _store_predictions(session: Session, run_id: uuid.UUID, frame) -> None:
    rows = [
        {
            "run_id": run_id,
            "game_id": int(row.game_id),
            "as_of": row.as_of,
            "predicted_home_win_prob": float(row.prob),
            "actual_home_win": bool(row.actual),
            "train_end_date": row.train_end,
            "n_train_rows": int(row.n_train),
            "season": int(row.season),
            "month": int(row.month),
            "lineup_confirmed": bool(row.lineup_confirmed),
            "starter_quality_index": (
                float(row.starter_quality_index)
                if row.starter_quality_index is not None
                and row.starter_quality_index == row.starter_quality_index
                else None
            ),
            "features": {},
        }
        for row in frame.itertuples()
    ]
    upsert(session, BacktestPrediction, rows, ["run_id", "game_id"], update=False)


def _store_slices(session: Session, run_id: uuid.UUID, slices) -> None:
    rows = []
    for item in slices:
        m = item.metrics
        rows.append(
            {
                "run_id": run_id,
                "slice_type": item.slice_type,
                "slice_key": item.slice_key,
                "n_games": m.n,
                "accuracy": m.accuracy,
                "log_loss": m.log_loss,
                "brier_score": m.brier_score,
                "calibration_error": m.calibration_error,
                "max_calibration_error": m.max_calibration_error,
                "roc_auc": m.roc_auc,
                # ROI and CLV stay NULL unless a licensed odds provider has
                # supplied timestamped historical prices. They are omitted, not
                # reported as zero (BACKTEST_PLAN.md §3).
                "roi": None,
                "clv": None,
                "extra": {
                    **item.extra,
                    "mean_predicted": m.mean_predicted,
                    "observed_rate": m.observed_rate,
                    "bins": [b.__dict__ for b in m.bins] if item.slice_type == "overall" else [],
                },
            }
        )
    upsert(session, BacktestResult, rows, ["run_id", "slice_type", "slice_key"])


def _store_ablation(session: Session, run_id: uuid.UUID, rows) -> None:
    payload = [
        {
            "run_id": run_id,
            "slice_type": "ablation",
            "slice_key": row.group,
            "n_games": row.n_games or 0,
            "log_loss": row.log_loss,
            "extra": row.to_dict(),
        }
        for row in rows
    ]
    upsert(session, BacktestResult, payload, ["run_id", "slice_type", "slice_key"])


def prune_old_runs(session: Session, keep: int = 10) -> int:
    """Retain only the most recent runs; row-level output is bulky."""
    ids = [
        r[0]
        for r in session.query(BacktestRun.id)
        .order_by(BacktestRun.created_at.desc())
        .offset(keep)
        .all()
    ]
    if not ids:
        return 0
    session.execute(delete(BacktestRun).where(BacktestRun.id.in_(ids)))
    return len(ids)
