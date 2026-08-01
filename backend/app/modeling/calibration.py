"""Probability calibration.

Both isotonic regression and Platt (sigmoid) scaling are supported. The
calibrator is fit on a validation window later than train and earlier than
test, and is applied — never re-fit — at prediction time
(MODELING_PLAN.md §5).

With one MLB season ~2,430 games, isotonic needs more validation data than a
single-season window provides, so ``sigmoid`` is the default. ``select_method``
implements the documented rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

CalibrationMethod = Literal["sigmoid", "isotonic", "identity"]

# Below this many validation samples, isotonic overfits the bin edges.
ISOTONIC_MIN_SAMPLES = 3000
EPS = 1e-6


@dataclass
class Calibrator:
    method: CalibrationMethod
    params: dict[str, Any] = field(default_factory=dict)
    _sigmoid_model: LogisticRegression | None = None
    _isotonic_model: IsotonicRegression | None = None
    n_fit: int = 0

    def transform(self, probabilities: np.ndarray) -> np.ndarray:
        p = np.clip(np.asarray(probabilities, dtype=float), EPS, 1 - EPS)
        if self.method == "identity":
            return p
        if self.method == "sigmoid":
            if self._sigmoid_model is None:
                return p
            logit = np.log(p / (1 - p)).reshape(-1, 1)
            return np.clip(self._sigmoid_model.predict_proba(logit)[:, 1], EPS, 1 - EPS)
        if self._isotonic_model is None:
            return p
        return np.clip(self._isotonic_model.predict(p), EPS, 1 - EPS)


def select_method(n_validation: int, requested: str | None = None) -> CalibrationMethod:
    if requested in ("sigmoid", "isotonic", "identity"):
        return requested  # type: ignore[return-value]
    return "isotonic" if n_validation >= ISOTONIC_MIN_SAMPLES else "sigmoid"


def fit_calibrator(
    raw_probabilities: np.ndarray, y: np.ndarray, method: str = "sigmoid"
) -> Calibrator:
    p = np.clip(np.asarray(raw_probabilities, dtype=float), EPS, 1 - EPS)
    y = np.asarray(y, dtype=int)

    if len(p) == 0 or len(np.unique(y)) < 2:
        # Not enough signal to calibrate; pass probabilities through unchanged
        # rather than fitting a degenerate transform.
        return Calibrator(method="identity", n_fit=len(p),
                          params={"reason": "insufficient validation data"})

    chosen = select_method(len(p), method)
    if chosen == "identity":
        return Calibrator(method="identity", n_fit=len(p))

    if chosen == "sigmoid":
        logit = np.log(p / (1 - p)).reshape(-1, 1)
        model = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
        model.fit(logit, y)
        return Calibrator(
            method="sigmoid",
            _sigmoid_model=model,
            n_fit=len(p),
            params={
                "slope": float(model.coef_[0][0]),
                "intercept": float(model.intercept_[0]),
            },
        )

    model = IsotonicRegression(out_of_bounds="clip", y_min=EPS, y_max=1 - EPS)
    model.fit(p, y)
    return Calibrator(
        method="isotonic", _isotonic_model=model, n_fit=len(p),
        params={"n_thresholds": int(len(getattr(model, "X_thresholds_", [])))},
    )
