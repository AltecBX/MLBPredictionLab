"""Ablation suite and sanity gates.

For each feature group, the entire walk-forward is refit with that group
removed and out-of-sample log loss is compared to the full model
(BACKTEST_PLAN.md §6). A group that does not improve walk-forward log loss is
a candidate for removal — that is a measured decision, not a preference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.backtest.metrics import evaluate
from app.backtest.walkforward import Step, collect_predictions, run_walk_forward
from app.core.logging import get_logger
from app.modeling.dataset import Dataset

log = get_logger(__name__)

# Δ log loss beyond which a group is judged to matter. Below this the
# difference is inside run-to-run noise for a season-scale sample.
NOISE_BAND = 0.0015

FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "starting_pitcher": ("sp_",),
    "bullpen": ("bp_",),
    "recent_form": ("_w30_", "_w14_", "off_form_delta"),
    "head_to_head": ("h2h_",),
    "travel_rest": ("sched_",),
    "team_strength": ("elo_", "team_"),
    "defense": ("def_",),
    "environment": ("env_is_dome", "env_venue_elevation"),
    "offense": ("off_",),
}

# Groups whose providers are not enabled in Phase 1. Reported as untestable
# rather than silently omitted from the table.
UNAVAILABLE_GROUPS = {
    "expected_lineups": "Requires the Phase 2 pregame lineup poller.",
    "weather": "Requires a configured weather provider (WEATHER_PROVIDER).",
    "park_factors": "Requires the Phase 2 park-factor regression.",
    "batter_vs_pitcher": "Requires Phase 3 play-by-play ingestion.",
    "market_odds": "Requires a licensed odds provider (ODDS_PROVIDER).",
}


@dataclass(frozen=True, slots=True)
class AblationRow:
    group: str
    n_features_removed: int
    n_games: int | None
    log_loss: float | None
    delta_log_loss: float | None
    delta_brier: float | None
    delta_calibration_error: float | None
    delta_roc_auc: float | None
    verdict: str
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "n_features_removed": self.n_features_removed,
            "n_games": self.n_games,
            "log_loss": self.log_loss,
            "delta_log_loss": self.delta_log_loss,
            "delta_brier": self.delta_brier,
            "delta_calibration_error": self.delta_calibration_error,
            "delta_roc_auc": self.delta_roc_auc,
            "verdict": self.verdict,
            "note": self.note,
        }


def group_members(group: str, feature_names: list[str]) -> list[str]:
    prefixes = FEATURE_GROUPS.get(group, ())
    return [name for name in feature_names if any(p in name for p in prefixes)]


def run_ablation(
    dataset: Dataset,
    steps: list[Step],
    C: float,
    baseline_frame: pd.DataFrame,
    min_train_rows: int | None = None,
) -> list[AblationRow]:
    """Refit the walk-forward once per group with that group's features removed.

    ``min_train_rows`` must match the value used for the baseline run, and every
    comparison is restricted to the games both runs predicted. Otherwise a group
    whose removal shifts step coverage would be scored on a different game set
    than the baseline, which is not a comparison at all.
    """
    rows: list[AblationRow] = []

    for group in FEATURE_GROUPS:
        members = group_members(group, dataset.feature_names)
        if not members:
            continue
        remaining = [n for n in dataset.feature_names if n not in members]
        if len(remaining) < 3:
            rows.append(
                AblationRow(group, len(members), None, None, None, None, None, None,
                            "UNTESTABLE", "Too few features remain to fit a model.")
            )
            continue

        results = run_walk_forward(
            dataset, steps, C=C, feature_names=remaining, min_train_rows=min_train_rows
        )
        frame = collect_predictions(results)
        if frame.empty:
            rows.append(
                AblationRow(group, len(members), 0, None, None, None, None, None,
                            "UNTESTABLE", "Ablated run produced no predictions.")
            )
            continue

        # Score both runs on the games they have in common.
        common = set(baseline_frame["game_id"]) & set(frame["game_id"])
        if len(common) < 30:
            rows.append(
                AblationRow(group, len(members), len(common), None, None, None, None, None,
                            "UNTESTABLE",
                            "Too few games shared with the baseline run to compare.")
            )
            continue
        aligned_baseline = baseline_frame[baseline_frame["game_id"].isin(common)]
        aligned = frame[frame["game_id"].isin(common)]
        baseline = evaluate(
            aligned_baseline["actual"].to_numpy(), aligned_baseline["prob"].to_numpy()
        )
        ablated = evaluate(aligned["actual"].to_numpy(), aligned["prob"].to_numpy())
        # Positive delta = removing the group made log loss worse = the group helps.
        delta_ll = (
            (ablated.log_loss - baseline.log_loss)
            if ablated.log_loss is not None and baseline.log_loss is not None
            else None
        )
        verdict = "UNTESTABLE"
        if delta_ll is not None:
            if delta_ll > NOISE_BAND:
                verdict = "IMPROVES"
            elif delta_ll < -NOISE_BAND:
                verdict = "HURTS"
            else:
                verdict = "NEUTRAL"

        rows.append(
            AblationRow(
                group=group,
                n_features_removed=len(members),
                n_games=ablated.n,
                log_loss=ablated.log_loss,
                delta_log_loss=delta_ll,
                delta_brier=_delta(ablated.brier_score, baseline.brier_score),
                delta_calibration_error=_delta(
                    ablated.calibration_error, baseline.calibration_error
                ),
                delta_roc_auc=_delta(ablated.roc_auc, baseline.roc_auc),
                verdict=verdict,
            )
        )
        log.info("ablation.group", group=group, delta_log_loss=delta_ll, verdict=verdict)

    for group, reason in UNAVAILABLE_GROUPS.items():
        rows.append(
            AblationRow(group, 0, None, None, None, None, None, None, "UNAVAILABLE", reason)
        )
    return rows


def _delta(ablated: float | None, baseline: float | None) -> float | None:
    if ablated is None or baseline is None:
        return None
    return ablated - baseline


# --- Sanity gates (BACKTEST_PLAN.md §8) ------------------------------------

GATES = {
    "accuracy_too_high": 0.62,
    "log_loss_too_low": 0.62,
    "roc_auc_too_high": 0.70,
    "dominant_feature_share": 0.40,
    "extreme_band_share": 0.02,
}


def sanity_flags(
    frame: pd.DataFrame, metrics: Any, dominant_share: float | None = None
) -> list[dict[str, Any]]:
    """Tripwires for results that are too good to be true, or broken."""
    flags: list[dict[str, Any]] = []
    if frame.empty or metrics.n < 300:
        return flags

    if metrics.accuracy is not None and metrics.accuracy > GATES["accuracy_too_high"]:
        flags.append({
            "code": "SUSPECTED_LEAKAGE",
            "gate": "accuracy",
            "value": metrics.accuracy,
            "threshold": GATES["accuracy_too_high"],
            "detail": "Season-scale accuracy above 62% exceeds what the closing "
                      "line achieves. Investigate before trusting this run.",
        })
    if metrics.log_loss is not None and metrics.log_loss < GATES["log_loss_too_low"]:
        flags.append({
            "code": "SUSPECTED_LEAKAGE",
            "gate": "log_loss",
            "value": metrics.log_loss,
            "threshold": GATES["log_loss_too_low"],
            "detail": "Log loss below 0.62 is better than the market's closing line.",
        })
    if metrics.roc_auc is not None and metrics.roc_auc > GATES["roc_auc_too_high"]:
        flags.append({
            "code": "SUSPECTED_LEAKAGE",
            "gate": "roc_auc",
            "value": metrics.roc_auc,
            "threshold": GATES["roc_auc_too_high"],
            "detail": "Discrimination above 0.70 AUC is implausible for MLB game outcomes.",
        })
    if dominant_share is not None and dominant_share > GATES["dominant_feature_share"]:
        flags.append({
            "code": "SUSPECTED_LEAKAGE",
            "gate": "dominant_feature_share",
            "value": dominant_share,
            "threshold": GATES["dominant_feature_share"],
            "detail": "A single feature carries most of the model's weight.",
        })

    extreme = ((frame["prob"] < 0.05) | (frame["prob"] > 0.95)).mean()
    if extreme > GATES["extreme_band_share"]:
        flags.append({
            "code": "SUSPECTED_LEAKAGE",
            "gate": "extreme_band_share",
            "value": float(extreme),
            "threshold": GATES["extreme_band_share"],
            "detail": "Too many predictions sit in the 0-5% / 95-100% bands.",
        })

    if metrics.log_loss is not None and metrics.log_loss > 0.6931:
        flags.append({
            "code": "UNDERPERFORMING",
            "gate": "log_loss",
            "value": metrics.log_loss,
            "threshold": 0.6931,
            "detail": "Worse than always predicting 50%, which points at a broken "
                      "feature or a label alignment bug.",
        })
    return flags
