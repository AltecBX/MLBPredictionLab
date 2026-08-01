"""Elo team-strength engine.

The rating used for game *i* is the rating produced after game *i−1*. The
update for game *i* is applied only after the pre-game rating has been emitted
(LEAKAGE_PREVENTION.md §9).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

import pandas as pd

BASE_RATING = 1500.0
DEFAULT_K = 6.0
DEFAULT_HOME_ADVANTAGE = 24.0
SEASON_REGRESSION = 0.30  # fraction pulled back toward the mean between seasons
MOV_SCALE = 1.0


@dataclass
class EloEngine:
    """Chronological Elo with a margin-of-victory multiplier."""

    k: float = DEFAULT_K
    home_advantage: float = DEFAULT_HOME_ADVANTAGE
    season_regression: float = SEASON_REGRESSION
    ratings: dict[int, float] = field(default_factory=dict)
    _last_season: int | None = None
    pregame: dict[tuple[int, int], float] = field(default_factory=dict)

    def rating(self, team_id: int) -> float:
        return self.ratings.get(int(team_id), BASE_RATING)

    def _regress_for_new_season(self, season: int) -> None:
        if self._last_season is None:
            self._last_season = season
            return
        if season == self._last_season:
            return
        for team_id, value in self.ratings.items():
            self.ratings[team_id] = value + (BASE_RATING - value) * self.season_regression
        self._last_season = season

    @staticmethod
    def expected_home(home_rating: float, away_rating: float, home_advantage: float) -> float:
        diff = (home_rating + home_advantage) - away_rating
        return 1.0 / (1.0 + 10 ** (-diff / 400.0))

    def win_probability(self, home_team_id: int, away_team_id: int) -> float:
        return self.expected_home(
            self.rating(home_team_id), self.rating(away_team_id), self.home_advantage
        )

    def observe(
        self,
        game_id: int,
        season: int,
        home_team_id: int,
        away_team_id: int,
        home_score: int,
        away_score: int,
    ) -> None:
        """Record the pre-game ratings, then apply the update."""
        self._regress_for_new_season(season)
        home_before, away_before = self.rating(home_team_id), self.rating(away_team_id)
        self.pregame[(game_id, int(home_team_id))] = home_before
        self.pregame[(game_id, int(away_team_id))] = away_before

        expected = self.expected_home(home_before, away_before, self.home_advantage)
        actual = 1.0 if home_score > away_score else 0.0
        margin = abs(home_score - away_score)
        multiplier = MOV_SCALE * (1.0 + 0.15 * (margin - 1)) if margin > 1 else MOV_SCALE
        multiplier = min(multiplier, 2.0)

        delta = self.k * multiplier * (actual - expected)
        self.ratings[int(home_team_id)] = home_before + delta
        self.ratings[int(away_team_id)] = away_before - delta

    def rating_before(self, game_id: int, team_id: int) -> float:
        """Pre-game rating. Falls back to the base rating for an unseen team."""
        return self.pregame.get((int(game_id), int(team_id)), self.rating(team_id))


def build_elo_history(games: pd.DataFrame, **kwargs: float) -> EloEngine:
    """Single forward pass over completed games in chronological order."""
    engine = EloEngine(**kwargs)  # type: ignore[arg-type]
    if games.empty:
        return engine
    completed = games[
        games["is_final"].fillna(False)
        & games["home_score"].notna()
        & games["away_score"].notna()
    ].sort_values("game_date_utc")
    for row in completed.itertuples():
        engine.observe(
            game_id=int(row.id),
            season=int(row.season),
            home_team_id=int(row.home_team_id),
            away_team_id=int(row.away_team_id),
            home_score=int(row.home_score),
            away_score=int(row.away_score),
        )
    return engine


class AsOfElo:
    """Elo ratings queryable at an arbitrary ``as_of``.

    Built by replaying completed games in order and snapshotting the rating
    each team carried immediately before each game. A rating for a time with no
    subsequent game falls back to the last snapshot before that time.
    """

    @staticmethod
    def _ns(moment) -> int:
        return pd.Timestamp(moment).tz_convert("UTC").value

    def __init__(self, games: pd.DataFrame, **kwargs: float) -> None:
        self._times: dict[int, list[int]] = {}
        self._values: dict[int, list[float]] = {}
        engine = EloEngine(**kwargs)  # type: ignore[arg-type]
        self.engine = engine
        if games.empty:
            return
        completed = games[
            games["is_final"].fillna(False)
            & games["home_score"].notna()
            & games["away_score"].notna()
        ].sort_values("game_date_utc")
        for row in completed.itertuples():
            # The rating is knowable only after the game has been played.
            knowable_at = row.knowledge_time
            engine.observe(
                game_id=int(row.id),
                season=int(row.season),
                home_team_id=int(row.home_team_id),
                away_team_id=int(row.away_team_id),
                home_score=int(row.home_score),
                away_score=int(row.away_score),
            )
            knowable_ns = self._ns(knowable_at)
            for team_id in (int(row.home_team_id), int(row.away_team_id)):
                self._times.setdefault(team_id, []).append(knowable_ns)
                self._values.setdefault(team_id, []).append(engine.rating(team_id))

    def _cut(self, team_id: int, as_of: pd.Timestamp) -> int:
        times = self._times.get(int(team_id))
        if not times:
            return 0
        return bisect_right(times, self._ns(as_of))

    def rating_at(self, team_id: int, as_of: pd.Timestamp) -> float:
        cut = self._cut(team_id, as_of)
        if cut == 0:
            return BASE_RATING
        return self._values[int(team_id)][cut - 1]

    def games_rated(self, team_id: int, as_of: pd.Timestamp) -> int:
        """Completed games whose result was knowable by ``as_of``."""
        return self._cut(team_id, as_of)
