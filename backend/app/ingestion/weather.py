"""Weather ingestion: forecasts at first pitch, live and backfilled.

Two paths, and they differ in exactly one thing that matters — when the row
became knowable.

**Live** fetches the forecast for upcoming games now, so `knowledge_time` is the
moment of retrieval. That is unambiguous and cannot leak.

**Backfill** walks venue by venue rather than game by game. Four seasons of
games is over ten thousand requests one at a time; by venue and season it is a
few hundred, because one request returns every hour in a date range and the
games join back on the hour of their own first pitch. `knowledge_time` is
midnight UTC on the game's date — see `provider.backfill_knowledge_time` for why
that is the conservative choice and what it does *not* fix.

Derived quantities are computed here rather than at feature time: air density
and field-relative wind are properties of the observation, and a feature that
recomputed them would be able to drift from what was stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.db.models import Ballpark, Game, Weather
from app.db.upsert import upsert
from app.features.weather_physics import (
    air_density_kg_m3,
    field_relative_wind,
    is_enclosed,
)
from app.ingestion.status import job_run
from app.providers.open_meteo.provider import (
    SOURCE_NAME,
    HourlyForecast,
    OpenMeteoWeatherProvider,
    backfill_knowledge_time,
)

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VenueRef:
    id: int
    latitude: float
    longitude: float
    azimuth: float | None
    roof_type: str | None


def _venues(session: Session) -> dict[int, VenueRef]:
    rows = session.execute(
        select(
            Ballpark.id, Ballpark.latitude, Ballpark.longitude,
            Ballpark.azimuth_angle, Ballpark.roof_type,
        ).where(Ballpark.latitude.is_not(None), Ballpark.longitude.is_not(None))
    ).all()
    return {
        r.id: VenueRef(
            id=r.id,
            latitude=float(r.latitude),
            longitude=float(r.longitude),
            azimuth=None if r.azimuth_angle is None else float(r.azimuth_angle),
            roof_type=r.roof_type,
        )
        for r in rows
    }


def _weather_row(
    game_id: int,
    venue: VenueRef,
    first_pitch: datetime,
    hour: dict[str, Any],
    knowledge_time: datetime,
    retrieved_at: datetime,
) -> dict[str, Any]:
    temperature = _number(hour.get("temperature_2m"))
    pressure = _number(hour.get("surface_pressure"))
    humidity = _number(hour.get("relative_humidity_2m"))
    speed = _number(hour.get("wind_speed_10m"))
    direction = _number(hour.get("wind_direction_10m"))

    wind = field_relative_wind(speed, direction, venue.azimuth)
    density = air_density_kg_m3(temperature, pressure, humidity)

    return {
        "game_id": game_id,
        "venue_id": venue.id,
        "observation_type": "FORECAST",
        "valid_at": first_pitch,
        "temperature_f": temperature,
        "wind_speed_mph": speed,
        "wind_direction_deg": None if direction is None else int(round(direction)),
        # None rather than a neutral string when the venue's orientation is
        # unknown: a wind called neutral because nobody knows which way the
        # stadium faces is an invented fact.
        "wind_field_relative": None if wind is None else wind.label,
        "humidity_pct": humidity,
        "pressure_mb": pressure,
        "precipitation_prob": _number(hour.get("precipitation_probability")),
        "precipitation_mm": _number(hour.get("precipitation")),
        "air_density_kg_m3": None if density is None else round(density, 4),
        "roof_status": "ENCLOSED" if is_enclosed(venue.roof_type) else "OPEN",
        # A forecast is a forecast. Every row from this source is an estimate,
        # and the flag says so rather than leaving a reader to infer it.
        "is_estimated": True,
        "source_name": SOURCE_NAME,
        "retrieved_at": retrieved_at,
        "knowledge_time": knowledge_time,
    }


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def ingest_weather_for_dates(
    session: Session,
    start: date,
    end: date,
    archived: bool = True,
    provider: OpenMeteoWeatherProvider | None = None,
) -> int:
    """Forecasts at first pitch for every game between two dates.

    One request per venue per date range rather than one per game. Games at a
    venue with no coordinates on record are skipped and counted, never written
    with a guessed location.
    """
    owned = provider is None
    provider = provider or OpenMeteoWeatherProvider()
    written = 0

    try:
        with job_run(
            session, "ingest_weather", start=start.isoformat(), end=end.isoformat()
        ) as run:
            venues = _venues(session)
            games = session.execute(
                select(Game.id, Game.venue_id, Game.game_date_utc, Game.official_date)
                .where(Game.official_date >= start, Game.official_date <= end)
                .order_by(Game.game_date_utc)
            ).all()
            if not games:
                log.info("weather.no_games", start=start.isoformat(), end=end.isoformat())
                return 0

            by_venue: dict[int, list[Any]] = {}
            skipped_no_venue = 0
            for game in games:
                venue = venues.get(game.venue_id) if game.venue_id else None
                if venue is None:
                    skipped_no_venue += 1
                    continue
                by_venue.setdefault(venue.id, []).append(game)

            retrieved_at = utcnow()
            for venue_id, venue_games in by_venue.items():
                venue = venues[venue_id]
                first = min(g.official_date for g in venue_games)
                last = max(g.official_date for g in venue_games)
                try:
                    forecast: HourlyForecast = provider.fetch_range(
                        venue.latitude, venue.longitude, first, last, archived=archived
                    )
                except Exception as exc:  # noqa: BLE001 - one venue must not stop the run
                    log.warning("weather.venue_failed", venue_id=venue_id, error=str(exc))
                    continue

                rows = []
                for game in venue_games:
                    first_pitch = game.game_date_utc.astimezone(UTC)
                    hour = forecast.at(first_pitch)
                    if hour is None:
                        continue
                    knowledge_time = (
                        backfill_knowledge_time(game.official_date)
                        if archived
                        else retrieved_at
                    )
                    rows.append(
                        _weather_row(
                            game.id, venue, first_pitch, hour, knowledge_time, retrieved_at
                        )
                    )
                if rows:
                    upsert(
                        session, Weather, rows,
                        ["game_id", "observation_type", "knowledge_time"],
                    )
                    written += len(rows)

            run.rows_written = written
            log.info(
                "weather.ingested",
                written=written,
                venues=len(by_venue),
                skipped_no_venue=skipped_no_venue,
                archived=archived,
            )
    finally:
        if owned:
            provider.close()
    return written


__all__ = ["ingest_weather_for_dates"]
