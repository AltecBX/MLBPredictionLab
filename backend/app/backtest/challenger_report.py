"""Scoring and verdicts for the challenger comparison.

Every model is scored on the identical out-of-sample games; the stack is
additionally scored on the subset where it had enough prior history to emit a
prediction, with every other model re-scored on that same subset so the
head-to-head stays paired. The promotion rule is the repository's standing
one, spelled out as checks a machine can answer:

  1. total walk-forward log loss improves, with the paired interval excluding
     zero;
  2. Brier improves or holds;
  3. calibration error is not distinguishably worse;
  4. the log-loss improvement holds its sign in every season with a full
     sample, not only one;
  5. nothing rests on a single unstable component (for the stack: the
     coefficient trajectory; for a single model: its fold-to-fold
     hyperparameter churn is reported for the same reading).

Failing any check is a NO — the served model stays.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.backtest.feature_set_compare import (
    PairedDelta,
    _bootstrap_calibration_delta,
    _paired_bootstrap,
    _per_game_log_loss,
)
from app.backtest.metrics import evaluate
from app.backtest.slices import PROBABILITY_BANDS
from app.modeling.challenger import MARKET_MAX_PROB


def _favorite_bands(y: np.ndarray, p: np.ndarray) -> list[dict[str, Any]]:
    home_favored = p >= 0.5
    fav_p = np.where(home_favored, p, 1 - p)
    fav_won = np.where(home_favored, y, 1 - y).astype(int)
    out: list[dict[str, Any]] = []
    for lower, upper in PROBABILITY_BANDS:
        mask = (fav_p >= lower) & (fav_p < upper)
        n = int(mask.sum())
        if n == 0:
            continue
        out.append(
            {
                "band": f"{int(lower * 100)}-{min(int(upper * 100), 100)}",
                "n": n,
                "mean_predicted": float(fav_p[mask].mean()),
                "observed": float(fav_won[mask].mean()),
            }
        )
    return out


def _metrics_dict(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    m = evaluate(y, p)
    return {
        "n": m.n,
        "log_loss": m.log_loss,
        "brier": m.brier_score,
        "calibration_error": m.calibration_error,
        "accuracy": m.accuracy,
        "roc_auc": m.roc_auc,
    }


def score_model(frame: pd.DataFrame, prob_column: str) -> dict[str, Any]:
    """Everything the report needs about one model's OOF stream."""
    y = frame["actual"].to_numpy()
    p = frame[prob_column].to_numpy(dtype=float)

    by_season: dict[str, Any] = {}
    for season, group in frame.groupby("season"):
        by_season[str(int(season))] = _metrics_dict(
            group["actual"].to_numpy(), group[prob_column].to_numpy(dtype=float)
        )

    fav_p = np.where(p >= 0.5, p, 1 - p)
    starters_known = (
        frame["home_starter_known"].astype(bool)
        & frame["away_starter_known"].astype(bool)
    ).to_numpy()

    # Confirmed-lineup split. Every historical row in this database is honestly
    # pre-lineup — the boxscore-derived lineups are knowable only after the
    # game, and the pregame poller's archive starts from its first run — so
    # the "after confirmed lineups" arm is expected to be empty and is
    # reported as such rather than faked from postgame data.
    confirmed = frame["lineup_confirmed"].astype(bool).to_numpy()

    return {
        "overall": _metrics_dict(y, p),
        "by_season": by_season,
        "bands": _favorite_bands(y, p),
        "max_probability": float(fav_p.max()) if len(fav_p) else None,
        "overconfidence_rate": float((fav_p > MARKET_MAX_PROB).mean()) if len(fav_p) else None,
        "before_confirmed_lineups": _metrics_dict(y[~confirmed], p[~confirmed]),
        "after_confirmed_lineups": _metrics_dict(y[confirmed], p[confirmed]),
        "starters_both_known": _metrics_dict(y[starters_known], p[starters_known]),
        "starter_missing": _metrics_dict(y[~starters_known], p[~starters_known]),
    }


def paired_against_baseline(
    frame: pd.DataFrame, baseline_column: str, prob_column: str
) -> dict[str, Any]:
    """Paired per-game deltas, positive when the challenger is better."""
    y = frame["actual"].to_numpy()
    base = frame[baseline_column].to_numpy(dtype=float)
    cand = frame[prob_column].to_numpy(dtype=float)

    dll = _paired_bootstrap(_per_game_log_loss(y, base) - _per_game_log_loss(y, cand))
    dbrier = _paired_bootstrap((base - y) ** 2 - (cand - y) ** 2)
    dcal = _bootstrap_calibration_delta(y, base, cand)

    by_season: dict[str, Any] = {}
    for season, group in frame.groupby("season"):
        gy = group["actual"].to_numpy()
        gb = group[baseline_column].to_numpy(dtype=float)
        gc = group[prob_column].to_numpy(dtype=float)
        by_season[str(int(season))] = _paired_bootstrap(
            _per_game_log_loss(gy, gb) - _per_game_log_loss(gy, gc)
        ).to_dict()

    return {
        "log_loss": dll.to_dict(),
        "brier": dbrier.to_dict(),
        "calibration_error": dcal.to_dict(),
        "log_loss_by_season": by_season,
        "_deltas": (dll, dbrier, dcal),
    }


def promotion_verdict(
    paired: dict[str, Any],
    full_seasons: list[str],
) -> dict[str, Any]:
    """Answer the promotion rule's checks; every check must pass."""
    dll: PairedDelta
    dbrier: PairedDelta
    dcal: PairedDelta
    dll, dbrier, dcal = paired["_deltas"]

    improves_log_loss = dll.favours_candidate
    brier_holds = dbrier.mean >= 0.0 or not dbrier.is_distinguishable_from_zero
    calibration_holds = dcal.mean >= 0.0 or not dcal.is_distinguishable_from_zero
    season_signs = {
        season: paired["log_loss_by_season"].get(season, {}).get("mean")
        for season in full_seasons
    }
    # An empty season list must fail the check, not pass it vacuously — a
    # window too short to contain a full season cannot demonstrate stability.
    multi_season = bool(season_signs) and all(
        v is not None and v > 0 for v in season_signs.values()
    )

    checks = {
        "improves_total_log_loss_ci_excludes_zero": improves_log_loss,
        "brier_improves_or_holds": brier_holds,
        "calibration_not_materially_worse": calibration_holds,
        "positive_in_every_full_season": multi_season,
    }
    return {
        "checks": checks,
        "season_log_loss_deltas": season_signs,
        "promote": all(checks.values()),
    }


def strip_private(report: Any) -> Any:
    """Remove the in-memory delta objects before JSON serialisation."""
    if isinstance(report, dict):
        return {k: strip_private(v) for k, v in report.items() if not k.startswith("_")}
    if isinstance(report, list):
        return [strip_private(v) for v in report]
    return report
