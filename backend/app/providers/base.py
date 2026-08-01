"""Provider contract.

Every external fact enters the system through one of these Protocols. A
provider never invents a value, never raises past its own boundary, and always
returns the metadata needed to audit the fact (DATA_SOURCES.md §1).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


class ProviderStatus(StrEnum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class DataCategory(StrEnum):
    """Categories tracked independently for freshness (ARCHITECTURE.md §7)."""

    REFERENCE = "reference"
    SCHEDULE = "schedule"
    RESULTS = "results"
    PROBABLE_PITCHERS = "probable_pitchers"
    LINEUPS = "lineups"
    INJURIES = "injuries"
    WEATHER = "weather"
    PLAYER_STATS = "player_stats"
    BULLPEN_USAGE = "bullpen_usage"
    BULLPEN_AVAILABILITY = "bullpen_availability"
    STATCAST = "statcast"
    PARK_FACTORS = "park_factors"
    ODDS = "odds"


@dataclass(frozen=True, slots=True)
class ProviderResult(Generic[T]):
    status: ProviderStatus
    source_name: str
    category: DataCategory
    retrieved_at: datetime
    knowledge_time: datetime
    data: T | None = None
    raw_payload: dict[str, Any] | None = None
    message: str | None = None
    endpoint: str | None = None
    request_params: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (ProviderStatus.OK, ProviderStatus.PARTIAL)

    @property
    def content_hash(self) -> str | None:
        if self.raw_payload is None:
            return None
        blob = json.dumps(self.raw_payload, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()

    @classmethod
    def unavailable(
        cls,
        source_name: str,
        category: DataCategory,
        message: str,
        *,
        endpoint: str | None = None,
        request_params: dict[str, Any] | None = None,
    ) -> ProviderResult[T]:
        now = datetime.now(UTC)
        return cls(
            status=ProviderStatus.UNAVAILABLE,
            source_name=source_name,
            category=category,
            retrieved_at=now,
            knowledge_time=now,
            data=None,
            message=message,
            endpoint=endpoint,
            request_params=request_params or {},
        )


# ---------------------------------------------------------------------------
# Normalized records. Providers map their payload onto these; nothing
# downstream ever sees a provider-specific shape.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawTeam:
    id: int
    name: str
    abbreviation: str
    team_name: str | None = None
    location_name: str | None = None
    league_id: int | None = None
    league_name: str | None = None
    division_id: int | None = None
    division_name: str | None = None
    home_venue_id: int | None = None
    first_year_of_play: int | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class RawVenue:
    id: int
    name: str
    city: str | None = None
    state: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    elevation_ft: int | None = None
    azimuth_angle: float | None = None
    roof_type: str | None = None
    turf_type: str | None = None
    capacity: int | None = None
    lf_line: int | None = None
    lf_center: int | None = None
    center: int | None = None
    rf_center: int | None = None
    rf_line: int | None = None
    timezone: str | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class RawPlayer:
    id: int
    full_name: str
    primary_position: str | None = None
    position_type: str | None = None
    bat_side: str | None = None
    pitch_hand: str | None = None
    birth_date: date | None = None
    mlb_debut_date: date | None = None
    height_in: int | None = None
    weight_lb: int | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class RawGame:
    id: int
    season: int
    game_type: str
    game_date_utc: datetime
    official_date: date
    home_team_id: int
    away_team_id: int
    venue_id: int | None
    status_abstract: str | None = None
    status_detailed: str | None = None
    status_code: str | None = None
    game_guid: str | None = None
    day_night: str | None = None
    doubleheader: str | None = None
    game_number: int | None = None
    series_game_number: int | None = None
    games_in_series: int | None = None
    scheduled_innings: int | None = None
    home_score: int | None = None
    away_score: int | None = None
    is_final: bool = False
    innings_played: int | None = None
    home_probable_pitcher_id: int | None = None
    away_probable_pitcher_id: int | None = None
    home_record_wins: int | None = None
    home_record_losses: int | None = None
    away_record_wins: int | None = None
    away_record_losses: int | None = None
    weather_condition: str | None = None
    weather_temp_f: int | None = None
    weather_wind: str | None = None

    @property
    def home_win(self) -> bool | None:
        if not self.is_final or self.home_score is None or self.away_score is None:
            return None
        return self.home_score > self.away_score


@dataclass(frozen=True, slots=True)
class RawTeamGameLine:
    game_id: int
    team_id: int
    opponent_team_id: int
    is_home: bool
    batting: dict[str, Any]
    pitching: dict[str, Any]
    fielding: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RawPlayerGameLine:
    game_id: int
    player_id: int
    team_id: int
    opponent_team_id: int
    is_home: bool
    role: str  # 'batter' | 'pitcher'
    position: str | None
    batting_order: int | None
    is_starter: bool
    stats: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RawBoxscore:
    game_id: int
    team_lines: list[RawTeamGameLine]
    player_lines: list[RawPlayerGameLine]
    officials: list[dict[str, Any]]
    lineups: list[dict[str, Any]]
    innings_played: int | None = None
    game_end_utc: datetime | None = None


@dataclass(frozen=True, slots=True)
class RawLineupEntry:
    game_id: int
    team_id: int
    player_id: int
    batting_order: int
    position: str | None
    is_confirmed: bool


@dataclass(frozen=True, slots=True)
class RawWeatherObservation:
    game_id: int
    venue_id: int | None
    observation_type: str
    valid_at: datetime | None
    temperature_f: float | None = None
    wind_speed_mph: float | None = None
    wind_direction_deg: int | None = None
    wind_direction_text: str | None = None
    humidity_pct: float | None = None
    pressure_mb: float | None = None
    precipitation_prob: float | None = None
    condition: str | None = None


@dataclass(frozen=True, slots=True)
class RawOddsSnapshot:
    game_id: int
    book: str
    market: str
    snapshot_at: datetime
    home_price: int | None
    away_price: int | None
    is_closing: bool = False


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class ReferenceProvider(Protocol):
    name: str

    def fetch_teams(self, season: int) -> ProviderResult[list[RawTeam]]: ...
    def fetch_venues(self, season: int) -> ProviderResult[list[RawVenue]]: ...
    def fetch_people(self, player_ids: list[int]) -> ProviderResult[list[RawPlayer]]: ...


@runtime_checkable
class ScheduleProvider(Protocol):
    name: str

    def fetch_schedule(self, start: date, end: date) -> ProviderResult[list[RawGame]]: ...


@runtime_checkable
class ResultsProvider(Protocol):
    name: str

    def fetch_boxscore(self, game_id: int) -> ProviderResult[RawBoxscore]: ...


@runtime_checkable
class LineupProvider(Protocol):
    name: str

    def fetch_lineup(self, game_id: int) -> ProviderResult[list[RawLineupEntry]]: ...


@runtime_checkable
class WeatherProvider(Protocol):
    name: str

    def fetch_forecast(
        self, game_id: int, venue_id: int, first_pitch_utc: datetime
    ) -> ProviderResult[RawWeatherObservation]: ...


@runtime_checkable
class StatcastProvider(Protocol):
    name: str

    def fetch_pitch_events(self, start: date, end: date) -> ProviderResult[list[dict[str, Any]]]: ...


@runtime_checkable
class InjuryProvider(Protocol):
    name: str

    def fetch_injuries(self, as_of: datetime) -> ProviderResult[list[dict[str, Any]]]: ...


@runtime_checkable
class ParkFactorProvider(Protocol):
    name: str

    def fetch_park_factors(self, season: int) -> ProviderResult[list[dict[str, Any]]]: ...


@runtime_checkable
class OddsProvider(Protocol):
    name: str

    def fetch_odds_snapshot(
        self, game_ids: list[int], as_of: datetime
    ) -> ProviderResult[list[RawOddsSnapshot]]: ...


__all__ = [
    "ProviderStatus", "DataCategory", "ProviderResult",
    "RawTeam", "RawVenue", "RawPlayer", "RawGame", "RawBoxscore",
    "RawTeamGameLine", "RawPlayerGameLine", "RawLineupEntry",
    "RawWeatherObservation", "RawOddsSnapshot",
    "ReferenceProvider", "ScheduleProvider", "ResultsProvider", "LineupProvider",
    "WeatherProvider", "StatcastProvider", "InjuryProvider", "ParkFactorProvider",
    "OddsProvider",
]
