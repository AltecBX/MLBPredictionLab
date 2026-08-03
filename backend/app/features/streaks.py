"""Candidate streak features — measured, never assumed.

Raw streak length never enters the production model directly; what is offered
to the ablation gate is the *processed* form of the streak story:

  * the current streak, capped at five each way, so one outlier run cannot
    dominate a linear term;
  * the team's shrunk historical probability of *winning* the next game at
    exactly this streak length, shrunk toward the league rate at that length
    (both computed from strictly-earlier results);
  * the adjusted effect — that shrunk rate minus the pre-game expectation for
    those same historical games, which is the part of the streak story that
    is not already team strength;
  * the run differential and the opponent quality across the current streak's
    games, because a 4-game streak against contenders and one against
    rebuilding clubs are different objects.

Every lookup is by ``knowledge_time``: an occurrence enters the history the
moment its game's result became knowable, and the current streak is read the
same way. The index is built once per :class:`FeatureBuilder` from the same
games frame everything else uses, replaying the same Elo engine for the
expectation term.

The verdict on this group lives in MODELING_PLAN.md (§ streaks). The section
on the site is display and research either way; these features are in the
model only if the walk-forward gate says so.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from app.features.shrinkage import FeatureValue

WIN_CAP = 5.0
LOSS_CAP = 5.0
MIN_STREAK = 2
MAX_BUCKET = 10
SHRINKAGE_K = 10.0
# Below this many league occurrences the continuation features stay missing —
# early in the first recorded season there is no history to consult, and a
# missing value with an honest sample size beats a fabricated 0.5.
MIN_LEAGUE_OCCURRENCES = 30


@dataclass(slots=True)
class _Occurrences:
    """One (entity, sign, length) history, sorted by knowledge_time."""

    times: np.ndarray  # int64 ns
    won: np.ndarray  # cumulative wins, aligned with times
    expected: np.ndarray  # cumulative expectation, aligned with times

    def before(self, as_of: datetime) -> tuple[int, float, float]:
        idx = bisect_right(self.times, pd.Timestamp(as_of).value)
        if idx == 0:
            return 0, 0.0, 0.0
        return idx, float(self.won[idx - 1]), float(self.expected[idx - 1])


class StreakIndex:
    """Streak state and continuation history, queryable at any ``as_of``."""

    def __init__(self, games: pd.DataFrame) -> None:
        # The display service already reconstructs the per-team log with
        # pregame Elo expectations under the exact rules this feature needs
        # (completed regular-season games, per-season reset); reusing it keeps
        # one definition of a streak in the repository.
        from app.services.streaks import build_team_log

        log = build_team_log(games)
        self._by_team: dict[int, pd.DataFrame] = {}
        self._occ: dict[tuple[int | None, int, int], _Occurrences] = {}
        if log.empty:
            return

        log = log.sort_values(["team_id", "knowledge_time", "game_id"])
        for team_id, rows in log.groupby("team_id"):
            self._by_team[int(team_id)] = rows.reset_index(drop=True)

        occurrences = log[log["entering"].abs() >= MIN_STREAK].copy()
        occurrences["sign"] = np.sign(occurrences["entering"]).astype(int)
        occurrences["bucket"] = occurrences["entering"].abs().clip(upper=MAX_BUCKET)
        occurrences = occurrences.sort_values(["knowledge_time", "game_id"])

        def register(key: tuple[int | None, int, int], rows: pd.DataFrame) -> None:
            self._occ[key] = _Occurrences(
                times=rows["knowledge_time"].astype("int64").to_numpy(),
                won=rows["won"].astype(float).cumsum().to_numpy(),
                expected=rows["elo_expected"].astype(float).cumsum().to_numpy(),
            )

        for (sign, bucket), rows in occurrences.groupby(["sign", "bucket"]):
            register((None, int(sign), int(bucket)), rows)
        for (team, sign, bucket), rows in occurrences.groupby(
            ["team_id", "sign", "bucket"]
        ):
            register((int(team), int(sign), int(bucket)), rows)

    # -- current state ------------------------------------------------------

    def state(
        self, team_id: int, season: int, as_of: datetime
    ) -> tuple[int, pd.DataFrame]:
        """The signed streak entering the next game, and the games inside it."""
        rows = self._by_team.get(int(team_id))
        if rows is None:
            return 0, pd.DataFrame()
        cutoff = pd.Timestamp(as_of).value
        known = rows[
            (rows["knowledge_time"].astype("int64") <= cutoff)
            & (rows["season"] == season)
        ]
        if known.empty:
            return 0, known
        streak = int(known["streak_after"].iloc[-1])
        return streak, known.tail(abs(streak)) if streak else known.iloc[0:0]

    # -- the feature values -------------------------------------------------

    def side_values(
        self, team_id: int, season: int, as_of: datetime
    ) -> dict[str, FeatureValue]:
        streak, inside = self.state(team_id, season, as_of)
        values: dict[str, FeatureValue] = {
            "sk_win_streak": FeatureValue(min(float(max(streak, 0)), WIN_CAP), abs(streak), False),
            "sk_loss_streak": FeatureValue(min(float(max(-streak, 0)), LOSS_CAP), abs(streak), False),
        }

        if len(inside):
            values["sk_streak_run_diff"] = FeatureValue(
                float(inside["run_diff"].mean()), len(inside), False
            )
            values["sk_streak_opp_elo"] = FeatureValue(
                float(inside["opp_elo_pregame"].mean() - 1500.0), len(inside), False
            )
        else:
            # No active streak: zero is the true value of "no streak", not a
            # stand-in for unknown.
            values["sk_streak_run_diff"] = FeatureValue(0.0, 0, False)
            values["sk_streak_opp_elo"] = FeatureValue(0.0, 0, False)

        if abs(streak) < MIN_STREAK:
            missing = FeatureValue.missing("no active streak of two or more games")
            values["sk_continue_prob"] = missing
            values["sk_adjusted_effect"] = missing
            return values

        sign = 1 if streak > 0 else -1
        bucket = min(abs(streak), MAX_BUCKET)
        league = self._occ.get((None, sign, bucket))
        league_n, league_wins, league_expected = (
            league.before(as_of) if league else (0, 0.0, 0.0)
        )
        if league_n < MIN_LEAGUE_OCCURRENCES:
            missing = FeatureValue.missing(
                f"fewer than {MIN_LEAGUE_OCCURRENCES} league occurrences of this "
                "streak length on record yet"
            )
            values["sk_continue_prob"] = missing
            values["sk_adjusted_effect"] = missing
            return values

        league_rate = league_wins / league_n
        team = self._occ.get((int(team_id), sign, bucket))
        team_n, team_wins, team_expected = team.before(as_of) if team else (0, 0.0, 0.0)

        # Win-direction probability, so the sign convention matches every
        # other feature: higher favours this side, whatever the streak's sign.
        shrunk = (team_wins + SHRINKAGE_K * league_rate) / (team_n + SHRINKAGE_K)
        expected_mean = (
            team_expected / team_n if team_n > 0 else league_expected / league_n
        )
        values["sk_continue_prob"] = FeatureValue(shrunk, team_n, team_n < 10)
        values["sk_adjusted_effect"] = FeatureValue(
            shrunk - expected_mean, team_n, team_n < 10
        )
        return values


__all__ = ["StreakIndex"]
