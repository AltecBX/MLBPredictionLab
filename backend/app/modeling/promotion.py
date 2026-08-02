"""Whether a freshly trained model should replace the one being served.

`train_model` registers a version and activates it. That is correct for the
first model and wrong for every one after it: a nightly refit that always
activates means a configuration that got *worse* by chance becomes what the
product serves, and nothing ever notices.

The gate distinguishes two cases that look identical in the metrics and are not
the same event.

**A refresh.** Same regularisation, same feature set, more data. There is no
challenger here — it is the incumbent configuration refitted on games that have
since been played, and withholding it would freeze the model at whatever day it
was first trained. Activated without argument.

**A challenger.** The walk-forward search picked a different `C`, or the feature
set changed. That is a different model, and it has to earn the swap on paired
out-of-sample evidence against the configuration it would replace.

The second case is not hypothetical caution. MODELING_PLAN.md § Individual
bullpen availability measured the same three features at C=0.01 and C=0.03 on
one season and got ADOPT from one and nothing from the other — a verdict that
moved with the regularisation constant alone. `C` is selected by a walk-forward
search that is itself noisy, so a nightly job that quietly re-selects it is
quietly changing the model, and this is the check that makes that visible.

**Both arms are scored on the same games, paired.** Comparing a candidate's
walk-forward metrics against the incumbent's *registered* metrics would compare
two numbers computed over different games, different windows and different
amounts of history, and the difference between them would be mostly calendar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from app.backtest.walkforward import Step, collect_predictions, run_walk_forward

if TYPE_CHECKING:  # `feature_set_compare` imports `train`, which imports this.
    from app.backtest.feature_set_compare import PairedDelta
from app.core.logging import get_logger
from app.db.models import ModelVersion
from app.modeling.dataset import Dataset

log = get_logger(__name__)

#: Verdicts. `REFRESH` is an activation; `HOLD` and `REJECT` are not.
REFRESH = "REFRESH"
PROMOTE = "PROMOTE"
HOLD = "HOLD"
REJECT = "REJECT"
NO_INCUMBENT = "NO_INCUMBENT"


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Whether to activate, and the evidence either way."""

    verdict: str
    should_activate: bool
    reason: str
    incumbent_version: str | None = None
    incumbent_C: float | None = None
    candidate_C: float | None = None
    incumbent_feature_set: str | None = None
    candidate_feature_set: str | None = None
    n_common_games: int = 0
    incumbent_log_loss: float | None = None
    candidate_log_loss: float | None = None
    delta: PairedDelta | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "should_activate": self.should_activate,
            "reason": self.reason,
            "incumbent": {
                "version": self.incumbent_version,
                "C": self.incumbent_C,
                "feature_set": self.incumbent_feature_set,
                "log_loss": self.incumbent_log_loss,
            },
            "candidate": {
                "C": self.candidate_C,
                "feature_set": self.candidate_feature_set,
                "log_loss": self.candidate_log_loss,
            },
            "n_common_games": self.n_common_games,
            "paired_95_ci": None if self.delta is None else self.delta.to_dict(),
        }


def _config(version: ModelVersion) -> tuple[float | None, str | None]:
    hyper = version.hyperparameters or {}
    C = hyper.get("C")
    return (None if C is None else float(C)), version.feature_set_version


