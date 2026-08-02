"""Why a prediction moved, decomposed exactly rather than described.

`diff_predictions` already answers *what changed* — it lists the features whose
values differ between two snapshots. That is a useful audit record and a poor
explanation: twenty features move between two snapshots and the list says
nothing about which of them moved the number.

This answers *how much each change was worth*, and it does so exactly. The
served probability is built in three stages, and every one of them is additive
in **log-odds**:

    logit(served) = (1 − w)·logit(calibrated) + w·logit(simulation)
    logit(raw)    = intercept + Σ_j β_j·z_j

so a change between two snapshots decomposes with no residual:

    Δ logit(served) = (1 − w)·[ Σ_j β_j·Δz_j  +  Δcalibration ]  +  w·Δ logit(sim)

Three properties of that make it worth building rather than approximating.

**It is exact, not attributed.** No SHAP sampling, no leave-one-out ordering
effect, no "approximately". The logistic model is linear in log-odds and the
blend is linear in log-odds, so the terms are the decomposition rather than an
estimate of it. The residual is asserted to be zero in a test.

**Probability points are a presentation, not the arithmetic.** The sigmoid is
not additive, so per-feature contributions cannot sum to the probability move.
They are computed in log-odds and then scaled by the observed move, and the
scaling factor is reported so a reader can tell which quantity is which.

**Calibration and the simulation are separated from the features.** A prediction
can move because a team played a game, or because the calibrator shifted, or
because the run simulation changed its mind. Those are different explanations
and folding them together would attribute a simulation's opinion to a feature.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.core.logging import get_logger
from app.db.models import Prediction
from app.features.registry import CATEGORY_LABELS, REGISTRY
from app.modeling.logistic import LogisticWinModel

log = get_logger(__name__)

EPS = 1e-6

#: Log-odds movement below this is rounding, not a change worth naming.
NEGLIGIBLE = 1e-9


def _logit(p: float | None) -> float | None:
    if p is None:
        return None
    clipped = min(max(float(p), EPS), 1 - EPS)
    return math.log(clipped / (1 - clipped))


@dataclass(frozen=True, slots=True)
class FeatureMove:
    """One feature's contribution to a probability move."""

    feature_key: str
    display_name: str
    category: str
    category_label: str
    previous: float | None
    current: float | None
    #: Exact additive contribution to the log-odds move.
    log_odds_delta: float
    #: The same, rescaled to the observed probability move. A presentation.
    contribution_pp: float
    favors: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_key": self.feature_key,
            "display_name": self.display_name,
            "category": self.category,
            "category_label": self.category_label,
            "previous": self.previous,
            "current": self.current,
            "log_odds_delta": round(self.log_odds_delta, 6),
            "contribution_pp": round(self.contribution_pp, 3),
            "favors": self.favors,
        }


@dataclass(frozen=True, slots=True)
class ChangeExplanation:
    """The full decomposition of one move, stage by stage."""

    has_previous: bool
    move_pp: float
    #: Stage totals, in log-odds. These sum to the total move exactly.
    features_log_odds: float
    calibration_log_odds: float
    simulation_log_odds: float
    total_log_odds: float
    #: Sum of stage terms minus the actual move. Asserted zero by test.
    residual_log_odds: float
    drivers: list[FeatureMove]
    #: Present when one side of the comparison had no simulation and the other
    #: did, which makes the simulation term a switch rather than a movement.
    simulation_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_previous": self.has_previous,
            "move_pp": round(self.move_pp, 3),
            "stages": {
                "features": round(self.features_log_odds, 6),
                "calibration": round(self.calibration_log_odds, 6),
                "simulation": round(self.simulation_log_odds, 6),
                "total": round(self.total_log_odds, 6),
                "residual": round(self.residual_log_odds, 9),
            },
            "drivers": [d.to_dict() for d in self.drivers],
            "simulation_note": self.simulation_note,
        }


NO_CHANGE = ChangeExplanation(
    has_previous=False, move_pp=0.0, features_log_odds=0.0,
    calibration_log_odds=0.0, simulation_log_odds=0.0, total_log_odds=0.0,
    residual_log_odds=0.0, drivers=[],
)


def _features(prediction: Prediction) -> dict[str, Any]:
    return (prediction.feature_snapshot or {}).get("features", {}) or {}


def _blend_weight(prediction: Prediction) -> float:
    blend = (prediction.feature_snapshot or {}).get("blend") or {}
    weight = blend.get("weight_on_simulation")
    return 0.0 if weight is None else float(weight)


