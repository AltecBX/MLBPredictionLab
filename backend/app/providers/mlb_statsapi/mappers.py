"""Payload -> normalized record mapping for the MLB Stats API.

All parsing lives here so an upstream schema change is a one-file fix.
Nothing in this module invents a value: an absent field maps to ``None``.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.providers.base import (
    RawBoxscore,
    RawGame,
    RawPlayer,
    RawPlayerGameLine,
    RawTeam,
    RawTeamGameLine,
    RawVenue,
)

# Conservative fallback when the source does not expose a game end time
# (DATA_SOURCES.md §4). It can only make a fact less available, never more.
RESULT_KNOWLEDGE_LAG = timedelta(hours=3, minutes=30)

_HEIGHT_RE = re.compile(r"(?P<ft>\d+)'\s*(?P<inch>\d+)")

# Ballpark timezones by state/province. Static reference data, not an estimate
# about anything observable. A venue outside this map gets ``None`` rather than
# a guessed zone.
_STATE_TZ = {
    "Arizona": "America/Phoenix",
    "California": "America/Los_Angeles",
    "Colorado": "America/Denver",
    "District of Columbia": "America/New_York",
    "Florida": "America/New_York",
    "Georgia": "America/New_York",
    "Illinois": "America/Chicago",
    "Maryland": "America/New_York",
    "Massachusetts": "America/New_York",
    "Michigan": "America/New_York",
    "Minnesota": "America/Chicago",
    "Missouri": "America/Chicago",
    "New York": "America/New_York",
    "Ohio": "America/New_York",
    "Ontario": "America/Toronto",
    "Pennsylvania": "America/New_York",
    "Texas": "America/Chicago",
    "Washington": "America/Los_Angeles",
    "Wisconsin": "America/Chicago",
}


def _int(value: Any) -> int | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_height(value: str | None) -> int | None:
    if not value:
        return None
    m = _HEIGHT_RE.search(value)
    if not m:
        return None
    return int(m.group("ft")) * 12 + int(m.group("inch"))


def parse_innings_pitched(value: Any) -> int | None:
    """MLB reports IP as '5.2' meaning 5 innings + 2 outs. Return total outs."""
    if value is None or value == "":
        return None
    try:
        text = str(value)
        whole, _, frac = text.partition(".")
        outs = int(whole) * 3
        if frac:
            outs += int(frac[0])
        return outs
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Reference
# ---------------------------------------------------------------------------


def map_team(node: dict[str, Any]) -> RawTeam:
    return RawTeam(
        id=int(node["id"]),
        name=node.get("name") or "",
        abbreviation=node.get("abbreviation") or "",
        team_name=node.get("teamName"),
        location_name=node.get("locationName"),
        league_id=_int((node.get("league") or {}).get("id")),
        league_name=(node.get("league") or {}).get("name"),
        division_id=_int((node.get("division") or {}).get("id")),
        division_name=(node.get("division") or {}).get("name"),
        home_venue_id=_int((node.get("venue") or {}).get("id")),
        first_year_of_play=_int(node.get("firstYearOfPlay")),
        active=bool(node.get("active", True)),
    )


def map_venue(node: dict[str, Any]) -> RawVenue:
    location = node.get("location") or {}
    coords = location.get("defaultCoordinates") or {}
    field = node.get("fieldInfo") or {}
    state = location.get("state")
    return RawVenue(
        id=int(node["id"]),
        name=node.get("name") or "",
        city=location.get("city"),
        state=state,
        country=location.get("country"),
        latitude=_float(coords.get("latitude")),
        longitude=_float(coords.get("longitude")),
        elevation_ft=_int(location.get("elevation")),
        azimuth_angle=_float(location.get("azimuthAngle")),
        roof_type=field.get("roofType"),
        turf_type=field.get("turfType"),
        capacity=_int(field.get("capacity")),
        lf_line=_int(field.get("leftLine")),
        lf_center=_int(field.get("leftCenter")),
        center=_int(field.get("center")),
        rf_center=_int(field.get("rightCenter")),
        rf_line=_int(field.get("rightLine")),
        timezone=_STATE_TZ.get(state or "", None),
        active=bool(node.get("active", True)),
    )


def map_player(node: dict[str, Any]) -> RawPlayer:
    position = node.get("primaryPosition") or {}
    return RawPlayer(
        id=int(node["id"]),
        full_name=node.get("fullName") or "",
        primary_position=position.get("abbreviation"),
        position_type=position.get("type"),
        bat_side=(node.get("batSide") or {}).get("code"),
        pitch_hand=(node.get("pitchHand") or {}).get("code"),
        birth_date=parse_date(node.get("birthDate")),
        mlb_debut_date=parse_date(node.get("mlbDebutDate")),
        height_in=parse_height(node.get("height")),
        weight_lb=_int(node.get("weight")),
        active=bool(node.get("active", True)),
    )


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


def map_game(node: dict[str, Any]) -> RawGame | None:
    game_date = parse_utc(node.get("gameDate"))
    official = parse_date(node.get("officialDate"))
    teams = node.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    home_team = (home.get("team") or {}).get("id")
    away_team = (away.get("team") or {}).get("id")

    if game_date is None or official is None or home_team is None or away_team is None:
        return None

    status = node.get("status") or {}
    abstract = status.get("abstractGameState")
    is_final = abstract == "Final" and status.get("codedGameState") in ("F", "O")

    linescore = node.get("linescore") or {}
    innings_played = _int(linescore.get("currentInning")) if is_final else None

    weather = node.get("weather") or {}
    home_record = home.get("leagueRecord") or {}
    away_record = away.get("leagueRecord") or {}

    return RawGame(
        id=int(node["gamePk"]),
        season=int(node.get("season") or game_date.year),
        game_type=node.get("gameType") or "R",
        game_date_utc=game_date,
        official_date=official,
        home_team_id=int(home_team),
        away_team_id=int(away_team),
        venue_id=_int((node.get("venue") or {}).get("id")),
        status_abstract=abstract,
        status_detailed=status.get("detailedState"),
        status_code=status.get("codedGameState"),
        game_guid=node.get("gameGuid"),
        day_night=node.get("dayNight"),
        doubleheader=node.get("doubleHeader"),
        game_number=_int(node.get("gameNumber")),
        series_game_number=_int(node.get("seriesGameNumber")),
        games_in_series=_int(node.get("gamesInSeries")),
        scheduled_innings=_int(node.get("scheduledInnings")),
        home_score=_int(home.get("score")) if is_final else None,
        away_score=_int(away.get("score")) if is_final else None,
        is_final=is_final,
        innings_played=innings_played,
        home_probable_pitcher_id=_int((home.get("probablePitcher") or {}).get("id")),
        away_probable_pitcher_id=_int((away.get("probablePitcher") or {}).get("id")),
        home_record_wins=_int(home_record.get("wins")),
        home_record_losses=_int(home_record.get("losses")),
        away_record_wins=_int(away_record.get("wins")),
        away_record_losses=_int(away_record.get("losses")),
        weather_condition=weather.get("condition"),
        weather_temp_f=_int(weather.get("temp")),
        weather_wind=weather.get("wind"),
    )


def probable_pitcher_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for side in ("home", "away"):
        pp = ((node.get("teams") or {}).get(side) or {}).get("probablePitcher")
        if pp and pp.get("id"):
            out.append(pp)
    return out


def result_knowledge_time(game: RawGame) -> datetime:
    """When the result became knowable. Conservative by design."""
    return game.game_date_utc + RESULT_KNOWLEDGE_LAG


# ---------------------------------------------------------------------------
# Boxscore
# ---------------------------------------------------------------------------


def _batting_order_slot(raw: Any) -> tuple[int | None, bool]:
    """MLB encodes batting order as e.g. '100' (slot 1, starter) or '101' (sub)."""
    value = _int(raw)
    if value is None:
        return None, False
    slot = value // 100
    return (slot if 1 <= slot <= 9 else None), value % 100 == 0


def map_boxscore(game_id: int, payload: dict[str, Any]) -> RawBoxscore:
    teams = payload.get("teams") or {}
    team_lines: list[RawTeamGameLine] = []
    player_lines: list[RawPlayerGameLine] = []
    lineups: list[dict[str, Any]] = []

    side_team_ids: dict[str, int | None] = {}
    for side in ("home", "away"):
        side_team_ids[side] = _int(((teams.get(side) or {}).get("team") or {}).get("id"))

    for side in ("home", "away"):
        node = teams.get(side) or {}
        team_id = side_team_ids[side]
        opponent_id = side_team_ids["away" if side == "home" else "home"]
        if team_id is None or opponent_id is None:
            continue
        is_home = side == "home"

        stats = node.get("teamStats") or {}
        team_lines.append(
            RawTeamGameLine(
                game_id=game_id,
                team_id=team_id,
                opponent_team_id=opponent_id,
                is_home=is_home,
                batting=stats.get("batting") or {},
                pitching=stats.get("pitching") or {},
                fielding=stats.get("fielding") or {},
            )
        )

        for _key, player in (node.get("players") or {}).items():
            person_id = _int((player.get("person") or {}).get("id"))
            if person_id is None:
                continue
            position = (player.get("position") or {}).get("abbreviation")
            pstats = player.get("stats") or {}
            slot, is_lineup_starter = _batting_order_slot(player.get("battingOrder"))

            batting = pstats.get("batting") or {}
            if batting.get("gamesPlayed") or batting.get("plateAppearances") or slot:
                player_lines.append(
                    RawPlayerGameLine(
                        game_id=game_id,
                        player_id=person_id,
                        team_id=team_id,
                        opponent_team_id=opponent_id,
                        is_home=is_home,
                        role="batter",
                        position=position,
                        batting_order=slot,
                        is_starter=is_lineup_starter,
                        stats=batting,
                    )
                )
                if slot is not None and is_lineup_starter:
                    lineups.append(
                        {
                            "game_id": game_id,
                            "team_id": team_id,
                            "player_id": person_id,
                            "batting_order": slot,
                            "position": position,
                        }
                    )

            pitching = pstats.get("pitching") or {}
            if pitching.get("gamesPlayed") or pitching.get("battersFaced"):
                player_lines.append(
                    RawPlayerGameLine(
                        game_id=game_id,
                        player_id=person_id,
                        team_id=team_id,
                        opponent_team_id=opponent_id,
                        is_home=is_home,
                        role="pitcher",
                        position=position,
                        batting_order=None,
                        is_starter=bool(_int(pitching.get("gamesStarted"))),
                        stats=pitching,
                    )
                )

    officials = [
        {
            "official_type": o.get("officialType"),
            "official_id": _int((o.get("official") or {}).get("id")),
            "official_name": (o.get("official") or {}).get("fullName"),
        }
        for o in (payload.get("officials") or [])
        if o.get("officialType")
    ]

    return RawBoxscore(
        game_id=game_id,
        team_lines=team_lines,
        player_lines=player_lines,
        officials=officials,
        lineups=lineups,
    )


BATTING_FIELDS = {
    "pa": "plateAppearances",
    "ab": "atBats",
    "hits": "hits",
    "doubles": "doubles",
    "triples": "triples",
    "home_runs": "homeRuns",
    "runs": "runs",
    "rbi": "rbi",
    "bb": "baseOnBalls",
    "ibb": "intentionalWalks",
    "so": "strikeOuts",
    "hbp": "hitByPitch",
    "sb": "stolenBases",
    "cs": "caughtStealing",
    "sac_flies": "sacFlies",
    "sac_bunts": "sacBunts",
    "gidp": "groundIntoDoublePlay",
    "total_bases": "totalBases",
    "left_on_base": "leftOnBase",
}

PITCHING_FIELDS = {
    "games_started": "gamesStarted",
    "batters_faced": "battersFaced",
    "hits_allowed": "hits",
    "runs_allowed": "runs",
    "earned_runs": "earnedRuns",
    "bb_allowed": "baseOnBalls",
    "ibb_allowed": "intentionalWalks",
    "so_pitched": "strikeOuts",
    "hr_allowed": "homeRuns",
    "hbp_allowed": "hitBatsmen",
    "pitches_thrown": "pitchesThrown",
    "strikes_thrown": "strikes",
    "ground_outs_pitched": "groundOuts",
    "air_outs_pitched": "airOuts",
    "inherited_runners": "inheritedRunners",
    "inherited_runners_scored": "inheritedRunnersScored",
    "wild_pitches": "wildPitches",
    "balks": "balks",
}

TEAM_BATTING_FIELDS = {
    "runs": "runs",
    "hits": "hits",
    "doubles": "doubles",
    "triples": "triples",
    "home_runs": "homeRuns",
    "walks": "baseOnBalls",
    "intentional_walks": "intentionalWalks",
    "strikeouts": "strikeOuts",
    "hit_by_pitch": "hitByPitch",
    "stolen_bases": "stolenBases",
    "caught_stealing": "caughtStealing",
    "left_on_base": "leftOnBase",
    "at_bats": "atBats",
    "plate_appearances": "plateAppearances",
    "total_bases": "totalBases",
    "sac_flies": "sacFlies",
    "sac_bunts": "sacBunts",
    "gidp": "groundIntoDoublePlay",
}

TEAM_PITCHING_FIELDS = {
    "runs_allowed": "runs",
    "earned_runs_allowed": "earnedRuns",
    "hits_allowed": "hits",
    "walks_allowed": "baseOnBalls",
    "strikeouts_pitched": "strikeOuts",
    "home_runs_allowed": "homeRuns",
    "batters_faced": "battersFaced",
    "pitches_thrown": "pitchesThrown",
    "strikes_thrown": "strikes",
    "ground_outs_pitched": "groundOuts",
    "air_outs_pitched": "airOuts",
}


def extract(stats: dict[str, Any], mapping: dict[str, str]) -> dict[str, int | None]:
    return {column: _int(stats.get(key)) for column, key in mapping.items()}


def outs_from_stats(stats: dict[str, Any]) -> int | None:
    outs = _int(stats.get("outs"))
    if outs is not None:
        return outs
    return parse_innings_pitched(stats.get("inningsPitched"))
