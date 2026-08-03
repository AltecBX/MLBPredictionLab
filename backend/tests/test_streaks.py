"""The streak engine's arithmetic, pinned on hand-checkable schedules.

Everything here is a tiny synthetic league whose streaks can be verified by
reading the fixture. The properties that matter: the streak a team carries
*into* a game never includes that game, streaks reset at season boundaries,
reach counts count streaks rather than games, a losing streak "continues" by
losing, and small samples shrink toward the expectation instead of printing
drama.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from app.services.streaks import (
    MIN_OCCURRENCES,
    SHRINKAGE_K,
    _cell,
    _reach_counts,
    _venue_streak,
    build_team_log,
)


def _games(results: list[tuple[int, int]], season: int = 2025) -> pd.DataFrame:
    """A two-team league. ``results`` are (home_score, away_score) for team 1
    hosting team 2 on consecutive days."""
    rows = []
    start = datetime(season, 4, 1, 23, 0, tzinfo=UTC)
    for i, (home, away) in enumerate(results):
        moment = start + timedelta(days=i)
        rows.append(
            {
                "id": season * 1000 + i,
                "season": season,
                "game_type": "R",
                "official_date": moment.date(),
                "game_date_utc": moment,
                "home_team_id": 1,
                "away_team_id": 2,
                "home_score": home,
                "away_score": away,
                "home_win": home > away,
                "is_final": True,
                "home_probable_pitcher_id": None,
                "away_probable_pitcher_id": None,
            }
        )
    return pd.DataFrame(rows)


class TestStreakReconstruction:
    def test_entering_never_includes_the_game_itself(self):
        # Team 1: W W W L — entering must read 0, +1, +2, +3.
        log = build_team_log(_games([(5, 2), (4, 1), (3, 2), (0, 6)]))
        team1 = log[log["team_id"] == 1].sort_values("game_date_utc")
        assert team1["entering"].tolist() == [0, 1, 2, 3]
        assert team1["streak_after"].tolist() == [1, 2, 3, -1]
        # Team 2 is the mirror image.
        team2 = log[log["team_id"] == 2].sort_values("game_date_utc")
        assert team2["entering"].tolist() == [0, -1, -2, -3]
        assert team2["streak_after"].tolist() == [-1, -2, -3, 1]

    def test_streaks_reset_at_the_season_boundary(self):
        a = _games([(5, 2), (4, 1)], season=2024)  # team 1 ends 2024 on W2
        b = _games([(3, 1)], season=2025)
        log = build_team_log(pd.concat([a, b], ignore_index=True))
        opener = log[(log["team_id"] == 1) & (log["season"] == 2025)]
        assert opener["entering"].tolist() == [0]

    def test_rest_days_and_doubleheaders(self):
        frame = _games([(5, 2), (4, 1), (3, 2)])
        # Move game 3 onto game 2's date: a doubleheader.
        frame.loc[2, "official_date"] = frame.loc[1, "official_date"]
        log = build_team_log(frame)
        team1 = log[log["team_id"] == 1].sort_values(["game_date_utc", "game_id"])
        rest = team1["rest_days"].tolist()
        assert np.isnan(rest[0])  # no previous game
        assert rest[1] == 0.0  # next day: no day off
        assert rest[2] == 0.0  # same day: doubleheader

    def test_only_completed_regular_season_games_count(self):
        frame = _games([(5, 2), (4, 1)])
        frame.loc[1, "game_type"] = "P"  # postseason must be excluded
        log = build_team_log(frame)
        assert len(log) == 2  # one game, two sides


class TestReachCounts:
    def test_one_long_streak_reaches_each_length_once(self):
        log = build_team_log(_games([(5, 2)] * 7))  # team 1 wins seven straight
        team1 = log[log["team_id"] == 1]
        counts = _reach_counts(team1)
        assert counts["W2"] == 1 and counts["W7"] == 1
        assert counts["W8"] == 0
        team2 = log[log["team_id"] == 2]
        assert _reach_counts(team2)["L7"] == 1

    def test_two_separate_streaks_count_twice(self):
        log = build_team_log(_games([(5, 2), (4, 1), (0, 3), (5, 2), (4, 1)]))
        counts = _reach_counts(log[log["team_id"] == 1])
        assert counts["W2"] == 2

    def test_ten_plus_pools_once_per_streak(self):
        log = build_team_log(_games([(5, 2)] * 12))
        counts = _reach_counts(log[log["team_id"] == 1])
        assert counts["W10+"] == 1
        assert counts["W9"] == 1


class TestCells:
    def _occurrences(self, wins: int, losses: int, expected: float) -> pd.DataFrame:
        won = [True] * wins + [False] * losses
        return pd.DataFrame(
            {
                "won": won,
                "elo_expected": expected,
                "run_diff": [1 if w else -1 for w in won],
            }
        )

    def test_losing_streak_continues_by_losing(self):
        rows = self._occurrences(wins=7, losses=5, expected=0.54)
        cell = _cell(rows, sign=-1)
        assert cell.n == 12
        assert cell.continued == 5 and cell.ended == 7
        assert abs(cell.next_win_rate_raw - 7 / 12) < 1e-9
        assert abs(cell.p_continue_raw - 5 / 12) < 1e-9
        # Adjusted effect measures against expectation, not against 50%.
        assert abs(cell.adjusted_effect - (7 / 12 - 0.54)) < 1e-9

    def test_shrinkage_tames_a_tiny_perfect_sample(self):
        rows = self._occurrences(wins=2, losses=0, expected=0.5)
        cell = _cell(rows, sign=1)
        assert cell.next_win_rate_raw == 1.0
        expected_shrunk = (2 + SHRINKAGE_K * 0.5) / (2 + SHRINKAGE_K)
        assert abs(cell.next_win_rate_shrunk - expected_shrunk) < 1e-9
        assert cell.insufficient  # 2 < MIN_OCCURRENCES
        assert MIN_OCCURRENCES == 10

    def test_wilson_interval_brackets_the_raw_rate(self):
        rows = self._occurrences(wins=7, losses=5, expected=0.54)
        cell = _cell(rows, sign=-1)
        assert cell.ci_low < cell.next_win_rate_raw < cell.ci_high


class TestVenueStreak:
    def test_reads_only_the_given_subset(self):
        log = build_team_log(_games([(5, 2), (4, 1), (0, 3)]))
        team1 = log[log["team_id"] == 1]
        # All games are home games for team 1: W W L → home streak L1.
        assert _venue_streak(team1[team1["is_home"]]) == -1
        assert _venue_streak(team1[~team1["is_home"]]) == 0
