"""
NOAA National Weather Service (NWS) API client.

No API key required. Returns latest observations for a given station.
API docs: https://www.weather.gov/documentation/services-web-api

Endpoint: https://api.weather.gov/stations/{stationId}/observations/latest
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from .models import WeatherObservation

logger = logging.getLogger(__name__)

NWS_BASE = "https://api.weather.gov"
REQUEST_TIMEOUT = 10.0
# NWS requires a User-Agent header per their API policy
USER_AGENT = "kinzie-weather-strategy (nxd914@gmail.com)"

_CELSIUS_TO_FAHRENHEIT = lambda c: c * 9 / 5 + 32
_MS_TO_MPH = lambda ms: ms * 2.23694


async def fetch_latest_observation(station_id: str, city: str) -> Optional[WeatherObservation]:
    """
    Fetch the latest observation for a NWS station.
    Returns WeatherObservation or None on error / missing data.
    """
    url = f"{NWS_BASE}/stations/{station_id}/observations/latest"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    logger.warning("NWS API HTTP %d for station %s", resp.status, station_id)
                    return None
                body = await resp.json(content_type=None)
                return _parse_observation(body, station_id, city)
    except Exception as exc:
        logger.warning("NWS fetch failed (station=%s): %s", station_id, exc)
        return None


def _parse_observation(body: dict, station_id: str, city: str) -> Optional[WeatherObservation]:
    props = body.get("properties", {})
    if not props:
        return None

    # Observation timestamp
    timestamp_str = props.get("timestamp", "")
    try:
        observed_at = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        observed_at = datetime.now(tz=timezone.utc)

    # Temperature: NWS returns Celsius with unitCode "wmoUnit:degC"
    temp_c = _extract_value(props.get("temperature"))
    temperature_f = round(_CELSIUS_TO_FAHRENHEIT(temp_c), 1) if temp_c is not None else None

    # Precipitation (last hour, in meters → convert to inches)
    precip_m = _extract_value(props.get("precipitationLastHour"))
    precipitation_in = round(precip_m * 39.3701, 3) if precip_m is not None else None

    # Wind speed: m/s → mph
    wind_ms = _extract_value(props.get("windSpeed"))
    wind_speed_mph = round(_MS_TO_MPH(wind_ms), 1) if wind_ms is not None else None

    return WeatherObservation(
        station_id=station_id,
        city=city,
        observed_at=observed_at,
        temperature_f=temperature_f,
        precipitation_in=precipitation_in,
        wind_speed_mph=wind_speed_mph,
    )


def _extract_value(field: Optional[dict]) -> Optional[float]:
    if field is None:
        return None
    val = field.get("value")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
