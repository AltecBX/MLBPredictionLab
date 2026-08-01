"""Provider selection by environment variable.

Adding a source means implementing the Protocol and registering it here — no
other file changes (DATA_SOURCES.md §8).
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.errors import ConfigurationError
from app.providers.base import DataCategory
from app.providers.baseball_savant.provider import BaseballSavantProvider
from app.providers.mlb_statsapi.provider import MlbStatsApiProvider
from app.providers.unavailable import (
    UnavailableBullpenAvailabilityProvider,
    UnavailableInjuryProvider,
    UnavailableLineupProvider,
    UnavailableOddsProvider,
    UnavailableParkFactorProvider,
    UnavailableStatcastProvider,
    UnavailableWeatherProvider,
)

_IMPLEMENTATIONS: dict[str, type] = {
    "mlb_statsapi": MlbStatsApiProvider,
    "baseball_savant": BaseballSavantProvider,
}

_shared: dict[str, Any] = {}


def _resolve(configured: str | None, unavailable_cls: type) -> Any:
    if not configured:
        return unavailable_cls()
    impl = _IMPLEMENTATIONS.get(configured)
    if impl is None:
        raise ConfigurationError(
            f"Unknown provider {configured!r}. Registered providers: "
            f"{sorted(_IMPLEMENTATIONS)}"
        )
    if configured not in _shared:
        _shared[configured] = impl()
    return _shared[configured]


def _require(configured: str | None, category: DataCategory) -> Any:
    """Resolve a provider for a category that has no acceptable unavailable state."""
    if not configured:
        raise ConfigurationError(
            f"No provider configured for {category}. This category is required; "
            f"the application cannot serve predictions without it."
        )
    impl = _IMPLEMENTATIONS.get(configured)
    if impl is None:
        raise ConfigurationError(
            f"Unknown provider {configured!r} for {category}. Registered providers: "
            f"{sorted(_IMPLEMENTATIONS)}"
        )
    if configured not in _shared:
        _shared[configured] = impl()
    return _shared[configured]


def get_reference_provider() -> Any:
    return _require(settings.reference_provider, DataCategory.REFERENCE)


def get_schedule_provider() -> Any:
    return _require(settings.schedule_provider, DataCategory.SCHEDULE)


def get_results_provider() -> Any:
    return _require(settings.results_provider, DataCategory.RESULTS)


def get_lineup_provider() -> Any:
    return _resolve(settings.lineup_provider, UnavailableLineupProvider)


def get_weather_provider() -> Any:
    return _resolve(settings.weather_provider, UnavailableWeatherProvider)


def get_statcast_provider() -> Any:
    return _resolve(settings.statcast_provider, UnavailableStatcastProvider)


def get_injury_provider() -> Any:
    return _resolve(settings.injury_provider, UnavailableInjuryProvider)


def get_park_factor_provider() -> Any:
    return _resolve(settings.park_factor_provider, UnavailableParkFactorProvider)


def get_odds_provider() -> Any:
    return _resolve(settings.odds_provider, UnavailableOddsProvider)


def get_bullpen_availability_provider() -> Any:
    return _resolve(None, UnavailableBullpenAvailabilityProvider)


def configured_categories() -> dict[str, str | None]:
    """Category -> configured provider name (None means explicitly unavailable)."""
    return {
        DataCategory.REFERENCE: settings.reference_provider,
        DataCategory.SCHEDULE: settings.schedule_provider,
        DataCategory.RESULTS: settings.results_provider,
        DataCategory.PROBABLE_PITCHERS: settings.schedule_provider,
        DataCategory.PLAYER_STATS: settings.results_provider,
        DataCategory.BULLPEN_USAGE: settings.results_provider,
        DataCategory.LINEUPS: settings.lineup_provider,
        DataCategory.WEATHER: settings.weather_provider,
        DataCategory.STATCAST: settings.statcast_provider,
        DataCategory.INJURIES: settings.injury_provider,
        DataCategory.PARK_FACTORS: settings.park_factor_provider,
        DataCategory.ODDS: settings.odds_provider,
        DataCategory.BULLPEN_AVAILABILITY: None,
    }


def reset_cache() -> None:
    """Test hook."""
    _shared.clear()
