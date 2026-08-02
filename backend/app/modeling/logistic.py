"""Model 1 — regularized logistic regression with probability calibration.

Chosen as the Phase 1 baseline because it is monotone, inspectable, hard to
overfit on ~2,400 games a season, and yields exact per-feature contributions in
probability points without needing SHAP (MODELING_PLAN.md §3).

The scaler, imputer and calibrator all live inside the artifact and are fit
strictly inside the training fold (LEAKAGE_PREVENTION.md §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.core.config import settings
from app.modeling.calibration import Calibrator, fit_calibrator

# Regularization grid searched by walk-forward validation only. It extends well
# below the usual default because MLB game features are noisy and the optimum
# must land inside the grid, not on its edge.
# The lower end is measured, not guessed: walk-forward log loss over four
# seasons rises again below 0.001 (0.6847 at 0.0003, 0.6867 at 0.0001), so the
# selected value lands inside the grid rather than on its edge.
C_GRID = (0.0001, 0.0003, 0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0)


def _sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-x))


@dataclass
class LogisticWinModel:
    """Fitted artifact: imputer + scaler + logistic regression + calibrator."""

    feature_names: list[str]
    C: float = 0.1
    seed: int = settings.random_seed
    pipeline: Pipeline | None = None
    calibrator: Calibrator | None = None
    train_rows: int = 0
    algorithm: str = "logistic_regression_l2"
    extra: dict[str, Any] = field(default_factory=dict)

    # -- fitting -----------------------------------------------------------
    def _make_pipeline(self) -> Pipeline:
        return Pipeline(
            [
                ("impute", SimpleImputer(strategy="median", add_indicator=False)),
                ("scale", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        C=self.C,
                        penalty="l2",
                        solver="lbfgs",
                        max_iter=2000,
                        random_state=self.seed,
                    ),
                ),
            ]
        )

    def fit(self, frame: pd.DataFrame, label_column: str = "home_win") -> LogisticWinModel:
        X = self.matrix(frame)
        y = frame[label_column].astype(int).to_numpy()
        self.pipeline = self._make_pipeline()
        self.pipeline.fit(X, y)
        self.train_rows = len(frame)
        return self

    def fit_calibration(
        self, frame: pd.DataFrame, label_column: str = "home_win", method: str = "sigmoid"
    ) -> LogisticWinModel:
        """Fit the calibrator on a validation slice that the model did not see."""
        if self.pipeline is None:
            raise RuntimeError("fit() must be called before fit_calibration().")
        raw = self.predict_raw(frame)
        y = frame[label_column].astype(int).to_numpy()
        self.calibrator = fit_calibrator(raw, y, method=method)
        return self

    # -- prediction --------------------------------------------------------
    def matrix(self, frame: pd.DataFrame) -> np.ndarray:
        missing = [name for name in self.feature_names if name not in frame.columns]
        if missing:
            raise KeyError(f"Feature frame is missing columns: {missing}")
        return frame[self.feature_names].astype(float).to_numpy()

    def predict_raw(self, frame: pd.DataFrame) -> np.ndarray:
        if self.pipeline is None:
            raise RuntimeError("Model is not fitted.")
        return self.pipeline.predict_proba(self.matrix(frame))[:, 1]

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        raw = self.predict_raw(frame)
        if self.calibrator is None:
            return raw
        return self.calibrator.transform(raw)

    # -- explanation -------------------------------------------------------
    @property
    def coefficients(self) -> dict[str, float]:
        if self.pipeline is None:
            raise RuntimeError("Model is not fitted.")
        clf: LogisticRegression = self.pipeline.named_steps["clf"]
        return dict(zip(self.feature_names, clf.coef_[0].tolist(), strict=True))

    @property
    def intercept(self) -> float:
        if self.pipeline is None:
            raise RuntimeError("Model is not fitted.")
        return float(self.pipeline.named_steps["clf"].intercept_[0])

    def transformed_row(self, frame: pd.DataFrame) -> np.ndarray:
        """Imputed and scaled feature row(s) — the vector the classifier sees."""
        if self.pipeline is None:
            raise RuntimeError("Model is not fitted.")
        X = self.matrix(frame)
        X = self.pipeline.named_steps["impute"].transform(X)
        return self.pipeline.named_steps["scale"].transform(X)

    def contributions(self, frame: pd.DataFrame) -> list[dict[str, float]]:
        """Exact leave-one-out contribution of each feature, in probability points.

            contribution_pp_i = 100 * [ sigma(eta) - sigma(eta - beta_i * z_i) ]

        Additive in log-odds and reported in the units a reader understands
        (MODELING_PLAN.md §3).
        """
        if self.pipeline is None:
            raise RuntimeError("Model is not fitted.")
        z = self.transformed_row(frame)
        clf: LogisticRegression = self.pipeline.named_steps["clf"]
        beta = clf.coef_[0]
        intercept = float(clf.intercept_[0])

        out: list[dict[str, float]] = []
        for row in z:
            eta = intercept + float(np.dot(beta, row))
            base = _sigmoid(eta)
            terms = beta * row
            out.append(
                {
                    name: float(100.0 * (base - _sigmoid(eta - term)))
                    for name, term in zip(self.feature_names, terms, strict=True)
                }
            )
        return out

    def log_odds_terms(self, frame: pd.DataFrame) -> dict[str, float]:
        """Each feature's additive contribution to the log-odds: ``beta_i * z_i``.

        Distinct from `contributions`, which is a leave-one-out reading in
        probability points at a single moment. These terms are what a *change*
        between two moments decomposes into, and they do so exactly: the log-odds
        is linear in them, so the differences sum to the total move with no
        residual and no approximation. Probability points cannot do that — the
        sigmoid is not additive — which is why the attribution is computed here
        and converted afterwards.
        """
        if self.pipeline is None:
            raise RuntimeError("Model is not fitted.")
        z = self.transformed_row(frame)[0]
        clf: LogisticRegression = self.pipeline.named_steps["clf"]
        beta = clf.coef_[0]
        return {
            name: float(b * value)
            for name, b, value in zip(self.feature_names, beta, z, strict=True)
        }

    def standardized_importance(self) -> dict[str, float]:
        """Absolute standardized coefficient — comparable across features."""
        return {k: abs(v) for k, v in self.coefficients.items()}

    def to_metadata(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "C": self.C,
            "seed": self.seed,
            "train_rows": self.train_rows,
            "n_features": len(self.feature_names),
            "intercept": self.intercept if self.pipeline is not None else None,
            "calibration_method": self.calibrator.method if self.calibrator else None,
            **self.extra,
        }
