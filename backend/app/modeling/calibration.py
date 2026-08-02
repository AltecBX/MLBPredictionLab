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


@dataclass(frozen=True, slots=True)
class CalibrationChoice:
    """Which calibrator was picked, and the evidence for picking it."""

    method: str
    reason: str
    scores: dict[str, float | None]
    n_fit: int
    n_score: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "reason": self.reason,
            "scores": {k: (None if v is None else round(v, 6)) for k, v in self.scores.items()},
            "n_fit": self.n_fit,
            "n_score": self.n_score,
        }


def choose_calibration(
    raw_probabilities: np.ndarray, y: np.ndarray, holdout_fraction: float = 0.4
) -> CalibrationChoice:
    """Pick isotonic or Platt by measuring both, not by counting rows.

    The previous rule chose isotonic above a sample-size threshold and Platt
    below it. That is a reasonable prior and it is not a measurement: isotonic
    is far more flexible, so on a model whose miscalibration is close to a simple
    monotone stretch it can fit the validation slice's noise and come out worse
    on anything else.

    So both are fitted on the earlier part of the validation window and scored on
    the later part. The split is **chronological**, like every other split in
    this repository — a random one would let a calibrator see games from after
    the ones it is scored on.

    **Ties go to Platt.** It has two parameters against isotonic's step function,
    and when two candidates cannot be told apart the one with less freedom is the
    one that is less likely to have been fitted to noise.
    """
    p = np.clip(np.asarray(raw_probabilities, dtype=float), EPS, 1 - EPS)
    y = np.asarray(y, dtype=int)
    n = len(p)

    cut = int(n * (1 - holdout_fraction))
    fit_p, fit_y = p[:cut], y[:cut]
    score_p, score_y = p[cut:], y[cut:]

    unusable = (
        n < ISOTONIC_MIN_SAMPLES
        or len(score_p) < 50
        or len(np.unique(fit_y)) < 2
        or len(np.unique(score_y)) < 2
    )
    if unusable:
        return CalibrationChoice(
            method="sigmoid",
            reason=(
                f"Too little validation data to choose by measurement "
                f"({n} rows, {len(score_p)} scoreable). Platt is the default "
                f"because it has the fewer parameters."
            ),
            scores={"sigmoid": None, "isotonic": None},
            n_fit=len(fit_p),
            n_score=len(score_p),
        )

    # Imported here: `feature_set_compare` pulls in `train`, which imports this.
    from app.backtest.feature_set_compare import _paired_bootstrap

    per_game: dict[str, np.ndarray] = {}
    scores: dict[str, float | None] = {}
    for method in ("sigmoid", "isotonic"):
        calibrator = fit_calibrator(fit_p, fit_y, method=method)
        losses = _per_game_loss(score_y, calibrator.transform(score_p))
        per_game[method] = losses
        scores[method] = float(losses.mean())

    # Isotonic has to beat Platt by more than noise, not merely beat it. On a
    # miscalibration Platt can already express, the extra flexibility buys a
    # difference in the fifth decimal roughly half the time, and adopting on
    # that is how a more complex model wins a coin toss. Same paired-bootstrap
    # standard every other decision in this repository is held to.
    delta = _paired_bootstrap(per_game["sigmoid"] - per_game["isotonic"])
    if delta.is_distinguishable_from_zero and delta.favours_candidate:
        return CalibrationChoice(
            method="isotonic",
            reason=(
                f"Isotonic beat Platt on held-out log loss over {len(score_p)} "
                f"games by {delta.mean:.5f}, interval "
                f"[{delta.ci_low:.5f}, {delta.ci_high:.5f}] excluding zero."
            ),
            scores=scores, n_fit=len(fit_p), n_score=len(score_p),
        )
    return CalibrationChoice(
        method="sigmoid",
        reason=(
            f"Isotonic did not beat Platt distinguishably over {len(score_p)} "
            f"games (delta {delta.mean:+.5f}, interval "
            f"[{delta.ci_low:.5f}, {delta.ci_high:.5f}]). A difference inside "
            f"the noise band goes to the model with fewer parameters."
        ),
        scores=scores, n_fit=len(fit_p), n_score=len(score_p),
    )


def _per_game_loss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    """Log loss per row, so the two calibrators can be compared pairwise."""
    clipped = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(clipped) + (1 - y) * np.log(1 - clipped))
