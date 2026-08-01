"""Explicit unavailable providers.

A category with no configured provider resolves to one of these. It returns
``UNAVAILABLE`` with the name of the environment variable that would enable it.
That message is what the UI displays. There is never a silent substitution and
never a synthetic value (ARCHITECTURE.md §2 rule 2).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.providers.base import DataCategory, ProviderResult


class _UnavailableProvider:
    category: DataCategory = DataCategory.REFERENCE
    env_var: str = "PROVIDER"
    what: str = "data"

    def __init__(self, env_var: str | None = None) -> None:
        self.name = f"unavailable::{self.category}"
        if env_var:
            self.env_var = env_var

    def _unavailable(self, **params: Any) -> ProviderResult[Any]:
        return ProviderResult.unavailable(
            self.name,
            self.category,
            f"No {self.what} provider configured. Set {self.env_var} to enable this "
            f"category. Until then {self.what} is reported as unavailable rather "
            f"than estimated.",
            request_params=params,
        )


class UnavailableLineupProvider(_UnavailableProvider):
    category = DataCategory.LINEUPS
    env_var = "LINEUP_PROVIDER"
    what = "pregame lineup"

    def fetch_lineup(self, game_id: int) -> ProviderResult[Any]:
        return self._unavailable(game_id=game_id)


class UnavailableWeatherProvider(_UnavailableProvider):
    category = DataCategory.WEATHER
    env_var = "WEATHER_PROVIDER"
    what = "weather forecast"

    def fetch_forecast(
        self, game_id: int, venue_id: int, first_pitch_utc: datetime
    ) -> ProviderResult[Any]:
        return self._unavailable(game_id=game_id, venue_id=venue_id)


class UnavailableStatcastProvider(_UnavailableProvider):
    category = DataCategory.STATCAST
    env_var = "STATCAST_PROVIDER"
    what = "Statcast"

    def fetch_pitch_events(self, start: date, end: date) -> ProviderResult[Any]:
        return self._unavailable(start=start.isoformat(), end=end.isoformat())


class UnavailableInjuryProvider(_UnavailableProvider):
    category = DataCategory.INJURIES
    env_var = "INJURY_PROVIDER"
    what = "injury"

    def fetch_injuries(self, as_of: datetime) -> ProviderResult[Any]:
        return self._unavailable(as_of=as_of.isoformat())


class UnavailableParkFactorProvider(_UnavailableProvider):
    category = DataCategory.PARK_FACTORS
    env_var = "PARK_FACTOR_PROVIDER"
    what = "park factor"

    def fetch_park_factors(self, season: int) -> ProviderResult[Any]:
        return self._unavailable(season=season)


class UnavailableOddsProvider(_UnavailableProvider):
    category = DataCategory.ODDS
    env_var = "ODDS_PROVIDER"
    what = "odds"

    def fetch_odds_snapshot(self, game_ids: list[int], as_of: datetime) -> ProviderResult[Any]:
        return self._unavailable(n_games=len(game_ids), as_of=as_of.isoformat())


class UnavailableBullpenAvailabilityProvider(_UnavailableProvider):
    category = DataCategory.BULLPEN_AVAILABILITY
    env_var = "BULLPEN_AVAILABILITY_PROVIDER"
    what = "bullpen availability"

    def fetch_availability(self, game_ids: list[int], as_of: datetime) -> ProviderResult[Any]:
        return self._unavailable(n_games=len(game_ids), as_of=as_of.isoformat())


__all__ = [
    "UnavailableLineupProvider",
    "UnavailableWeatherProvider",
    "UnavailableStatcastProvider",
    "UnavailableInjuryProvider",
    "UnavailableParkFactorProvider",
    "UnavailableOddsProvider",
    "UnavailableBullpenAvailabilityProvider",
]