def explain_change(
    current: Prediction,
    previous: Prediction | None,
    model: LogisticWinModel,
    limit: int = 10,
) -> ChangeExplanation:
    """Decompose the move from ``previous`` to ``current``.

    ``model`` supplies the coefficients. It must be the version both predictions
    were issued under — a decomposition against a different model's coefficients
    would be arithmetic about a model that never produced either number, so the
    caller checks and this function trusts.
    """
    if previous is None:
        return NO_CHANGE

    served_now = _logit(float(current.home_win_prob))
    served_before = _logit(float(previous.home_win_prob))
    total = served_now - served_before
    move_pp = (float(current.home_win_prob) - float(previous.home_win_prob)) * 100.0

    # Stage 1: the features, through the uncalibrated linear model. Exact.
    raw_now = _logit(_as_float(current.home_win_prob_uncalibrated))
    raw_before = _logit(_as_float(previous.home_win_prob_uncalibrated))
    now_features, before_features = _features(current), _features(previous)

    drivers: list[FeatureMove] = []
    feature_log_odds = 0.0
    if raw_now is not None and raw_before is not None:
        feature_log_odds = raw_now - raw_before
        drivers = _feature_moves(
            model, now_features, before_features, total, move_pp,
            weight=_blend_weight(current),
        )

    # Stage 2: calibration. Whatever the calibrator did that the linear model
    # did not — a real source of movement, and not a feature's doing.
    cal_now = _logit(_component(current, "logistic_calibrated"))
    cal_before = _logit(_component(previous, "logistic_calibrated"))
    calibration = 0.0
    calibrated_delta = 0.0
    if cal_now is not None and cal_before is not None:
        calibrated_delta = cal_now - cal_before
        calibration = calibrated_delta - feature_log_odds

    # Stage 3: the simulation, at the weight actually used.
    weight_now, weight_before = _blend_weight(current), _blend_weight(previous)
    sim_now = _logit(_component(current, "simulation"))
    sim_before = _logit(_component(previous, "simulation"))
    note: str | None = None
    if sim_now is not None and sim_before is not None and weight_now == weight_before:
        simulation = weight_now * (sim_now - sim_before)
        features_term = (1 - weight_now) * feature_log_odds
        calibration_term = (1 - weight_now) * calibration
    else:
        # One side had no simulation, or the weight moved. The blend changed
        # shape rather than value, and pretending otherwise would attribute a
        # structural switch to the run model changing its mind.
        features_term = (1 - weight_now) * feature_log_odds
        calibration_term = (1 - weight_now) * calibration
        simulation = total - features_term - calibration_term
        if (sim_now is None) != (sim_before is None):
            note = (
                "The simulation became available between these two snapshots."
                if sim_before is None
                else "The simulation was withheld for the later snapshot."
            )
        elif weight_now != weight_before:
            note = "The blend weight changed between these two snapshots."

    residual = total - (features_term + calibration_term + simulation)
    return ChangeExplanation(
        has_previous=True,
        move_pp=move_pp,
        features_log_odds=features_term,
        calibration_log_odds=calibration_term,
        simulation_log_odds=simulation,
        total_log_odds=total,
        residual_log_odds=residual,
        drivers=drivers[:limit],
        simulation_note=note,
    )


def _as_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _component(prediction: Prediction, key: str) -> float | None:
    return _as_float((prediction.component_probs or {}).get(key))


def _feature_moves(
    model: LogisticWinModel,
    now: dict[str, Any],
    before: dict[str, Any],
    total_log_odds: float,
    move_pp: float,
    weight: float,
) -> list[FeatureMove]:
    """Per-feature contributions to the SERVED log-odds, ranked.

    Scaled by ``1 - weight`` rather than left as the raw model's own movement.
    A feature that moves the logistic model by 0.2 in log-odds moves the served
    probability by half of that when the simulation carries the other half, and
    reporting the unscaled figure would credit each feature with roughly twice
    the influence it actually had.
    """
    try:
        terms_now = model.log_odds_terms(_frame(model, now))
        terms_before = model.log_odds_terms(_frame(model, before))
    except Exception as exc:  # noqa: BLE001 - an explanation must not break a page
        log.warning("timeline.terms_failed", error=str(exc))
        return []

    # The pp column is the exact log-odds split rescaled to the move that
    # actually happened. Without a move there is nothing to scale, and inventing
    # a denominator would turn rounding noise into a driver.
    scale = (move_pp / total_log_odds) if abs(total_log_odds) > NEGLIGIBLE else 0.0

    moves: list[FeatureMove] = []
    for key in sorted(set(terms_now) | set(terms_before)):
        delta = (1 - weight) * (terms_now.get(key, 0.0) - terms_before.get(key, 0.0))
        if abs(delta) <= NEGLIGIBLE:
            continue
        meta = REGISTRY.get(key)
        moves.append(
            FeatureMove(
                feature_key=key,
                display_name=meta.display_name if meta else key,
                category=str(meta.category) if meta else "",
                category_label=(
                    CATEGORY_LABELS.get(str(meta.category), str(meta.category))
                    if meta else ""
                ),
                previous=_as_float(before.get(key)),
                current=_as_float(now.get(key)),
                log_odds_delta=delta,
                contribution_pp=delta * scale,
                favors="H" if delta > 0 else "A",
            )
        )
    moves.sort(key=lambda m: abs(m.log_odds_delta), reverse=True)
    return moves


def _frame(model: LogisticWinModel, features: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame([{name: features.get(name) for name in model.feature_names}])


__all__ = ["ChangeExplanation", "FeatureMove", "explain_change"]
