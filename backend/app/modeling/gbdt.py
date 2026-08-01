"""Gradient-boosted trees as a second view of the same feature set.

Why add one at all: the logistic model is additive in log-odds and cannot
represent an interaction. "A tired bullpen matters more behind a short starter"
is exactly that shape, and if such interactions carry signal a tree ensemble
will find them where the linear model structurally cannot.

Why it does not simply replace the logistic model:

  1. The explanation layer decomposes the *linear* model exactly — each
     contribution is a real leave-one-out effect in log-odds that sums with the
     intercept to the probability. That property is the product's whole claim
     about itself, and a boosted ensemble does not have it.
  2. On ~9,000 rows and 42 correlated features, trees overfit readily. Whether
     they help is an empirical question, not an assumption.

So this is built as a *component*, its blend weight is chosen on out-of-sample
performance only (never in-sample — MODELING_PLAN.md), and it is wired into
serving only if walk-forward evidence says it earns its place. The measurement
lives in `ensemble.py`.

Interface mirrors LogisticWinModel deliberately: same fit / fit_calibration /
predict_raw / predict, so the walk-forward harness treats them alike.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from app.core.config import settings
from app.modeling.calibration import Calibrator, fit_calibrator

# Deliberately conservative. The dataset is small and wide, and an unconstrained
# booster will memorise it: shallow trees, a low learning rate, a leaf-size
# floor and an early-stopping fraction taken from the *end* of the training
# window rather than at random.
DEFAULT_PARAMS: dict[str, Any] = {
    "max_depth": 3,
    "max_leaf_nodes": 8,
    "learning_rate": 0.03,
    "max_iter": 400,
    "min_samples_leaf": 60,
    "l2_regularization": 1.0,
    "max_bins": 128,
    "early_stopping": True,
    "n_iter_no_change": 25,
    "validation_fraction": 0.15,
}


@dataclass
class GbdtWinModel:
    """Fitted artifact: HistGradientBoosting + calibrator.

    No imputer or scaler: HistGradientBoosting handles NaN natively by learning
    a default direction per split, which is strictly better than the median
    imputation the linear pipeline needs — a missing sample size is
    informative, and median-filling it discards that.
    """

    feature_names: list[str]
    seed: int = settings.random_seed
    params: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_PARAMS))
    model: HistGradientBoostingClassifier | None = None
    calibrator: Calibrator | None = None
    train_rows: int = 0
    algorithm: str = "hist_gradient_boosting"
    extra: dict[str, Any] = field(default_factory=dict)

    def matrix(self, frame: pd.DataFrame) -> np.ndarray:
        missing = [name for name in self.feature_names if name not in frame.columns]
        if missing:
            raise KeyError(f"Feature frame is missing columns: {missing}")
        return frame[self.feature_names].astype(float).to_numpy()

    def fit(self, frame: pd.DataFrame, label_column: str = "home_win") -> GbdtWinModel:
        X = self.matrix(frame)
        y = frame[label_column].astype(int).to_numpy()
        self.model = HistGradientBoostingClassifier(random_state=self.seed, **self.params)
        self.model.fit(X, y)
        self.train_rows = len(frame)
        return self

    def fit_calibration(
        self, frame: pd.DataFrame, label_column: str = "home_win", method: str = "sigmoid"
    ) -> GbdtWinModel:
        if self.model is None:
            raise RuntimeError("fit() must be called before fit_calibration().")
        raw = self.predict_raw(frame)
        y = frame[label_column].astype(int).to_numpy()
        self.calibrator = fit_calibrator(raw, y, method=method)
        return self

    def predict_raw(self, frame: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Model is not fitted.")
        return self.model.predict_proba(self.matrix(frame))[:, 1]

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        raw = self.predict_raw(frame)
        return raw if self.calibrator is None else self.calibrator.transform(raw)

    @property
    def n_iterations(self) -> int | None:
        """Trees actually kept after early stopping — the honest complexity."""
        return None if self.model is None else int(self.model.n_iter_)


__all__ = ["DEFAULT_PARAMS", "GbdtWinModel"]
