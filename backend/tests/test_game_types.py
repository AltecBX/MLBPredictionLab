"""Only regular-season lines feed the rates; only competitive games move Elo.

For the first year of this repository every season-to-date rate, projection
and run mean counted spring training — split squads and minor-league line-ups,
a sixth of a team's games by midsummer — because the store loaded every game
type and `season_start_utc` said, wrongly, that the type filter lived
elsewhere. These pin the rule at the one place it now lives: the store.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.features.asof import (
    COMPETITIVE_GAME_TYPES,
    RATE_GAME_TYPES,
    AsOfStore,
    competitive_games,
    rate_game_ids,
    season_start_utc,
)
from app.features.builder import FeatureVector, SideFeatures
from app.features.elo import AsOfElo, build_elo_history
from app.features.shrinkage import FeatureValue
from tests.conftest import make_store

SPRING_ID = 999_001
PLAYOFF_ID = 999_002


def _with_extra_games(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """The fixture season plus one spring-training game and one playoff game."""
    games = frames["games"].copy()
    template = games.iloc[0].to_dict()
    earliest = min(games["game_date_utc"])
    latest = max(games["game_date_utc"])

    spring = dict(template)
    spring.update(
        id=SPRING_ID, game_type="S", game_date_utc=earliest - timedelta(days=10),
        official_date=(earliest - timedelta(days=10)).date(),
        home_score=15, away_score=0, home_win=True,
        knowledge_time=earliest - timedelta(days=10) + timedelta(hours=4),
    )
    playoff = dict(template)
    playoff.update(
        id=PLAYOFF_ID, game_type="D", game_date_utc=latest + timedelta(days=10),
        official_date=(latest + timedelta(days=10)).date(),
        home_score=2, away_score=1, home_win=True,
        knowledge_time=latest + timedelta(days=10) + timedelta(hours=4),
    )
    games = pd.concat([games, pd.DataFrame([spring, playoff])], ignore_index=True)

    def lines(source: pd.DataFrame, game: dict) -> pd.DataFrame:
        first = source[source["game_id"] == template["id"]].copy()
        first["game_id"] = game["id"]
        first["game_date_utc"] = game["game_date_utc"]
        first["knowledge_time"] = game["knowledge_time"]
        return first

    team_games = pd.concat(
        [frames["team_games"], lines(frames["team_games"], spring), lines(frames["team_games"], playoff)],
        ignore_index=True,
    )
    pitcher_games = pd.concat(
        [frames["pitcher_games"], lines(frames["pitcher_games"], spring),
         lines(frames["pitcher_games"], playoff)],
        ignore_index=True,
    )
    return {**frames, "games": games, "team_games": team_games, "pitcher_games": pitcher_games}


@pytest.fixture
def store_with_extras(fixture_frames) -> AsOfStore:
    return make_store(_with_extra_games(fixture_frames))


def test_the_constants_say_what_the_docstrings_promise():
    assert {"R"} == RATE_GAME_TYPES
    assert "S" not in COMPETITIVE_GAME_TYPES
    assert {"R", "D", "L", "W", "F"} <= COMPETITIVE_GAME_TYPES


def test_spring_and_playoff_lines_never_reach_the_rate_frames(store_with_extras):
    store = store_with_extras
    assert SPRING_ID in set(store.games["id"])  # the schedule still knows the game
    for frame in (store.team_games, store.pitcher_games):
        assert SPRING_ID not in set(frame["game_id"])
        assert PLAYOFF_ID not in set(frame["game_id"])
    assert set(store.team_games["game_type"]) == {"R"}


def test_the_as_of_rates_do_not_see_a_spring_blowout(store_with_extras, fixture_frames):
    """A 15–0 exhibition, ten days before opening day, moves no season rate."""
    clean = make_store(fixture_frames)
    dirty = store_with_extras
    team_id = int(fixture_frames["games"].iloc[0]["home_team_id"])
    as_of = min(fixture_frames["games"]["game_date_utc"]) + timedelta(days=5)
    start = season_start_utc(int(fixture_frames["games"].iloc[0]["season"]))
    a = clean.team_games_asof(team_id, as_of, start)
    b = dirty.team_games_asof(team_id, as_of, start)
    assert len(a) == len(b)
    assert a["runs"].sum() == b["runs"].sum()


def test_elo_ignores_the_exhibition_and_counts_the_playoff(fixture_frames):
    frames = _with_extra_games(fixture_frames)
    games = frames["games"]
    with_extras = build_elo_history(games)
    without_spring = build_elo_history(games[games["game_type"] != "S"])
    regular_only = build_elo_history(games[games["game_type"] == "R"])
    home = int(games.iloc[0]["home_team_id"])
    assert with_extras.rating(home) == pytest.approx(without_spring.rating(home))
    assert with_extras.rating(home) != pytest.approx(regular_only.rating(home))

    asof = AsOfElo(games)
    before_opening_day = min(games[games["game_type"] == "R"]["game_date_utc"]) - timedelta(hours=1)
    assert asof.games_rated(home, pd.Timestamp(before_opening_day)) == 0


def test_the_schedule_index_counts_competitive_games_only(store_with_extras, fixture_frames):
    team_id = int(fixture_frames["games"].iloc[0]["home_team_id"])
    schedule = store_with_extras.team_schedule_all(team_id)
    assert SPRING_ID not in set(schedule["id"])
    assert PLAYOFF_ID in set(schedule["id"])


def test_helpers_pass_a_typeless_frame_through_unchanged():
    """A frame that cannot say what kind of game a line came from is left alone."""
    frame = pd.DataFrame({"id": [1, 2]})
    assert competitive_games(frame) is frame
    assert rate_game_ids(frame) is None
    typed = pd.DataFrame({"id": [1, 2, 3], "game_type": ["R", "S", "W"]})
    assert rate_game_ids(typed) == {1}
    assert set(competitive_games(typed)["id"]) == {1, 3}


def test_the_season_boundary_is_new_years_day():
    """The boundary is a date; the type filter is what keeps spring out."""
    assert season_start_utc(2024) == datetime(2024, 1, 1, tzinfo=UTC)


# --- opening day ------------------------------------------------------------
#
# With no spring games to count, a team has no games of history on opening
# day. A side whose projection stands on a real prior-season sample is still
# predictable; a side with neither is not.


def _side(team_games: int, projection_sample: int | None) -> SideFeatures:
    values = {}
    if projection_sample is not None:
        values["proj_off_rpg"] = FeatureValue(4.6, projection_sample, False)
    return SideFeatures(
        values=values, starter_id=None, starter_status="UNKNOWN", starter_hand=None,
        team_games_sample=team_games,
    )


def _vector(home: SideFeatures, away: SideFeatures, completeness: float = 0.6) -> FeatureVector:
    return FeatureVector(
        game_id=1, as_of=datetime(2025, 3, 27, 20, tzinfo=UTC), feature_set_version="fs_v9",
        features={}, sample_sizes={}, estimated_flags={}, missing_features=[],
        completeness=completeness, home=home, away=away,
    )


def test_opening_day_is_predictable_from_the_projections():
    assert _vector(_side(0, 300), _side(0, 250)).is_usable
    assert _vector(_side(0, 300), _side(12, None)).is_usable


def test_a_side_with_no_history_of_either_kind_is_not():
    assert not _vector(_side(0, None), _side(0, 300)).is_usable
    assert not _vector(_side(0, 0), _side(9, 300)).is_usable  # a projection on nothing
    assert not _vector(_side(0, 300), _side(0, 250), completeness=0.0).is_usable
