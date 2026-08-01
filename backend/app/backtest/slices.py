"""Backtest slicing.

Every slice is computed from the row-level walk-forward output, so adding a
slice never requires re-running the walk-forward (BACKTEST_PLAN.md §2, §4).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.backtest.metrics import Metrics, evaluate

PROBABILITY_BANDS = [
    (0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70),
    (0.70, 0.75), (0.75, 0.80), (0.80, 1.01),
]


@dataclass(frozen=True, slots=True)
class Slice:
    slice_type: str
    slice_key: str
    metrics: Metrics
    extra: dict


def _favorite_view(frame: pd.DataFrame) -> pd.DataFrame:
    """Recast every row from the favorite's perspective.

    A 40% home prediction is a 60% away favorite; the probability band question
    is about the favorite, not about the home team.
    """
    home_favored = frame["prob"] >= 0.5
    return pd.DataFrame(
        {
            "favorite_prob": np.where(home_favored, frame["prob"], 1 - frame["prob"]),
            "favorite_won": np.where(
                home_favored, frame["actual"], 1 - frame["actual"]
            ).astype(int),
            "home_favored": home_favored.to_numpy(),
        }
    )


def compute_slices(frame: pd.DataFrame) -> list[Slice]:
    if frame.empty:
        return []

    y = frame["actual"].to_numpy()
    p = frame["prob"].to_numpy()
    out: list[Slice] = [
        Slice("overall", "all", evaluate(y, p), {
            "start_date": str(frame["official_date"].min()),
            "end_date": str(frame["official_date"].max()),
        })
    ]

    for season, group in frame.groupby("season"):
        out.append(Slice("season", str(int(season)),
                         evaluate(group["actual"], group["prob"]), {}))

    for month, group in frame.groupby("month"):
        out.append(Slice("month", f"{int(month):02d}",
                         evaluate(group["actual"], group["prob"]), {}))

    favorite = _favorite_view(frame)
    for lower, upper in PROBABILITY_BANDS:
        mask = (favorite["favorite_prob"] >= lower) & (favorite["favorite_prob"] < upper)
        if not mask.any():
            continue
        key = f"{int(lower * 100)}-{min(int(upper * 100), 100)}"
        out.append(
            Slice(
                "probability_band",
                key,
                evaluate(favorite.loc[mask, "favorite_won"],
                         favorite.loc[mask, "favorite_prob"]),
                {
                    "n": int(mask.sum()),
                    "mean_predicted": float(favorite.loc[mask, "favorite_prob"].mean()),
                    "observed": float(favorite.loc[mask, "favorite_won"].mean()),
                },
            )
        )

    # Favorite vs underdog, from the home team's perspective so the metric
    # remains a proper score on the same target.
    out.append(Slice("favorite_underdog", "home_favorite",
                     evaluate(y[p >= 0.5], p[p >= 0.5]), {}))
    out.append(Slice("favorite_underdog", "home_underdog",
                     evaluate(y[p < 0.5], p[p < 0.5]), {}))

    out.append(Slice("home_away", "home_favored",
                     evaluate(y[p >= 0.5], p[p >= 0.5]), {}))
    out.append(Slice("home_away", "away_favored",
                     evaluate(y[p < 0.5], p[p < 0.5]), {}))

    # Starter quality quartiles (lower FIP index = better starter present).
    quality = frame["starter_quality_index"].astype(float)
    if quality.notna().sum() >= 100:
        labels = ["q1_best", "q2", "q3", "q4_worst"]
        try:
            buckets = pd.qcut(quality, 4, labels=labels)
        except ValueError:
            buckets = None
        if buckets is not None:
            for key, group in frame.groupby(buckets, observed=True):
                out.append(Slice("starter_quality", str(key),
                                 evaluate(group["actual"], group["prob"]), {}))

    for confirmed, group in frame.groupby(frame["lineup_confirmed"].astype(bool)):
        out.append(
            Slice(
                "lineup_confirmed",
                "confirmed" if confirmed else "unconfirmed",
                evaluate(group["actual"], group["prob"]),
                {
                    "note": (
                        None
                        if confirmed
                        else "Pregame lineup confirmation requires the Phase 2 lineup "
                             "poller; all historical rows are honestly unconfirmed."
                    )
                },
            )
        )

    both_starters = frame["home_starter_known"].astype(bool) & frame[
        "away_starter_known"
    ].astype(bool)
    for known, group in frame.groupby(both_starters):
        out.append(
            Slice(
                "starters_known",
                "both_known" if known else "at_least_one_unknown",
                evaluate(group["actual"], group["prob"]),
                {},
            )
        )
    return out
