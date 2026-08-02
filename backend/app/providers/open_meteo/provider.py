"""Forecast weather from Open-Meteo.

WEATHER_PROVIDER was documented as blocked on a paid account. It is not:
Open-Meteo serves forecasts without an API key, and it serves an *archive of
past forecasts* as well, which is what makes a weather feature measurable rather
than merely servable.

Two endpoints, and the difference between them is the leakage story.

**Live.** `api.open-meteo.com` returns the forecast as it stands now. A row
written from it carries `knowledge_time = retrieved_at`, because that is
literally when the forecast came into existence for us. Nothing about that can
leak: the forecast for tonight's game is fetched before tonight's game.

**Backfill.** `historical-forecast-api.open-meteo.com` returns archived
forecasts for past dates, which is the only way to put weather in front of a
walk-forward. The honest caveat, stated here because it belongs in the code
rather than in a footnote: **the archive does not expose which model run each
value came from.** A value for a 7pm first pitch may come from a run initialised
that afternoon — later, and therefore more accurate, than the forecast that
actually existed at T−3h.

That biases backfilled weather *optimistically*. The consequence is asymmetric
and worth being explicit about, because it decides how the eventual measurement
should be read:

* If weather features **fail** on this data, that is a strong result. They
  failed with better information than production will ever have.
* If they **succeed**, the size of the effect is an upper bound and needs
  confirming against forecasts collected live before anything is served.

`knowledge_time` for a backfilled row is set to midnight UTC on the game's own
date — a time by which a forecast for that evening certainly existed, and which
is before first pitch for every game in this database.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from app.core.logging import get_logger
from app.providers.base import (
    DataCategory,
    ProviderResult,
    ProviderStatus,
    RawWeatherObservation,
)

log = get_logger(__name__)

SOURCE_NAME = "open_meteo"

LIVE_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

HOURLY_FIELDS = (
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "surface_pressure",
    "precipitation_probability",
    "precipitation",
    "weather_code",
)

#: Open-Meteo asks for courtesy rather than enforcing a hard limit on the free
#: tier. One request every 200ms is well inside it and keeps a full backfill —
#: thirty venues by four seasons — to a couple of minutes.
MIN_INTERVAL_S = 0.2
TIMEOUT_S = 30.0
MAX_RETRIES = 3

#: WMO weather codes, collapsed to the words a reader uses. The full table is
#: 28 entries of drizzle gradations that no baseball feature distinguishes.
WMO_CONDITIONS = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
    61: "Rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
    67: "Freezing rain", 71: "Snow", 73: "Snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Rain showers", 82: "Heavy showers",
    95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
}


def condition_text(code: Any) -> str | None:
    try:
        return WMO_CONDITIONS.get(int(code))
    except (TypeError, ValueError):
        return None


class OpenMeteoClient:
    """Rate-limited transport. Parsing stays in the provider."""

    def __init__(self, timeout_s: float = TIMEOUT_S, min_interval_s: float = MIN_INTERVAL_S):
        self.timeout_s = timeout_s
        self.min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last_request_at = 0.0
        self._client = httpx.Client(
            timeout=timeout_s,
            headers={"User-Agent": "JerryMLBPredictionLab/1.0 (analytics)"},
            follow_redirects=True,
        )
        self.request_count = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenMeteoClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.min_interval_s:
                time.sleep(self.min_interval_s - elapsed)
            self._last_request_at = time.monotonic()

    def get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self._throttle()
            self.request_count += 1
            try:
                response = self._client.get(url, params=params)
                if response.status_code >= 500 or response.status_code == 429:
                    raise httpx.HTTPError(f"retryable status {response.status_code}")
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001 - retried, then surfaced
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2**attempt * 0.5)
                    log.info("open_meteo.retry", attempt=attempt + 1, error=str(exc))
        raise RuntimeError(f"Open-Meteo request failed after {MAX_RETRIES} attempts: {last_error}")


def _common_params(latitude: float, longitude: float) -> dict[str, Any]:
    return {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join(HOURLY_FIELDS),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "mm",
        "timezone": "GMT",
    }


class HourlyForecast:
    """Hour-indexed forecast for one venue, queried by timestamp."""

    def __init__(self, payload: dict[str, Any]) -> None:
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        self._by_hour: dict[datetime, dict[str, Any]] = {}
        for index, stamp in enumerate(times):
            try:
                moment = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
            except ValueError:
                continue
            self._by_hour[moment] = {
                field: (hourly.get(field) or [None] * len(times))[index]
                for field in HOURLY_FIELDS
            }

    def at(self, moment: datetime) -> dict[str, Any] | None:
        """The forecast for the hour containing ``moment``.

        Rounded down rather than to nearest: a 7:05pm first pitch is played in
        the 7pm hour, and the hour it is closest to is not the hour it is in.
        """
        key = moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        return self._by_hour.get(key)

    def __len__(self) -> int:
        return len(self._by_hour)


class OpenMeteoWeatherProvider:
    """Implements the WeatherProvider protocol, plus a bulk path for backfill."""

    name = SOURCE_NAME
    category = DataCategory.WEATHER

    def __init__(self, client: OpenMeteoClient | None = None) -> None:
        self._client = client or OpenMeteoClient()

    # -- the protocol ------------------------------------------------------
    def fetch_forecast(
        self,
        game_id: int,
        venue_id: int,
        first_pitch_utc: datetime,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> ProviderResult[RawWeatherObservation]:
        """One game's forecast at first pitch.

        Latitude and longitude are passed in rather than looked up: this layer
        does not touch the database, which is what keeps the providers testable
        against recorded payloads.
        """
        if latitude is None or longitude is None:
            return ProviderResult.unavailable(
                SOURCE_NAME,
                DataCategory.WEATHER,
                "The ballpark has no latitude and longitude on record, so no "
                "forecast can be located for it.",
                request_params={"game_id": game_id, "venue_id": venue_id},
            )
        day = first_pitch_utc.astimezone(UTC).date()
        params = _common_params(latitude, longitude) | {
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
        }
        payload = self._client.get(LIVE_URL, params)
        forecast = HourlyForecast(payload)
        row = forecast.at(first_pitch_utc)
        if row is None:
            return ProviderResult.unavailable(
                SOURCE_NAME,
                DataCategory.WEATHER,
                f"Open-Meteo returned no hour covering {first_pitch_utc.isoformat()}.",
                endpoint=LIVE_URL,
                request_params={"game_id": game_id},
            )
        return ProviderResult(
            source_name=SOURCE_NAME,
            category=DataCategory.WEATHER,
            status=ProviderStatus.OK,
            data=_observation(game_id, venue_id, first_pitch_utc, row),
            raw=payload,
        )

    # -- backfill ----------------------------------------------------------
    def fetch_range(
        self,
        latitude: float,
        longitude: float,
        start: date,
        end: date,
        archived: bool = True,
    ) -> HourlyForecast:
        """Every hour between two dates for one venue, in one request.

        Backfilling game by game would be ten thousand requests for four
        seasons; by venue and date range it is a few hundred. The join back to
        games happens on the hour of first pitch.
        """
        params = _common_params(latitude, longitude) | {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        url = ARCHIVE_URL if archived else LIVE_URL
        return HourlyForecast(self._client.get(url, params))

    def close(self) -> None:
        self._client.close()


def _observation(
    game_id: int, venue_id: int | None, valid_at: datetime, row: dict[str, Any]
) -> RawWeatherObservation:
    return RawWeatherObservation(
        game_id=game_id,
        venue_id=venue_id,
        observation_type="FORECAST",
        valid_at=valid_at,
        temperature_f=_number(row.get("temperature_2m")),
        wind_speed_mph=_number(row.get("wind_speed_10m")),
        wind_direction_deg=_integer(row.get("wind_direction_10m")),
        wind_direction_text=_compass(row.get("wind_direction_10m")),
        humidity_pct=_number(row.get("relative_humidity_2m")),
        pressure_mb=_number(row.get("surface_pressure")),
        precipitation_prob=_number(row.get("precipitation_probability")),
        condition=condition_text(row.get("weather_code")),
    )


COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def _compass(degrees: Any) -> str | None:
    value = _number(degrees)
    if value is None:
        return None
    return COMPASS[int((value % 360) / 22.5 + 0.5) % 16]


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(round(number))


def backfill_knowledge_time(game_date: date) -> datetime:
    """When a backfilled forecast is treated as having become knowable.

    Midnight UTC on the game's own date. A forecast for that evening certainly
    existed by then, and it precedes first pitch for every game in this
    database — so the as-of filter cannot admit a value the day could not have
    had. It does not fix the accuracy caveat in the module docstring, which is
    about which model run the archive drew from, not about timing.
    """
    return datetime(game_date.year, game_date.month, game_date.day, tzinfo=UTC)


def next_day(day: date) -> date:
    return day + timedelta(days=1)


__all__ = [
    "ARCHIVE_URL",
    "LIVE_URL",
    "SOURCE_NAME",
    "HourlyForecast",
    "OpenMeteoClient",
    "OpenMeteoWeatherProvider",
    "backfill_knowledge_time",
    "condition_text",
]