def decide_promotion(
    dataset: Dataset,
    steps: list[Step],
    candidate_C: float,
    incumbent: ModelVersion | None,
    min_train_rows: int | None = None,
) -> PromotionDecision:
    """Should the candidate replace ``incumbent``?

    Runs a walk-forward for each configuration over the same steps, so the two
    are scored on identical games and can be paired. A configuration that is
    unchanged skips the comparison entirely — there is nothing to compare.
    """
    if incumbent is None:
        return PromotionDecision(
            verdict=NO_INCUMBENT,
            should_activate=True,
            reason="No model is currently active, so this one is served.",
            candidate_C=candidate_C,
            candidate_feature_set=dataset.feature_set_version,
        )

    incumbent_C, incumbent_features = _config(incumbent)
    candidate_features = dataset.feature_set_version
    unchanged = (
        incumbent_C is not None
        and abs(incumbent_C - candidate_C) < 1e-12
        and incumbent_features == candidate_features
    )
    if unchanged:
        return PromotionDecision(
            verdict=REFRESH,
            should_activate=True,
            reason=(
                "Same regularisation and same feature set as the active model — "
                "this is the incumbent refitted on newer games, not a challenger."
            ),
            incumbent_version=incumbent.version,
            incumbent_C=incumbent_C,
            candidate_C=candidate_C,
            incumbent_feature_set=incumbent_features,
            candidate_feature_set=candidate_features,
        )

    if incumbent_C is None:
        return PromotionDecision(
            verdict=HOLD,
            should_activate=False,
            reason=(
                "The active model records no regularisation, so the two cannot be "
                "scored on the same footing. Activate deliberately if intended."
            ),
            incumbent_version=incumbent.version,
            candidate_C=candidate_C,
            incumbent_feature_set=incumbent_features,
            candidate_feature_set=candidate_features,
        )

    # Imported here rather than at module scope: `feature_set_compare` pulls in
    # `train`, and `train` is what calls this. A local import is the smaller
    # price than splitting the bootstrap helpers into a third module.
    from app.backtest.feature_set_compare import _paired_bootstrap, _per_game_log_loss

    candidate = collect_predictions(
        run_walk_forward(dataset, steps, C=candidate_C, min_train_rows=min_train_rows)
    )
    baseline = collect_predictions(
        run_walk_forward(dataset, steps, C=incumbent_C, min_train_rows=min_train_rows)
    )
    if candidate.empty or baseline.empty:
        return PromotionDecision(
            verdict=HOLD,
            should_activate=False,
            reason="The walk-forward produced no comparable games.",
            incumbent_version=incumbent.version,
            incumbent_C=incumbent_C,
            candidate_C=candidate_C,
            incumbent_feature_set=incumbent_features,
            candidate_feature_set=candidate_features,
        )

    merged = baseline.merge(
        candidate[["game_id", "prob"]], on="game_id", how="inner",
        suffixes=("_base", "_cand"), validate="one_to_one",
    )
    if merged.empty:
        return PromotionDecision(
            verdict=HOLD,
            should_activate=False,
            reason="The two configurations share no scored games.",
            incumbent_version=incumbent.version,
            incumbent_C=incumbent_C,
            candidate_C=candidate_C,
            incumbent_feature_set=incumbent_features,
            candidate_feature_set=candidate_features,
        )

    actual = merged["actual"].to_numpy()
    base_loss = _per_game_log_loss(actual, merged["prob_base"].to_numpy(dtype=float))
    cand_loss = _per_game_log_loss(actual, merged["prob_cand"].to_numpy(dtype=float))
    delta = _paired_bootstrap(base_loss - cand_loss)

    common: dict[str, Any] = {
        "incumbent_version": incumbent.version,
        "incumbent_C": incumbent_C,
        "candidate_C": candidate_C,
        "incumbent_feature_set": incumbent_features,
        "candidate_feature_set": candidate_features,
        "n_common_games": int(len(merged)),
        "incumbent_log_loss": float(np.mean(base_loss)),
        "candidate_log_loss": float(np.mean(cand_loss)),
        "delta": delta,
    }

    if not delta.is_distinguishable_from_zero:
        return PromotionDecision(
            verdict=HOLD,
            should_activate=False,
            reason=(
                "The configuration changed but the difference is inside the noise "
                "band, so the incumbent stays. A tie is not a reason to swap the "
                "served model."
            ),
            **common,
        )
    if delta.favours_candidate:
        return PromotionDecision(
            verdict=PROMOTE,
            should_activate=True,
            reason=(
                "The new configuration beats the active one out of sample on the "
                "same games, with an interval excluding zero."
            ),
            **common,
        )
    return PromotionDecision(
        verdict=REJECT,
        should_activate=False,
        reason=(
            "The new configuration is measurably worse than the active one. It is "
            "registered for the record and not served."
        ),
        **common,
    )


__all__ = [
    "HOLD",
    "NO_INCUMBENT",
    "PROMOTE",
    "REFRESH",
    "REJECT",
    "PromotionDecision",
    "decide_promotion",
]
