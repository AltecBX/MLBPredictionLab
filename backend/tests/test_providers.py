"""Provider contract and mapping tests.

Contract tests run against recorded fixture payloads, so they never make a
network call and a schema change surfaces here first.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from app.providers.base import (
    DataCategory,
    ProviderResult,
    ProviderStatus,
    ScheduleProvider,
)
from app.providers.mlb_statsapi import mappers
from app.providers.mlb_statsapi.client import MlbStatsApiClient
from app.providers.mlb_statsapi.provider import MlbStatsApiProvider
from app.providers.unavailable import (
    UnavailableOddsProvider,
    UnavailableStatcastProvider,
    UnavailableWeatherProvider,
)

SCHEDULE_PAYLOAD = {
    "dates": [
        {
            "date": "2024-07-04",
            "games": [
                {
                    "gamePk": 744834,
                    "gameGuid": "abc",
                    "gameType": "R",
                    "season": "2024",
                    "gameDate": "2024-07-04T15:05:00Z",
                    "officialDate": "2024-07-04",
                    "status": {
                        "abstractGameState": "Final",
                        "codedGameState": "F",
                        "detailedState": "Final",
                    },
                    "teams": {
                        "away": {
                            "team": {"id": 121},
                            "score": 0,
                            "leagueRecord": {"wins": 42, "losses": 43},
                            "probablePitcher": {"id": 500779, "fullName": "A Pitcher"},
                        },
                        "home": {
                            "team": {"id": 120},
                            "score": 3,
                            "leagueRecord": {"wins": 50, "losses": 40},
                            "probablePitcher": {"id": 663623, "fullName": "B Pitcher"},
                        },
                    },
                    "venue": {"id": 3309},
                    "weather": {"condition": "Partly Cloudy", "temp": "87",
                                "wind": "3 mph, Out To LF"},
                    "linescore": {"currentInning": 9},
                    "dayNight": "day",
                    "doubleHeader": "N",
                    "gameNumber": 1,
                    "seriesGameNumber": 4,
                    "gamesInSeries": 4,
                    "scheduledInnings": 9,
                }
            ],
        }
    ]
}

BOXSCORE_PAYLOAD = {
    "teams": {
        "home": {
            "team": {"id": 120},
            "teamStats": {
                "batting": {"runs": 3, "hits": 8, "atBats": 33, "plateAppearances": 37,
                            "baseOnBalls": 3, "strikeOuts": 9, "homeRuns": 1},
                "pitching": {"runs": 0, "earnedRuns": 0, "hits": 1, "baseOnBalls": 1,
                             "strikeOuts": 10, "homeRuns": 0, "battersFaced": 29,
                             "outs": 27, "inningsPitched": "9.0", "pitchesThrown": 108},
                "fielding": {"errors": 0},
            },
            "players": {
                "ID663623": {
                    "person": {"id": 663623, "fullName": "B Pitcher"},
                    "position": {"abbreviation": "P"},
                    "battingOrder": None,
                    "stats": {
                        "batting": {},
                        "pitching": {"gamesStarted": 1, "gamesPlayed": 1,
                                     "inningsPitched": "8.0", "earnedRuns": 0,
                                     "battersFaced": 26, "strikeOuts": 8,
                                     "baseOnBalls": 1, "homeRuns": 0, "hits": 1,
                                     "pitchesThrown": 99, "strikes": 70,
                                     "groundOuts": 3, "airOuts": 13},
                    },
                },
                "ID682928": {
                    "person": {"id": 682928, "fullName": "A Batter"},
                    "position": {"abbreviation": "SS"},
                    "battingOrder": "100",
                    "stats": {
                        "batting": {"gamesPlayed": 1, "atBats": 4,
                                    "plateAppearances": 4, "hits": 1, "homeRuns": 0,
                                    "strikeOuts": 1, "baseOnBalls": 0},
                        "pitching": {},
                    },
                },
                "ID999999": {
                    "person": {"id": 999999, "fullName": "Sub Batter"},
                    "position": {"abbreviation": "1B"},
                    "battingOrder": "301",
                    "stats": {
                        "batting": {"gamesPlayed": 1, "atBats": 1,
                                    "plateAppearances": 1, "hits": 0},
                        "pitching": {},
                    },
                },
            },
        },
        "away": {
            "team": {"id": 121},
            "teamStats": {"batting": {"runs": 0}, "pitching": {"outs": 24},
                          "fielding": {"errors": 1}},
            "players": {},
        },
    },
    "officials": [
        {"official": {"id": 1, "fullName": "Nate Tomlinson"}, "officialType": "Home Plate"},
    ],
}


# --- mapping ----------------------------------------------------------------

def test_map_game_extracts_every_scheduling_fact():
    node = SCHEDULE_PAYLOAD["dates"][0]["games"][0]
    game = mappers.map_game(node)
    assert game is not None
    assert game.id == 744834
    assert game.season == 2024
    assert game.game_date_utc == datetime(2024, 7, 4, 15, 5, tzinfo=UTC)
    assert game.official_date == date(2024, 7, 4)
    assert game.home_team_id == 120 and game.away_team_id == 121
    assert game.is_final is True
    assert game.home_score == 3 and game.away_score == 0
    assert game.home_win is True
    assert game.innings_played == 9
    assert game.home_probable_pitcher_id == 663623
    assert game.weather_temp_f == 87


def test_map_game_returns_none_on_an_unusable_row():
    assert mappers.map_game({"gamePk": 1}) is None


def test_scores_are_not_read_for_an_unfinished_game():
    node = {
        **SCHEDULE_PAYLOAD["dates"][0]["games"][0],
        "status": {"abstractGameState": "Preview", "codedGameState": "S",
                   "detailedState": "Scheduled"},
    }
    game = mappers.map_game(node)
    assert game.is_final is False
    assert game.home_score is None and game.away_score is None
    assert game.home_win is None
    assert game.innings_played is None


def test_innings_pitched_string_converts_to_outs():
    assert mappers.parse_innings_pitched("5.2") == 17
    assert mappers.parse_innings_pitched("6.0") == 18
    assert mappers.parse_innings_pitched("0.1") == 1
    assert mappers.parse_innings_pitched(None) is None


def test_batting_order_encoding():
    assert mappers._batting_order_slot("100") == (1, True)
    assert mappers._batting_order_slot("301") == (3, False)
    assert mappers._batting_order_slot(None) == (None, False)


def test_height_parsing():
    assert mappers.parse_height("6' 6\"") == 78
    assert mappers.parse_height(None) is None


def test_map_boxscore_splits_roles_and_extracts_the_lineup():
    box = mappers.map_boxscore(744834, BOXSCORE_PAYLOAD)
    assert box.game_id == 744834
    assert len(box.team_lines) == 2

    pitchers = [p for p in box.player_lines if p.role == "pitcher"]
    batters = [p for p in box.player_lines if p.role == "batter"]
    assert len(pitchers) == 1 and pitchers[0].is_starter is True
    assert {b.player_id for b in batters} == {682928, 999999}

    # Only starters (order ending in 00) become lineup entries.
    assert [entry["player_id"] for entry in box.lineups] == [682928]
    assert box.officials[0]["official_type"] == "Home Plate"


def test_team_stat_extraction_maps_to_columns():
    box = mappers.map_boxscore(744834, BOXSCORE_PAYLOAD)
    home = next(line for line in box.team_lines if line.team_id == 120)
    batting = mappers.extract(home.batting, mappers.TEAM_BATTING_FIELDS)
    pitching = mappers.extract(home.pitching, mappers.TEAM_PITCHING_FIELDS)
    assert batting["runs"] == 3
    assert batting["plate_appearances"] == 37
    assert pitching["strikeouts_pitched"] == 10
    assert mappers.outs_from_stats(home.pitching) == 27


def test_map_venue_reads_geometry_and_location():
    venue = mappers.map_venue(
        {
            "id": 3309,
            "name": "Nationals Park",
            "location": {
                "city": "Washington", "state": "District of Columbia",
                "defaultCoordinates": {"latitude": 38.87, "longitude": -77.01},
                "elevation": 35, "azimuthAngle": 28.0, "country": "USA",
            },
            "fieldInfo": {"capacity": 41376, "turfType": "Grass", "roofType": "Open",
                          "leftLine": 336, "center": 402, "rightLine": 335},
        }
    )
    assert venue.elevation_ft == 35
    assert venue.azimuth_angle == 28.0
    assert venue.roof_type == "Open"
    assert venue.timezone == "America/New_York"
    assert venue.center == 402


# --- contract ---------------------------------------------------------------

def test_provider_result_reports_status_and_hashes_the_payload():
    result = ProviderResult(
        status=ProviderStatus.OK,
        source_name="test",
        category=DataCategory.SCHEDULE,
        retrieved_at=datetime.now(UTC),
        knowledge_time=datetime.now(UTC),
        data=[],
        raw_payload={"a": 1},
    )
    assert result.ok is True
    assert len(result.content_hash) == 64
    assert result.content_hash == ProviderResult(
        status=ProviderStatus.OK, source_name="test", category=DataCategory.SCHEDULE,
        retrieved_at=result.retrieved_at, knowledge_time=result.knowledge_time,
        data=[], raw_payload={"a": 1},
    ).content_hash


def test_unavailable_result_names_the_missing_configuration():
    result = ProviderResult.unavailable("x", DataCategory.WEATHER, "not configured")
    assert result.ok is False
    assert result.data is None
    assert result.status is ProviderStatus.UNAVAILABLE


@pytest.mark.parametrize(
    ("provider", "env_var"),
    [
        (UnavailableWeatherProvider(), "WEATHER_PROVIDER"),
        (UnavailableOddsProvider(), "ODDS_PROVIDER"),
        (UnavailableStatcastProvider(), "STATCAST_PROVIDER"),
    ],
)
def test_unavailable_providers_name_the_variable_that_would_enable_them(provider, env_var):
    method = next(
        getattr(provider, name)
        for name in dir(provider)
        if name.startswith("fetch_")
    )
    result = (
        method(1, 2, datetime.now(UTC))
        if env_var == "WEATHER_PROVIDER"
        else method([1], datetime.now(UTC))
        if env_var == "ODDS_PROVIDER"
        else method(date(2024, 1, 1), date(2024, 1, 2))
    )
    assert result.status is ProviderStatus.UNAVAILABLE
    assert env_var in result.message
    assert result.data is None


def test_mlb_provider_satisfies_the_schedule_protocol():
    assert isinstance(MlbStatsApiProvider.__new__(MlbStatsApiProvider), ScheduleProvider)


def test_provider_failure_never_raises_past_its_boundary(monkeypatch):
    provider = MlbStatsApiProvider(client=MlbStatsApiClient(max_retries=0))

    def boom(*args, **kwargs):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(provider._client, "get", boom)
    result = provider.fetch_schedule(date(2024, 7, 1), date(2024, 7, 2))
    assert result.status is ProviderStatus.UNAVAILABLE
    assert result.data is None
    assert "failed" in result.message


def test_client_rate_limit_is_configurable():
    client = MlbStatsApiClient(min_interval_ms=250)
    assert client.min_interval_s == pytest.approx(0.25)
    client.close()
