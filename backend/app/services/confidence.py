"""Confidence scoring.

Confidence is NOT a restatement of the probability. It is a weighted score over
five signals; distance from 50% enters only as a small tie-break
(MODELING_PLAN.md §6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BacktestResult, BacktestRun
from app.features.builder import FeatureVector
from app.modeling.logistic import LogisticWinModel

WEIGHTS = {
    "model_agreement": 0.25,
    "data_completeness": 0.25,
    "input_confirmation": 0.20,
    "historical_calibration": 0.20,
    "prediction_stability": 0.10,
}

# Distance from 50% may contribute at most this much of the final score.
DISTANCE_TIEBREAK_WEIGHT = 0.10

LABELS = (
    (0.75, "HIGH"),
    (0.55, "MODERATE"),
    (0.35, "LOW"),
)

INSUFFICIENT_DATA_COMPLETENESS = 0.5


@dataclass(slots=True)
class ConfidenceResult:
    score: float
    label: str
    components: dict[str, Any] = field(default_factory=dict)


def _label(score: float, completeness: float) -> str:
    if completeness < INSUFFICIENT_DATA_COMPLETENESS:
        return "INSUFFICIENT_DATA"
    for threshold, name in LABELS:
        if score >= threshold:
            return name
    return "VERY_LOW"


def prediction_stability(
    model: LogisticWinModel, frame: pd.DataFrame, vector: FeatureVector
) -> float | None:
    """How much the probability moves when uncertain inputs are perturbed.

    Features that were shrunk toward a prior or are missing entirely are nudged
    by half a training standard deviation in each direction; the spread of the
    resulting probabilities is the instability.
    """
    if model.pipeline is None:
        return None
    uncertain = [
        name
        for name in model.feature_names
        if vector.estimated_flags.get(name) or vector.features.get(name) is None
    ]
    if not uncertain:
        return 1.0

    z = model.transformed_row(frame)[0]
    clf = model.pipeline.named_steps["clf"]
    beta, intercept = clf.coef_[0], float(clf.intercept_[0])
    base_eta = intercept + float(np.dot(beta, z))

    probabilities = [1.0 / (1.0 + np.exp(-base_eta))]
    index = {name: i for i, name in enumerate(model.feature_names)}
    for name in uncertain:
        i = index[name]
        for delta in (-0.5, 0.5):
            eta = base_eta + beta[i] * delta
            probabilities.append(1.0 / (1.0 + np.exp(-eta)))

    spread = float(np.max(probabilities) - np.min(probabilities))
    # A 20-point swing under perturbation is treated as fully unstable.
    return float(max(0.0, 1.0 - spread / 0.20))


def historical_calibration(session: Session, favorite_prob: float) -> tuple[float | None, dict]:
    """Reliability of past predictions in this probability band."""
    run = session.scalar(select(BacktestRun).order_by(BacktestRun.created_at.desc()))
    if run is None:
        return None, {"reason": "no backtest run available"}

    rows = session.scalars(
        select(BacktestResult).where(
            BacktestResult.run_id == run.id,
            BacktestResult.slice_type == "probability_band",
        )
    ).all()
    if not rows:
        return None, {"reason": "no probability-band slices in the latest backtest"}

    percent = favorite_prob * 100.0
    for row in rows:
        try:
            lower, upper = (float(v) for v in row.slice_key.split("-"))
        except ValueError:
            continue
        if lower <= percent < upper or (upper >= 100 and percent >= lower):
            extra = row.extra or {}
            observed = extra.get("observed")
            predicted = extra.get("mean_predicted")
            if observed is None or predicted is None or row.n_games < 30:
                return None, {
                    "band": row.slice_key,
                    "n": row.n_games,
                    "reason": "band sample too small to judge reliability",
                }
            gap = abs(float(observed) - float(predicted))
            return max(0.0, 1.0 - gap / 0.15), {
                "band": row.slice_key,
                "n": row.n_games,
                "observed": float(observed),
                "predicted": float(predicted),
                "run_id": str(run.id),
            }
    return None, {"reason": "prediction falls outside every reported band"}


def score_confidence(
    session: Session,
    probability: float,
    vector: FeatureVector,
    model: LogisticWinModel,
    frame: pd.DataFrame,
    model_agreement: float | None = None,
    lineup_confirmed: bool = False,
) -> ConfidenceResult:
    favorite_prob = max(probability, 1 - probability)
    starters_known = 0.5 * float(vector.features.get("sp_identified_home") or 0.0) + 0.5 * float(
        vector.features.get("sp_identified_away") or 0.0
    )
    confirmation = 0.6 * starters_known + 0.4 * (1.0 if lineup_confirmed else 0.0)

    calibration, calibration_detail = historical_calibration(session, favorite_prob)
    stability = prediction_stability(model, frame, vector)

    signals: dict[str, float | None] = {
        # A single active model has nothing to disagree with. Rather than
        # inventing agreement, the signal is None and its weight redistributes.
        "model_agreement": model_agreement,
        "data_completeness": vector.completeness,
        "input_confirmation": confirmation,
        "historical_calibration": calibration,
        "prediction_stability": stability,
    }

    available = {k: v for k, v in signals.items() if v is not None}
    total_weight = sum(WEIGHTS[k] for k in available) or 1.0
    core = sum(WEIGHTS[k] * min(max(v, 0.0), 1.0) for k, v in available.items()) / total_weight

    # Distance from 50%, capped so a confident-looking number built on missing
    # inputs never outranks a modest one built on confirmed inputs.
    distance = min((favorite_prob - 0.5) / 0.15, 1.0)
    score = (1 - DISTANCE_TIEBREAK_WEIGHT) * core + DISTANCE_TIEBREAK_WEIGHT * distance

    return ConfidenceResult(
        score=round(float(min(max(score, 0.0), 1.0)), 4),
        label=_label(score, vector.completeness),
        components={
            "signals": signals,
            "weights_used": {k: WEIGHTS[k] for k in available},
            "unavailable_signals": [k for k, v in signals.items() if v is None],
            "distance_component": round(float(distance), 4),
            "historical_calibration_detail": calibration_detail,
        },
    )


def recommendation_label(
    probability: float, confidence: float, completeness: float,
    starters_known: float,
) -> str:
    """Lean strength, gated by confidence. Never a 'lock' (MODELING_PLAN.md §7)."""
    if completeness < INSUFFICIENT_DATA_COMPLETENESS:
        return "INSUFFICIENT_DATA"
    if starters_known < 1.0 and completeness < 0.65:
        return "INSUFFICIENT_DATA"
    edge = abs(probability - 0.5)
    if edge >= 0.10 and confidence >= 0.65:
        return "STRONG_LEAN"
    if edge >= 0.06 and confidence >= 0.50:
        return "MODERATE_LEAN"
    if edge >= 0.03:
        return "SMALL_LEAN"
    return "NO_MEANINGFUL_ADVANTAGE"
