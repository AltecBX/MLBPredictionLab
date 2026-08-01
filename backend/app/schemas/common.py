"""Shared response DTOs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class RecordSplit(ApiModel):
    """A win-loss record and its percentage. `win_pct` is None, never .000,
    when nothing has been played — a zero there reads as a real bad record."""

    wins: int
    losses: int
    win_pct: float | None = None


class StreakGameRef(ApiModel):
    game_id: int
    date: str
    opponent: str
    opponent_id: int
    is_home: bool
    runs_for: int
    runs_against: int


class StreakSummary(ApiModel):
    kind: str  # 'W' | 'L'
    length: int
    label: str
    games: list[StreakGameRef] = []


class StandingSummary(ApiModel):
    division_name: str | None = None
    division_rank: int | None = None
    games_behind: float | None = None
    league_name: str | None = None
    league_rank: int | None = None
    wildcard_rank: int | None = None
    wildcard_games_behind: float | None = None
    in_playoff_position: bool = False
    elimination_number: int | None = None
    clinched_division: bool = False
    eliminated: bool = False


class TeamRef(ApiModel):
    id: int
    name: str
    abbreviation: str
    team_name: str | None = None
    location_name: str | None = None
    division_name: str | None = None
    wins: int | None = None
    losses: int | None = None
    # Derived from ingested results under the same as-of cut the model uses;
    # display context only, never a model input.
    home_record: RecordSplit | None = None
    away_record: RecordSplit | None = None
    streak: StreakSummary | None = None
    standing: StandingSummary | None = None

    @property
    def record(self) -> str | None:
        if self.wins is None or self.losses is None:
            return None
        return f"{self.wins}-{self.losses}"


class BallparkRef(ApiModel):
    id: int | None = None
    name: str | None = None
    city: str | None = None
    state: str | None = None
    roof_type: str | None = None
    elevation_ft: int | None = None
    lf_line: int | None = None
    center: int | None = None
    rf_line: int | None = None
    turf_type: str | None = None
    capacity: int | None = None
    timezone: str | None = None


class PitcherRef(ApiModel):
    id: int | None = None
    full_name: str | None = None
    pitch_hand: str | None = None
    status: str = "UNKNOWN"


class FreshnessEntry(ApiModel):
    category: str
    label: str
    status: str
    freshness: str
    last_success_at: datetime | None = None
    age_seconds: int | None = None
    provider: str | None = None
    detail: str | None = None
    records_last_run: int | None = None


class WarningEntry(ApiModel):
    code: str
    severity: str
    message: str


class Unavailable(ApiModel):
    """Explicit unavailable state. Never a zero, never a placeholder."""

    available: bool = False
    reason: str
    required_source: str | None = None
    phase: int | None = None


class SampleAnnotated(ApiModel):
    value: float | None = None
    sample_size: int | None = None
    is_estimated: bool = False
    display: str | None = None
