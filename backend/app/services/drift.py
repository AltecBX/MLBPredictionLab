"""Has the world moved away from the data the model was fitted on?

A model does not decay because its code changed. It decays because the games
stop looking like the ones it was trained on — a rule change, a scoring
environment that shifts, a roster churn that moves a feature's whole
distribution — and none of that shows up in a test suite or a CI run. Two things
are measured here, and a third is named as not measurable yet.

**Feature drift, by population stability index.** For each feature, compare the
distribution over recent predictions against the distribution over the model's
own training window:

    PSI = Σ_bins (recent_share − reference_share) · ln(recent_share / reference_share)

The conventional reading — under 0.10 stable, 0.10 to 0.25 moderate, above 0.25
a materially different population — is a convention and is labelled as one. It
is not a threshold this repository measured, and the bands are reported beside
the number rather than instead of it.

**Calibration drift.** The model's registered out-of-sample calibration error
against the calibration error of the predictions it has actually issued on games
that have since finished. Predictions on unfinished games are excluded — there
is no outcome to calibrate against, and including them would quietly compute a
statistic over a biased subset of the slate.

**Importance stability is not measured here.** It would need each version's
standardized coefficients stored side by side, and only the active model's are
loadable. The gap is named rather than approximated with something weaker.

Nothing in this module gates anything. It reports, and `promotion.py` decides —
a drift number is a reason to look, not a rule for what to serve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.db.models import Game, ModelVersion, Prediction

log = get_logger(__name__)

#: Conventional PSI bands. A convention, not a measurement — see the docstring.
PSI_STABLE = 0.10
PSI_MODERATE = 0.25

#: Quantile bins for the reference distribution.
PSI_BINS = 10

#: A share floor, so an empty bin produces a large finite contribution rather
#: than an infinity that makes the whole index unreportable.
MIN_SHARE = 1e-4

#: Predictions newer than this form the "recent" window.
RECENT_DAYS = 30

#: Below this many observations on either side, a PSI is noise about a small
#: sample rather than a statement about the population.
MIN_SAMPLE = 50


@dataclass(frozen=True, slots=True)
class FeatureDrift:
    feature_key: str
    psi: float
    band: str
    n_reference: int
    n_recent: int
    reference_mean: float | None
    recent_mean: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_key": self.feature_key,
            "psi": round(self.psi, 4),
            "band": self.band,
            "n_reference": self.n_reference,
            "n_recent": self.n_recent,
            "reference_mean": (
                None if self.reference_mean is None else round(self.reference_mean, 4)
            ),
            "recent_mean": None if self.recent_mean is None else round(self.recent_mean, 4),
        }


def _band(psi: float) -> str:
    if psi < PSI_STABLE:
        return "STABLE"
    if psi < PSI_MODERATE:
        return "MODERATE"
    return "SHIFTED"


def population_stability_index(
    reference: np.ndarray, recent: np.ndarray, bins: int = PSI_BINS
) -> float | None:
    """PSI of ``recent`` against ``reference``, binned on reference quantiles.

    Quantile bins rather than equal-width: a feature whose reference values pile
    up in a narrow range would otherwise put every observation in one equal-width
    bin and report perfect stability regardless of what happened.
    """
    reference = reference[np.isfinite(reference)]
    recent = recent[np.isfinite(recent)]
    if reference.size < MIN_SAMPLE or recent.size < MIN_SAMPLE:
        return None

    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    if edges.size < 3:
        # A near-constant feature has no distribution to compare. Reporting 0
        # would claim stability that was never measured.
        return None
    edges[0], edges[-1] = -np.inf, np.inf

    ref_counts, _ = np.histogram(reference, bins=edges)
    new_counts, _ = np.histogram(recent, bins=edges)
    ref_share = np.maximum(ref_counts / reference.size, MIN_SHARE)
    new_share = np.maximum(new_counts / recent.size, MIN_SHARE)
    return float(np.sum((new_share - ref_share) * np.log(new_share / ref_share)))


def _feature_matrix(rows: list[Prediction]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for row in rows:
        features = (row.feature_snapshot or {}).get("features") or {}
        for key, value in features.items():
            if value is None:
                continue
            out.setdefault(key, []).append(float(value))
    return out


def feature_drift(
    session: Session, active: ModelVersion, recent_days: int = RECENT_DAYS
) -> list[FeatureDrift]:
    """PSI per feature, recent predictions against the training window.

    The reference is the model's own training window rather than "all older
    predictions": the question is whether today's games look like what the model
    learned from, and a reference drawn from predictions the same model already
    drifted through would answer a different one.
    """
    if active.train_end_date is None:
        return []
    cutoff = utcnow() - timedelta(days=recent_days)

    reference_rows = list(
        session.scalars(
            select(Prediction).where(
                Prediction.model_version_id == active.id,
                Prediction.as_of < cutoff,
            )
        )
    )
    recent_rows = list(
        session.scalars(
            select(Prediction).where(
                Prediction.model_version_id == active.id,
                Prediction.as_of >= cutoff,
            )
        )
    )
    if not reference_rows or not recent_rows:
        return []

    reference = _feature_matrix(reference_rows)
    recent = _feature_matrix(recent_rows)

    drifts: list[FeatureDrift] = []
    for key in sorted(set(reference) & set(recent)):
        ref = np.asarray(reference[key], dtype=float)
        new = np.asarray(recent[key], dtype=float)
        psi = population_stability_index(ref, new)
        if psi is None:
            continue
        drifts.append(
            FeatureDrift(
                feature_key=key,
                psi=psi,
                band=_band(psi),
                n_reference=int(ref.size),
                n_recent=int(new.size),
                reference_mean=float(ref.mean()),
                recent_mean=float(new.mean()),
            )
        )
    drifts.sort(key=lambda d: d.psi, reverse=True)
    return drifts


def calibration_drift(session: Session, active: ModelVersion) -> dict[str, Any]:
    """Registered out-of-sample calibration against what has actually happened.

    Scored only on predictions whose game has finished. A prediction on a game
    that has not been played has no outcome to be calibrated against, and
    including it would compute the statistic over whichever games happen to have
    finished — which is not a random subset of a slate.
    """
    registered = ((active.metrics or {}).get("out_of_sample") or {}).get(
        "calibration_error"
    )
    rows = list(
        session.execute(
            select(Prediction.home_win_prob, Game.home_win)
            .join(Game, Game.id == Prediction.game_id)
            .where(
                Prediction.model_version_id == active.id,
                Prediction.is_latest.is_(True),
                Game.is_final.is_(True),
                Game.home_win.is_not(None),
            )
        ).all()
    )
    if len(rows) < MIN_SAMPLE:
        return {
            "available": False,
            "reason": (
                f"Only {len(rows)} finished games have a prediction from this model "
                f"version; {MIN_SAMPLE} are needed before a calibration reading "
                f"says anything."
            ),
            "registered_calibration_error": registered,
        }

    probs = np.array([float(p) for p, _ in rows])
    actual = np.array([1.0 if w else 0.0 for _, w in rows])
    observed = float(abs(probs.mean() - actual.mean()))
    return {
        "available": True,
        "n_finished": len(rows),
        "registered_calibration_error": registered,
        "observed_calibration_error": round(observed, 5),
        "drift": None if registered is None else round(observed - float(registered), 5),
        "mean_predicted": round(float(probs.mean()), 5),
        "observed_rate": round(float(actual.mean()), 5),
    }


def drift_report(session: Session) -> dict[str, Any]:
    """Everything the diagnostics screen shows about drift."""
    active = session.scalar(select(ModelVersion).where(ModelVersion.is_active.is_(True)))
    if active is None:
        return {
            "available": False,
            "reason": "No active model version, so there is nothing to compare against.",
        }

    try:
        features = feature_drift(session, active)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not break the page
        log.warning("drift.features_failed", error=str(exc))
        features = []

    shifted = [d for d in features if d.band != "STABLE"]
    return {
        "available": True,
        "model_version": active.version,
        "bands": {
            "stable_below": PSI_STABLE,
            "shifted_above": PSI_MODERATE,
            "note": (
                "Conventional PSI bands, not thresholds measured on this data."
            ),
        },
        "recent_window_days": RECENT_DAYS,
        "features": [d.to_dict() for d in features[:20]],
        "n_features_compared": len(features),
        "n_features_shifted": len(shifted),
        "calibration": calibration_drift(session, active),
        "importance_stability": {
            "available": False,
            "reason": (
                "Comparing coefficient stability across versions needs each "
                "version's standardized coefficients stored; only the active "
                "model's artifact is loadable."
            ),
        },
    }


__all__ = [
    "MIN_SAMPLE",
    "PSI_MODERATE",
    "PSI_STABLE",
    "FeatureDrift",
    "calibration_drift",
    "drift_report",
    "feature_drift",
    "population_stability_index",
]
