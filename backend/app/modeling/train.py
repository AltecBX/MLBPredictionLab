"""Training entry point.

Hyperparameter selection is walk-forward by date. There is no random
cross-validation anywhere (LEAKAGE_PREVENTION.md §8).
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.backtest.metrics import evaluate
from app.backtest.walkforward import (
    MIN_CALIBRATION_ROWS,
    collect_predictions,
    make_steps,
    run_walk_forward,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ModelVersion
from app.features.asof import AsOfStore
from app.features.registry import feature_keys
from app.ingestion.status import job_run
from app.modeling.calibration import choose_calibration
from app.modeling.dataset import LABEL_COLUMN, Dataset, build_dataset
from app.modeling.logistic import C_GRID, LogisticWinModel
from app.modeling.promotion import decide_promotion, incumbent_columns
from app.modeling.registry import get_active_version, next_version, register_model

log = get_logger(__name__)


def incumbent_matrix(
    session: Session,
    dataset: Dataset,
    incumbent: ModelVersion | None,
    seasons: list[int] | None,
    store: AsOfStore | None,
) -> Dataset | None:
    """The active model's own matrix, built only when the gate cannot do without it.

    The usual feature-set change adds columns, and the gate scores the incumbent
    on its own columns of the candidate's matrix — no second build, which
    matters because a build is the slow part of training. When the active model
    was fitted on a column the candidate's matrix no longer carries, its matrix
    is built once here on the same store, so the two are still scored on the
    same games. A retired feature set is left to the gate, which holds and says
    why.
    """
    if incumbent is None or incumbent.feature_set_version == dataset.feature_set_version:
        return None
    names = incumbent_columns(incumbent)
    if names is None or all(name in dataset.frame.columns for name in names):
        return None
    try:
        feature_keys(incumbent.feature_set_version)
    except KeyError:
        return None
    log.info(
        "train.incumbent_matrix",
        feature_set=incumbent.feature_set_version,
        missing=[name for name in names if name not in dataset.frame.columns],
    )
    return build_dataset(
        session,
        seasons=seasons,
        store=store,
        feature_set_version=incumbent.feature_set_version,
    )

# The final calibrator is fit on the most recent slice, held out of the fit
# that produces it, then the classifier is refit on everything.
FINAL_VALIDATION_DAYS = 60


def select_hyperparameters(
    dataset: Dataset, step_days: int = 45, grid: tuple[float, ...] = C_GRID
) -> tuple[float, dict[str, Any]]:
    """Pick C by walk-forward out-of-sample log loss."""
    steps = make_steps(dataset.labelled, step_days=step_days)
    if not steps:
        return grid[len(grid) // 2], {"reason": "no walk-forward steps available"}

    scores: dict[float, dict[str, Any]] = {}
    for candidate in grid:
        results = run_walk_forward(dataset, steps, C=candidate)
        frame = collect_predictions(results)
        if frame.empty:
            continue
        metrics = evaluate(frame["actual"].to_numpy(), frame["prob"].to_numpy())
        scores[candidate] = {
            "log_loss": metrics.log_loss,
            "brier_score": metrics.brier_score,
            "calibration_error": metrics.calibration_error,
            "n": metrics.n,
        }
        log.info("train.grid", C=candidate, log_loss=metrics.log_loss, n=metrics.n)

    if not scores:
        return grid[len(grid) // 2], {"reason": "walk-forward produced no predictions"}

    best = min(scores, key=lambda c: scores[c]["log_loss"] or float("inf"))
    return best, {"grid": {str(k): v for k, v in scores.items()}, "selected_C": best}


def fit_final_model(dataset: Dataset, C: float) -> LogisticWinModel:
    """Fit on all labelled rows; calibrate on a held-out recent slice."""
    frame = dataset.labelled
    if frame.empty:
        raise ValueError("Cannot train: dataset contains no labelled rows.")

    last_date = max(frame["official_date"])
    cutoff = last_date.fromordinal(last_date.toordinal() - FINAL_VALIDATION_DAYS)
    core = frame[frame["official_date"] < cutoff]
    validation = frame[frame["official_date"] >= cutoff]

    if len(core) < settings.min_train_rows or len(validation) < MIN_CALIBRATION_ROWS:
        core, validation = frame, frame.iloc[:0]

    calibrated = LogisticWinModel(feature_names=list(dataset.feature_names), C=C)
    calibrated.fit(core, LABEL_COLUMN)
    fitted_calibrator = None
    choice = None
    if not validation.empty:
        # Which calibrator, measured rather than assumed from the row count.
        # Both are fitted on the earlier part of the validation window and
        # scored on the later part, chronologically.
        raw = calibrated.predict_raw(validation)
        choice = choose_calibration(raw, validation[LABEL_COLUMN].astype(int).to_numpy())
        calibrated.fit_calibration(validation, LABEL_COLUMN, method=choice.method)
        fitted_calibrator = calibrated.calibrator

    final = LogisticWinModel(feature_names=list(dataset.feature_names), C=C)
    final.fit(frame, LABEL_COLUMN)
    final.calibrator = fitted_calibrator
    final.extra = {
        "calibration_rows": int(len(validation)),
        "calibration_cutoff": cutoff.isoformat() if not validation.empty else None,
        "calibration_choice": None if choice is None else choice.to_dict(),
    }
    return final


def train_model(
    session: Session,
    seasons: list[int] | None = None,
    activate: bool = True,
    step_days: int = 45,
    name: str | None = None,
) -> dict[str, Any]:
    """Build the dataset, select C walk-forward, fit, evaluate and register."""
    name = name or settings.active_model_name

    with job_run(session, "train_model", seasons=seasons or "all") as run:
        # The store is loaded once so that, should the gate need the incumbent's
        # own matrix, the second build reads the same games without a reload.
        store = AsOfStore.load(session, seasons)
        dataset = build_dataset(session, seasons=seasons, store=store)
        if dataset.frame.empty:
            raise ValueError(
                "No training rows available. Ingest schedule and boxscore history first."
            )

        C, search = select_hyperparameters(dataset, step_days=step_days)

        # Out-of-sample metrics for the selected C — the numbers that get
        # registered. In-sample fit is never reported as performance.
        steps = make_steps(dataset.labelled, step_days=step_days)
        results = run_walk_forward(dataset, steps, C=C)
        oos = collect_predictions(results)
        metrics = evaluate(oos["actual"].to_numpy(), oos["prob"].to_numpy()) if not oos.empty else None

        # Whether this replaces what is being served is a separate question
        # from whether it trains. A nightly refit that always activates means a
        # configuration that got worse by chance becomes the product.
        incumbent = None
        if activate:
            try:
                incumbent = get_active_version(session, name)
            except Exception:  # noqa: BLE001 - no active model is the first-run case
                incumbent = None
        decision = decide_promotion(
            dataset,
            steps,
            C,
            incumbent,
            incumbent_dataset=incumbent_matrix(session, dataset, incumbent, seasons, store),
        )
        log.info(
            "model.promotion",
            verdict=decision.verdict,
            activate=decision.should_activate,
            candidate_C=C,
            incumbent_C=decision.incumbent_C,
            candidate_feature_set=decision.candidate_feature_set,
            incumbent_feature_set=decision.incumbent_feature_set,
            reason=decision.reason,
        )
        should_activate = activate and decision.should_activate

        # A tie on C alone sends the refit back to the incumbent's C; the
        # registered out-of-sample figures then describe the C that was fitted.
        if decision.fit_C is not None and abs(decision.fit_C - C) > 1e-12:
            C = decision.fit_C
            results = run_walk_forward(dataset, steps, C=C)
            oos = collect_predictions(results)
            metrics = (
                evaluate(oos["actual"].to_numpy(), oos["prob"].to_numpy())
                if not oos.empty
                else None
            )

        model = fit_final_model(dataset, C)
        version = next_version(session, name)
        labelled = dataset.labelled

        registered = register_model(
            session,
            model,
            name=name,
            version=version,
            feature_set_version=dataset.feature_set_version,
            train_start=min(labelled["official_date"]),
            train_end=max(labelled["official_date"]),
            metrics={
                "out_of_sample": metrics.to_dict() if metrics else None,
                "hyperparameter_search": search,
                "n_walk_forward_steps": len([r for r in results if not r.skipped]),
                "n_steps_skipped": len([r for r in results if r.skipped]),
                "promotion": decision.to_dict(),
            },
            hyperparameters={
                "C": C,
                "penalty": "l2",
                "solver": "lbfgs",
                "seed": settings.random_seed,
                "as_of_policy": dataset.as_of_policy,
                "step_days": step_days,
            },
            notes=(
                f"Walk-forward selected C={C}. Out-of-sample log loss "
                f"{metrics.log_loss:.4f} over {metrics.n} games."
                if metrics and metrics.log_loss is not None
                else "Registered without out-of-sample metrics (insufficient history)."
            ),
            activate=should_activate,
        )
        run.rows_written = model.train_rows

    return {
        "model_version_id": registered.id,
        "name": name,
        "version": version,
        "C": C,
        "train_rows": model.train_rows,
        "feature_set_version": dataset.feature_set_version,
        "out_of_sample": {
            "n": metrics.n if metrics else 0,
            "log_loss": metrics.log_loss if metrics else None,
            "brier_score": metrics.brier_score if metrics else None,
            "calibration_error": metrics.calibration_error if metrics else None,
            "accuracy": metrics.accuracy if metrics else None,
            "roc_auc": metrics.roc_auc if metrics else None,
        },
        "activated": should_activate,
        "promotion": decision.to_dict(),
    }


def coefficient_report(model: LogisticWinModel) -> list[dict[str, Any]]:
    coefficients = model.coefficients
    ordered = sorted(coefficients.items(), key=lambda kv: abs(kv[1]), reverse=True)
    total = sum(abs(v) for v in coefficients.values()) or 1.0
    return [
        {
            "feature": name,
            "coefficient": value,
            "abs_share": abs(value) / total,
        }
        for name, value in ordered
    ]


def dominant_feature_share(model: LogisticWinModel) -> float:
    values = np.abs(np.array(list(model.coefficients.values())))
    return float(values.max() / values.sum()) if values.sum() else 0.0
