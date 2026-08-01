"""Blending two views of the same features, and measuring whether it helps.

The blend is a weighted average in **log-odds** space, not probability space.
Averaging probabilities pulls every prediction toward 0.5 — it is a shrinkage
operator disguised as an ensemble, and it systematically damages the tails,
which is where this model is already weakest. Log-odds averaging is the
geometric mean of the odds and preserves confident agreement.

The weight is chosen **only on out-of-sample predictions**. Selecting a blend
weight on the same rows the components were fitted on would pick whichever
model memorised harder, which is precisely the failure mode an ensemble is
supposed to guard against (MODELING_PLAN.md).

Nothing here changes what is served unless the walk-forward comparison says it
should. `compare_walk_forward` is the measurement; it reports the logistic
model alone, the GBDT alone, and the blend, on the same games.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.backtest.metrics import evaluate
from app.backtest.walkforward import Step
from app.core.config import settings
from app.core.logging import get_logger
from app.modeling.calibration import select_method
from app.modeling.dataset import LABEL_COLUMN, Dataset
from app.modeling.gbdt import GbdtWinModel
from app.modeling.logistic import LogisticWinModel

log = get_logger(__name__)

EPS = 1e-6

# Candidate weights on the GBDT component. 0.0 is included on purpose: it is
# the null hypothesis, and if it wins the ensemble has earned nothing.
WEIGHT_GRID: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5)


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _expit(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def blend(primary: np.ndarray, secondary: np.ndarray, weight: float) -> np.ndarray:
    """Weighted log-odds average. ``weight`` is the share given to ``secondary``."""
    if weight <= 0:
        return primary
    return _expit((1.0 - weight) * _logit(primary) + weight * _logit(secondary))


@dataclass(frozen=True, slots=True)
class ComponentRun:
    label: str
    predictions: pd.DataFrame  # game_id, official_date, prob, actual
    metrics: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EnsembleComparison:
    logistic: ComponentRun
    gbdt: ComponentRun
    best_weight: float
    blended: ComponentRun
    weight_grid: dict[str, dict[str, Any]]
    n_games: int
    verdict: str

    @property
    def improves(self) -> bool:
        """True only when the blend beats the logistic model on log loss."""
        base = self.logistic.metrics.get("log_loss")
        blended = self.blended.metrics.get("log_loss")
        if base is None or blended is None:
            return False
        return blended < base


def _step_predictions(
    dataset: Dataset,
    steps: list[Step],
    C: float,
    min_train_rows: int | None = None,
) -> pd.DataFrame:
    """Walk forward once, fitting both components per step.

    Both components see exactly the same training window, the same validation
    slice for calibration, and the same test window — so any difference between
    them is the model, not the split.
    """
    min_train_rows = min_train_rows or settings.min_train_rows
    names = list(dataset.feature_names)
    frame = dataset.labelled
    rows: list[pd.DataFrame] = []

    for step in steps:
        train = frame[frame["official_date"] <= step.train_end]
        test = frame[
            (frame["official_date"] >= step.test_start)
            & (frame["official_date"] <= step.test_end)
        ]
        if len(train) < min_train_rows or test.empty:
            continue

        validation = train[train["official_date"] >= step.validation_start]
        core = train[train["official_date"] < step.validation_start]
        if len(core) < min_train_rows // 2 or len(validation) < 50:
            core, validation = train, train.iloc[:0]

        method = select_method(len(validation)) if not validation.empty else None

        # Logistic: fit core, calibrate on validation, refit on the full window
        # with the calibration mapping fixed. Same protocol as the production
        # walk-forward, so the numbers are comparable to the shipped backtest.
        linear = LogisticWinModel(feature_names=names, C=C)
        linear.fit(core, LABEL_COLUMN)
        if method:
            linear.fit_calibration(validation, LABEL_COLUMN, method=method)
            keep = linear.calibrator
            linear = LogisticWinModel(feature_names=names, C=C)
            linear.fit(train, LABEL_COLUMN)
            linear.calibrator = keep

        trees = GbdtWinModel(feature_names=names)
        trees.fit(core, LABEL_COLUMN)
        if method:
            trees.fit_calibration(validation, LABEL_COLUMN, method=method)
            keep_t = trees.calibrator
            trees = GbdtWinModel(feature_names=names)
            trees.fit(train, LABEL_COLUMN)
            trees.calibrator = keep_t

        rows.append(
            pd.DataFrame(
                {
                    "game_id": test["game_id"].to_numpy(),
                    "official_date": test["official_date"].to_numpy(),
                    "actual": test[LABEL_COLUMN].astype(int).to_numpy(),
                    "p_logistic": linear.predict(test),
                    "p_gbdt": trees.predict(test),
                }
            )
        )
        log.info(
            "ensemble.step",
            train_end=str(step.train_end),
            n_train=len(train),
            n_test=len(test),
            gbdt_trees=trees.n_iterations,
        )

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _metrics(actual: np.ndarray, prob: np.ndarray) -> dict[str, Any]:
    m = evaluate(actual, prob)
    return {
        "n": m.n,
        "log_loss": m.log_loss,
        "brier_score": m.brier_score,
        "calibration_error": m.calibration_error,
        "accuracy": m.accuracy,
        "roc_auc": m.roc_auc,
    }


def compare_walk_forward(
    dataset: Dataset,
    steps: list[Step],
    C: float,
    min_train_rows: int | None = None,
    weight_grid: tuple[float, ...] = WEIGHT_GRID,
) -> EnsembleComparison | None:
    """Measure logistic vs GBDT vs blend on identical out-of-sample games.

    Returns None when the walk-forward produced nothing to compare.
    """
    frame = _step_predictions(dataset, steps, C, min_train_rows)
    if frame.empty:
        return None

    actual = frame["actual"].to_numpy()
    p_lin = frame["p_logistic"].to_numpy()
    p_gbd = frame["p_gbdt"].to_numpy()

    grid: dict[str, dict[str, Any]] = {}
    for weight in weight_grid:
        grid[str(weight)] = _metrics(actual, blend(p_lin, p_gbd, weight))

    best_weight = min(
        grid, key=lambda w: grid[w]["log_loss"] if grid[w]["log_loss"] is not None else 1e9
    )
    best = float(best_weight)
    p_blend = blend(p_lin, p_gbd, best)

    logistic = ComponentRun("logistic", frame, _metrics(actual, p_lin))
    gbdt = ComponentRun("gbdt", frame, _metrics(actual, p_gbd))
    blended = ComponentRun(f"blend@{best}", frame, _metrics(actual, p_blend))

    base_ll = logistic.metrics["log_loss"]
    blend_ll = blended.metrics["log_loss"]
    if best == 0.0:
        verdict = (
            "The out-of-sample weight search chose 0.0 — the boosted component "
            "earns no weight, and the logistic model is served unchanged."
        )
    elif base_ll is not None and blend_ll is not None and blend_ll < base_ll:
        verdict = (
            f"Blending at weight {best} improves out-of-sample log loss from "
            f"{base_ll:.4f} to {blend_ll:.4f} over {len(frame):,} games."
        )
    else:
        verdict = (
            "No blend weight improved out-of-sample log loss; the logistic model "
            "is served unchanged."
        )

    return EnsembleComparison(
        logistic=logistic,
        gbdt=gbdt,
        best_weight=best,
        blended=blended,
        weight_grid=grid,
        n_games=len(frame),
        verdict=verdict,
    )


__all__ = [
    "WEIGHT_GRID",
    "ComponentRun",
    "EnsembleComparison",
    "blend",
    "compare_walk_forward",
]
