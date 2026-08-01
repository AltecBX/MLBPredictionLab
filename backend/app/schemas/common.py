"""Shared response DTOs."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class TeamRef(ApiModel):
    id: int
    name: str
    abbreviation: str
    team_name: str | None = None
    location_name: str | None = None
    division_name: str | None = None
    wins: int | None = None
    losses: int | None = None

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
