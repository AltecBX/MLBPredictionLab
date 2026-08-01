"""Shared fixtures.

Tests build small, fully synthetic in-memory frames — these are TEST FIXTURES,
never data served to a user. Nothing here writes to the application database or
reaches an external source.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.features.asof import AsOfStore
from app.features.builder import FeatureBuilder
from app.features.context import GameContext
from app.features.elo import AsOfElo
from app.providers.mlb_statsapi.mappers import RESULT_KNOWLEDGE_LAG

SEASON = 2024
BASE = datetime(SEASON, 4, 1, 23, 0, tzinfo=UTC)

HOME_TEAM, AWAY_TEAM = 100, 200
HOME_SP, AWAY_SP = 9001, 9002
HOME_RP, AWAY_RP = 9101, 9102
HOME_PARK, AWAY_PARK = 500, 501


def _game(index: int, home: int, away: int, venue: int, home_runs: int, away_runs: int):
    first_pitch = BASE + timedelta(days=index)
    return {
        "id": 1000 + index,
        "season": SEASON,
        "game_type": "R",
        "game_date_utc": first_pitch,
        "official_date": first_pitch.date(),
        "home_team_id": home,
        "away_team_id": away,
        "venue_id": venue,
        "day_night": "night",
        "doubleheader": "N",
        "game_number": 1,
        "home_score": home_runs,
        "away_score": away_runs,
        "home_win": home_runs > away_runs,
        "is_final": True,
        "innings_played": 9,
        "knowledge_time": first_pitch + RESULT_KNOWLEDGE_LAG,
        "home_probable_pitcher_id": HOME_SP,
        "away_probable_pitcher_id": AWAY_SP,
    }


def _team_line(game: dict, team: int, opponent: int, is_home: bool, runs: int, allowed: int):
    return {
        "game_id": game["id"],
        "team_id": team,
        "opponent_team_id": opponent,
        "is_home": is_home,
        "game_date_utc": game["game_date_utc"],
        "knowledge_time": game["knowledge_time"],
        "runs": runs,
        "hits": 8,
        "doubles": 2,
        "triples": 0,
        "home_runs": 1,
        "walks": 3,
        "intentional_walks": 0,
        "strikeouts": 8,
        "hit_by_pitch": 0,
        "stolen_bases": 1,
        "caught_stealing": 0,
        "left_on_base": 6,
        "at_bats": 34,
        "plate_appearances": 38,
        "total_bases": 13,
        "sac_flies": 1,
        "sac_bunts": 0,
        "gidp": 1,
        "runs_allowed": allowed,
        "earned_runs_allowed": allowed,
        "hits_allowed": 8,
        "walks_allowed": 3,
        "strikeouts_pitched": 8,
        "home_runs_allowed": 1,
        "outs_pitched": 27,
        "batters_faced": 37,
        "pitches_thrown": 145,
        "strikes_thrown": 95,
        "ground_outs_pitched": 10,
        "air_outs_pitched": 9,
        "errors": 0,
    }


def _pitcher_line(game: dict, player: int, team: int, opponent: int, is_home: bool,
                  starter: bool, earned: int):
    return {
        "game_id": game["id"],
        "player_id": player,
        "team_id": team,
        "opponent_team_id": opponent,
        "game_date_utc": game["game_date_utc"],
        "knowledge_time": game["knowledge_time"],
        "is_home": is_home,
        "role": "pitcher",
        "is_starter": starter,
        "position": "P",
        "games_started": 1 if starter else 0,
        "outs_pitched": 18 if starter else 9,
        "batters_faced": 24 if starter else 12,
        "hits_allowed": 5 if starter else 2,
        "runs_allowed": earned,
        "earned_runs": earned,
        "bb_allowed": 2 if starter else 1,
        "ibb_allowed": 0,
        "so_pitched": 7 if starter else 3,
        "hr_allowed": 1 if starter else 0,
        "hbp_allowed": 0,
        "pitches_thrown": 95 if starter else 20,
        "strikes_thrown": 62 if starter else 14,
        "ground_outs_pitched": 7 if starter else 3,
        "air_outs_pitched": 6 if starter else 3,
        "inherited_runners": 0,
        "inherited_runners_scored": 0,
        "wild_pitches": 0,
        "balks": 0,
    }


@pytest.fixture
def fixture_frames() -> dict[str, pd.DataFrame]:
    games, team_lines, pitcher_lines = [], [], []
    for i in range(40):
        home, away = (HOME_TEAM, AWAY_TEAM) if i % 2 == 0 else (AWAY_TEAM, HOME_TEAM)
        venue = HOME_PARK if home == HOME_TEAM else AWAY_PARK
        home_runs = 5 if home == HOME_TEAM else 3
        away_runs = 3 if home == HOME_TEAM else 4
        game = _game(i, home, away, venue, home_runs, away_runs)
        games.append(game)
        team_lines.append(_team_line(game, home, away, True, home_runs, away_runs))
        team_lines.append(_team_line(game, away, home, False, away_runs, home_runs))
        pitcher_lines.append(
            _pitcher_line(game, HOME_SP if home == HOME_TEAM else AWAY_SP,
                          home, away, True, True, 2)
        )
        pitcher_lines.append(
            _pitcher_line(game, AWAY_SP if home == HOME_TEAM else HOME_SP,
                          away, home, False, True, 3)
        )
        pitcher_lines.append(
            _pitcher_line(game, HOME_RP if home == HOME_TEAM else AWAY_RP,
                          home, away, True, False, 1)
        )
        pitcher_lines.append(
            _pitcher_line(game, AWAY_RP if home == HOME_TEAM else HOME_RP,
                          away, home, False, False, 1)
        )

    players = pd.DataFrame(
        [
            {"id": HOME_SP, "full_name": "Home Starter", "pitch_hand": "R", "bat_side": "R"},
            {"id": AWAY_SP, "full_name": "Away Starter", "pitch_hand": "L", "bat_side": "L"},
            {"id": HOME_RP, "full_name": "Home Reliever", "pitch_hand": "R", "bat_side": "R"},
            {"id": AWAY_RP, "full_name": "Away Reliever", "pitch_hand": "L", "bat_side": "L"},
        ]
    )
    ballparks = pd.DataFrame(
        [
            {"id": HOME_PARK, "name": "Home Park", "latitude": 40.0, "longitude": -74.0,
             "elevation_ft": 20, "roof_type": "Open", "timezone": "America/New_York"},
            {"id": AWAY_PARK, "name": "Away Park", "latitude": 34.0, "longitude": -118.0,
             "elevation_ft": 500, "roof_type": "Open", "timezone": "America/Los_Angeles"},
        ]
    )
    return {
        "games": pd.DataFrame(games),
        "team_games": pd.DataFrame(team_lines),
        "pitcher_games": pd.DataFrame(pitcher_lines),
        "players": players,
        "ballparks": ballparks,
    }


def make_store(frames: dict[str, pd.DataFrame]) -> AsOfStore:
    return AsOfStore(
        AsOfStore._prepare_games(frames["games"]),
        AsOfStore._prepare_team_games(frames["team_games"], frames["games"]),
        AsOfStore._prepare_pitcher_games(frames["pitcher_games"]),
        frames["players"],
        frames["ballparks"],
    )


@pytest.fixture
def store(fixture_frames) -> AsOfStore:
    return make_store(fixture_frames)


@pytest.fixture
def builder(store) -> FeatureBuilder:
    return FeatureBuilder(store, AsOfElo(store.games))


@pytest.fixture
def target_game(store) -> GameContext:
    """A game late in the fixture window, so history exists before it."""
    row = store.games.iloc[35].to_dict()
    return GameContext.from_row(row)
