"""The league rates exist on opening day and settle through April.

Before a season's first pitch there is no current-season league rate, and
every prior that reads one was undefined — hidden for a year by the spring
training games the store used to count. The season in progress is now shrunk
toward the previous season's completed rates by a pre-registered constant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.features.builder import LEAGUE_PRIOR_K_TEAM_GAMES, FeatureBuilder, LeagueBaseline
from app.features.elo import AsOfElo
from tests.conftest import SEASON, make_store

YEAR = timedelta(days=365)
ID_OFFSET = 100_000


def _day_boundary(moment) -> datetime:
    """`league_baseline` cuts at the start of the as-of day; the raw rates must too."""
    day = pd.Timestamp(moment).tz_convert("UTC").date()
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _two_seasons(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """The fixture season, plus a copy of it a year earlier with more scoring."""
    games = frames["games"].copy()
    earlier = games.copy()
    earlier["id"] = earlier["id"] + ID_OFFSET
    earlier["season"] = SEASON - 1
    for column in ("game_date_utc", "knowledge_time"):
        earlier[column] = earlier[column] - YEAR
    earlier["official_date"] = [d - YEAR for d in earlier["official_date"]]
    earlier["home_score"] = earlier["home_score"] + 2  # a higher-scoring year
    earlier["away_score"] = earlier["away_score"] + 2

    def shift(frame: pd.DataFrame, bump_runs: bool) -> pd.DataFrame:
        out = frame.copy()
        out["game_id"] = out["game_id"] + ID_OFFSET
        for column in ("game_date_utc", "knowledge_time"):
            out[column] = out[column] - YEAR
        if bump_runs:
            out["runs"] = out["runs"] + 2
            out["runs_allowed"] = out["runs_allowed"] + 2
        return out

    return {
        **frames,
        "games": pd.concat([earlier, games], ignore_index=True),
        "team_games": pd.concat(
            [shift(frames["team_games"], True), frames["team_games"]], ignore_index=True
        ),
        "pitcher_games": pd.concat(
            [shift(frames["pitcher_games"], False), frames["pitcher_games"]], ignore_index=True
        ),
    }


@pytest.fixture
def two_season_builder(fixture_frames) -> tuple[FeatureBuilder, pd.DataFrame]:
    frames = _two_seasons(fixture_frames)
    store = make_store(frames)
    return FeatureBuilder(store, AsOfElo(store.games)), frames["games"]


def test_opening_day_reads_the_previous_seasons_rates(two_season_builder):
    builder, games = two_season_builder
    this_season = games[games["season"] == SEASON]
    opening = min(this_season["game_date_utc"]) - timedelta(hours=3)

    baseline = builder.league_baseline(SEASON, opening)
    previous = builder._season_league_rates(SEASON - 1, min(this_season["game_date_utc"]))
    assert baseline.team_games == 0
    assert baseline.runs_per_game == pytest.approx(previous.runs_per_game)
    assert previous.runs_per_game == pytest.approx(5.75)  # the fixture's 3.75, plus two


def test_the_rates_settle_from_the_prior_toward_the_season_in_progress(two_season_builder):
    builder, games = two_season_builder
    this_season = games[games["season"] == SEASON]
    late = max(this_season["game_date_utc"]) + timedelta(days=1)

    blended = builder.league_baseline(SEASON, late)
    current = builder._season_league_rates(SEASON, _day_boundary(late))
    previous = builder._season_league_rates(SEASON - 1, min(this_season["game_date_utc"]))
    n = current.team_games
    # Forty fixture games, two team-games each; the last may still be
    # unknowable at the day boundary.
    assert 70 <= n <= 80
    expected = (current.runs_per_game * n + previous.runs_per_game * LEAGUE_PRIOR_K_TEAM_GAMES) / (
        n + LEAGUE_PRIOR_K_TEAM_GAMES
    )
    assert blended.runs_per_game == pytest.approx(expected)
    assert previous.runs_per_game > blended.runs_per_game > current.runs_per_game
    assert blended.team_games == n


def test_a_season_with_no_predecessor_keeps_its_own_rates(builder, store):
    """The fixture's only season: nothing to shrink toward, nothing invented."""
    first = min(store.games["game_date_utc"])
    before = builder.league_baseline(SEASON, first - timedelta(hours=3))
    assert before.team_games == 0
    assert before.runs_per_game is None
    later = builder.league_baseline(SEASON, first + timedelta(days=20))
    assert later.runs_per_game == pytest.approx(
        builder._season_league_rates(
            SEASON, _day_boundary(first + timedelta(days=20))
        ).runs_per_game
    )


def test_shrinking_toward_nothing_is_the_identity():
    rates = LeagueBaseline(
        runs_per_game=4.5, era=4.0, fip_constant=3.1, whip=1.3, k_pct=0.22, bb_pct=0.08,
        hr_per_9=1.1, woba_proxy=0.31, batter_k_pct=0.22, errors_per_game=0.6,
        def_efficiency=0.69, relief_era=4.2, relief_k_minus_bb_pct=0.14, team_games=10,
    )
    assert rates.shrunk_toward(None, 300.0) is rates
    empty = LeagueBaseline(**{name: None for name in LeagueBaseline.RATE_FIELDS}, team_games=0)
    assert empty.shrunk_toward(rates, 300.0).runs_per_game == 4.5
    assert rates.shrunk_toward(empty, 300.0).runs_per_game == 4.5
