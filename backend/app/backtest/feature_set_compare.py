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

import pandas as pd
from sqlalchemy.orm import Session

from app.backtest.metrics import Metrics, evaluate
from app.backtest.walkforward import Step, collect_predictions, make_steps, run_walk_forward
from app.core.logging import get_logger
from app.features.asof import AsOfStore
from app.modeling.dataset import Dataset, build_dataset

log = get_logger(__name__)

# Δ log loss beyond which the difference is treated as real rather than as
# run-to-run noise on a season-scale sample. Same band the ablation uses.
NOISE_BAND = 0.0015


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

    verdict, reading = _judge(delta_ll, delta_brier, delta_ece)
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
    delta_ll: float, delta_brier: float, delta_ece: float | None
) -> tuple[str, str]:
    """Verdict on the primary metric, with the others as corroboration.

    Log loss decides. It is the proper scoring rule this system is graded on,
    and it punishes confident errors the way a reader of these predictions
    would. Brier and calibration are reported alongside so that an improvement
    that comes entirely from one metric while damaging another is visible rather
    than buried.
    """
    supporting = [d for d in (delta_brier, delta_ece) if d is not None]
    agreeing = sum(1 for d in supporting if d > 0)

    if delta_ll > NOISE_BAND:
        if agreeing == len(supporting):
            return "ADOPT", (
                "Log loss improves beyond the noise band and every other metric "
                "agrees. The group earns its place."
            )
        return "ADOPT_WITH_CAVEAT", (
            "Log loss improves beyond the noise band, but at least one of Brier "
            "score and calibration error moved the wrong way. Worth adopting, "
            "worth watching."
        )
    if delta_ll < -NOISE_BAND:
        return "REJECT", (
            "Log loss is worse beyond the noise band. The group is not carrying "
            "information the existing features do not already have, and adding "
            "it costs accuracy. A measured no is a result."
        )
    return "NO_EFFECT", (
        "The difference is inside the noise band for a sample this size. The "
        "group neither helps nor hurts measurably, so it does not enter the "
        "model — a feature has to earn its place, not merely fail to hurt."
    )


__all__ = ["Comparison", "NOISE_BAND", "SetResult", "compare_feature_sets"]
