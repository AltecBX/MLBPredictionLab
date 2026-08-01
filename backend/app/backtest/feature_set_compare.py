"""Walk-forward comparison of two feature sets.

The single question this answers: does adding a candidate group to the model
improve out-of-sample performance? Not accuracy alone — log loss, Brier score
and calibration error, because a model that is right slightly more often while
being wildly overconfident is worse, not better (MODELING_PLAN.md).

Three things make the comparison honest:

* **Same games.** Only games both models predicted are scored. A candidate
  group whose presence changes which steps have enough training rows would
  otherwise be measured on a different sample than the baseline, which is not a
  comparison at all.
* **Same protocol.** Both runs use the same steps, the same regularisation, the
  same calibration selection, and the same walk-forward code path.
* **Same seed, no in-sample peeking.** Every number is from the test window of a
  model fitted only on games before it.

A "no improvement" verdict is a result, not a failure. The repository already
keeps one of those on purpose — the GBDT ensemble in MODELING_PLAN.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.backtest.metrics import Metrics, evaluate
from app.backtest.walkforward import Step, collect_predictions, make_steps, run_walk_forward
from app.core.logging import get_logger
from app.features.asof import AsOfStore
from app.modeling.dataset import Dataset, build_dataset

log = get_logger(__name__)

# Δ log loss below which a difference is not worth acting on even if it is
# statistically distinguishable from zero. The same band the ablation uses; it
# is a practical-significance floor, not the significance test itself.
NOISE_BAND = 0.0015

# Resamples for the paired bootstrap. Both models predict the same games, so the
# per-game differences are paired and their spread can be measured directly
# rather than assumed — which is what makes "beyond the noise band" a claim
# about this sample instead of about a constant someone chose once.
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20240401


@dataclass(frozen=True, slots=True)
class SetResult:
    version: str
    n_features: int
    metrics: dict[str, Any]
    coverage: int


@dataclass(frozen=True, slots=True)
class Comparison:
    baseline: SetResult
    candidate: SetResult
    n_games: int
    n_common_games: int
    delta_log_loss: float | None
    delta_brier: float | None
    delta_ece: float | None
    delta_accuracy: float | None
    # Paired 95% intervals for the three metrics the gate is decided on.
    log_loss_interval: PairedDelta
    brier_interval: PairedDelta
    calibration_interval: PairedDelta
    verdict: str
    reading: str
    # How often the candidate group actually had a value to contribute. A
    # verdict on a group that was missing for most games is a verdict about
    # coverage, not about the group.
    candidate_coverage: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_games": self.n_games,
            "n_common_games": self.n_common_games,
            "baseline": {
                "feature_set": self.baseline.version,
                "n_features": self.baseline.n_features,
                **_headline(self.baseline.metrics),
            },
            "candidate": {
                "feature_set": self.candidate.version,
                "n_features": self.candidate.n_features,
                **_headline(self.candidate.metrics),
            },
            "delta": {
                "log_loss": self.delta_log_loss,
                "brier": self.delta_brier,
                "calibration_error": self.delta_ece,
                "accuracy": self.delta_accuracy,
            },
            # Positive means the candidate is better. An interval spanning zero
            # means this sample cannot tell the two apart.
            "paired_95_ci": {
                "log_loss": self.log_loss_interval.to_dict(),
                "brier": self.brier_interval.to_dict(),
                "calibration_error": self.calibration_interval.to_dict(),
            },
            "candidate_coverage": self.candidate_coverage,
            "verdict": self.verdict,
            "reading": self.reading,
        }


HEADLINE = (
    "n", "log_loss", "brier_score", "calibration_error", "max_calibration_error",
    "accuracy", "roc_auc", "log_loss_improvement", "mean_predicted", "observed_rate",
)


def _headline(metrics: dict[str, Any]) -> dict[str, Any]:
    """Drop the per-bin calibration arrays; the headline numbers are the point."""
    return {k: metrics.get(k) for k in HEADLINE}


def _metrics(frame: pd.DataFrame) -> Metrics:
    return evaluate(frame["actual"].to_numpy(), frame["prob"].to_numpy())


def _per_game_log_loss(actual: np.ndarray, prob: np.ndarray) -> np.ndarray:
    p = np.clip(prob, 1e-12, 1 - 1e-12)
    return -(actual * np.log(p) + (1 - actual) * np.log(1 - p))


@dataclass(frozen=True, slots=True)
class PairedDelta:
    """A difference, with how sure we are that it is not zero."""

    mean: float
    ci_low: float
    ci_high: float

    @property
    def is_distinguishable_from_zero(self) -> bool:
        return self.ci_low > 0.0 or self.ci_high < 0.0

    @property
    def favours_candidate(self) -> bool:
        return self.ci_low > 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "mean": round(self.mean, 6),
            "ci_low": round(self.ci_low, 6),
            "ci_high": round(self.ci_high, 6),
        }


def _paired_bootstrap(deltas: np.ndarray) -> PairedDelta:
    """95% percentile bootstrap CI for a mean of paired per-game differences."""
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    n = len(deltas)
    if n == 0:
        return PairedDelta(0.0, 0.0, 0.0)
    draws = rng.integers(0, n, size=(BOOTSTRAP_RESAMPLES, n))
    means = deltas[draws].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return PairedDelta(float(deltas.mean()), float(low), float(high))


def _bootstrap_calibration_delta(
    actual: np.ndarray, base_prob: np.ndarray, cand_prob: np.ndarray
) -> PairedDelta:
    """Calibration error does not decompose per game, so resample whole slates.

    The same resampled games go to both models on every draw, which keeps the
    comparison paired even though the statistic is not additive.
    """
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    n = len(actual)
    if n == 0:
        return PairedDelta(0.0, 0.0, 0.0)
    deltas = np.empty(BOOTSTRAP_RESAMPLES)
    for i in range(BOOTSTRAP_RESAMPLES):
        idx = rng.integers(0, n, size=n)
        base = evaluate(actual[idx], base_prob[idx]).calibration_error
        cand = evaluate(actual[idx], cand_prob[idx]).calibration_error
        deltas[i] = (base - cand) if base is not None and cand is not None else np.nan
    finite = deltas[np.isfinite(deltas)]
    if finite.size == 0:
        return PairedDelta(0.0, 0.0, 0.0)
    low, high = np.percentile(finite, [2.5, 97.5])
    return PairedDelta(float(finite.mean()), float(low), float(high))


def _coverage(dataset: Dataset, extra: list[str]) -> dict[str, float]:
    """Share of labelled rows on which each candidate feature had a value."""
    rows = dataset.labelled
    if rows.empty:
        return {}
    return {
        name: round(float(rows[name].notna().mean()), 4)
        for name in extra
        if name in rows.columns
    }


def compare_feature_sets(
    session: Session,
    baseline_version: str = "fs_v1",
    candidate_version: str = "fs_v2",
    seasons: list[int] | None = None,
    start: date | None = None,
    end: date | None = None,
    step_days: int = 30,
    C: float = 0.001,
    store: AsOfStore | None = None,
) -> Comparison | None:
    """Fit both feature sets walk-forward and score them on the same games."""
    store = store or AsOfStore.load(session, seasons)

    baseline = build_dataset(
        session, seasons=seasons, store=store, feature_set_version=baseline_version
    )
    candidate = build_dataset(
        session, seasons=seasons, store=store, feature_set_version=candidate_version
    )
    if baseline.labelled.empty or candidate.labelled.empty:
        log.warning("compare.no_labelled_games")
        return None

    steps: list[Step] = make_steps(
        baseline.labelled, start=start, end=end, step_days=step_days
    )
    if not steps:
        log.warning("compare.no_steps")
        return None

    base_frame = collect_predictions(run_walk_forward(baseline, steps, C=C))
    cand_frame = collect_predictions(run_walk_forward(candidate, steps, C=C))
    if base_frame.empty or cand_frame.empty:
        log.warning("compare.no_predictions")
        return None

    common = sorted(set(base_frame["game_id"]) & set(cand_frame["game_id"]))
    if not common:
        log.warning("compare.no_common_games")
        return None
    base_common = base_frame[base_frame["game_id"].isin(common)].sort_values("game_id")
    cand_common = cand_frame[cand_frame["game_id"].isin(common)].sort_values("game_id")

    base_metrics, cand_metrics = _metrics(base_common), _metrics(cand_common)
    extra = [n for n in candidate.feature_names if n not in set(baseline.feature_names)]

    # Sorting both by game_id above makes these row-aligned, which is what turns
    # two independent evaluations into one paired comparison.
    actual = base_common["actual"].to_numpy()
    base_prob = base_common["prob"].to_numpy()
    cand_prob = cand_common["prob"].to_numpy()
    assert (cand_common["actual"].to_numpy() == actual).all()

    ll_interval = _paired_bootstrap(
        _per_game_log_loss(actual, base_prob) - _per_game_log_loss(actual, cand_prob)
    )
    brier_interval = _paired_bootstrap(
        (base_prob - actual) ** 2 - (cand_prob - actual) ** 2
    )
    ece_interval = _bootstrap_calibration_delta(actual, base_prob, cand_prob)

    # Positive delta = the candidate is better. Log loss, Brier and calibration
    # error are all lower-is-better, so the sign is flipped for those.
    delta_ll = base_metrics.log_loss - cand_metrics.log_loss
    delta_brier = base_metrics.brier_score - cand_metrics.brier_score
    delta_acc = cand_metrics.accuracy - base_metrics.accuracy
    delta_ece = (
        base_metrics.calibration_error - cand_metrics.calibration_error
        if base_metrics.calibration_error is not None
        and cand_metrics.calibration_error is not None
        else None
    )

    verdict, reading = _judge(ll_interval, brier_interval, ece_interval)
    comparison = Comparison(
        baseline=SetResult(
            baseline_version, len(baseline.feature_names), base_metrics.to_dict(),
            len(base_frame),
        ),
        candidate=SetResult(
            candidate_version, len(candidate.feature_names), cand_metrics.to_dict(),
            len(cand_frame),
        ),
        n_games=len(cand_frame),
        n_common_games=len(common),
        delta_log_loss=round(delta_ll, 6),
        delta_brier=round(delta_brier, 6),
        delta_ece=None if delta_ece is None else round(delta_ece, 6),
        delta_accuracy=round(delta_acc, 6),
        log_loss_interval=ll_interval,
        brier_interval=brier_interval,
        calibration_interval=ece_interval,
        verdict=verdict,
        reading=reading,
        candidate_coverage=_coverage(candidate, extra),
    )
    log.info(
        "compare.done",
        baseline=baseline_version,
        candidate=candidate_version,
        n_games=len(common),
        delta_log_loss=comparison.delta_log_loss,
        verdict=verdict,
    )
    return comparison


def _judge(
    log_loss: PairedDelta, brier: PairedDelta, calibration: PairedDelta
) -> tuple[str, str]:
    """Apply the gate in BACKTEST_PLAN.md § Phase 2A, on measured intervals.

    Two rules, in order.

    **Log loss has a veto.** If it is distinguishably worse, nothing else saves
    the group. A proper scoring rule getting worse means the probabilities got
    worse, and that is the number this system is graded on.

    **Reliability wins when the two disagree.** That is the repository's own
    rule, not a convenience: a group that leaves log loss where it was but makes
    the stated probabilities closer to the observed frequencies has improved the
    product, because the probability is the product. FEATURE_DICTIONARY.md keeps
    a group only if it improves log loss, Brier score *or* calibration, and this
    is the "or".

    Every judgement is on a paired 95% bootstrap interval rather than a fixed
    band, so "beyond noise" is a statement about this sample. NOISE_BAND then
    acts as a practical-significance floor on top: distinguishable from zero and
    too small to matter is still not a reason to change the model.
    """
    if log_loss.is_distinguishable_from_zero and not log_loss.favours_candidate:
        return "REJECT", (
            "Log loss is worse, and the paired interval excludes zero. The group "
            "is not carrying information the existing features lack, and adding "
            "it costs probability quality. A measured no is a result."
        )

    if log_loss.favours_candidate and log_loss.mean > NOISE_BAND:
        if calibration.is_distinguishable_from_zero and not calibration.favours_candidate:
            return "ADOPT_WITH_CAVEAT", (
                "Log loss improves, but calibration is measurably worse. "
                "Reliability is what the reader acts on, so this is worth "
                "adopting only with the calibration curve watched."
            )
        return "ADOPT", (
            "Log loss improves beyond both the noise floor and the paired "
            "interval. The group earns its place."
        )

    if calibration.favours_candidate and calibration.mean > NOISE_BAND:
        if brier.is_distinguishable_from_zero and not brier.favours_candidate:
            return "NO_EFFECT", (
                "Calibration improves but Brier score is measurably worse, which "
                "is the two halves of the same score pulling apart. Not enough "
                "to change the model on."
            )
        return "ADOPT_ON_CALIBRATION", (
            "Log loss is within noise, but calibration error improves and the "
            "paired interval excludes zero. BACKTEST_PLAN.md § Phase 2A is "
            "explicit that reliability wins when the two disagree: the stated "
            "probabilities are closer to the observed frequencies, and the "
            "probability is the product."
        )

    return "NO_EFFECT", (
        "No metric moves distinguishably beyond noise for a sample this size. "
        "The group neither helps nor hurts measurably, so it does not enter the "
        "model — a feature has to earn its place, not merely fail to hurt."
    )


__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "NOISE_BAND",
    "Comparison",
    "PairedDelta",
    "SetResult",
    "compare_feature_sets",
]
