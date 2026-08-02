"""Open-Meteo weather provider. No API key, so nothing here is gated on one."""

from app.providers.open_meteo.provider import OpenMeteoWeatherProvider

__all__ = ["OpenMeteoWeatherProvider"]
