"""
Weather Feed Agent

Polls NOAA NWS every WEATHER_POLL_MINUTES for all monitored stations
and publishes fresh WeatherObservation events to the queue.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ..core.config import WeatherConfig
from ..core.models import WeatherObservation
from ..core.nws_client import fetch_latest_observation
from ..core.stations import MONITORED_STATIONS

logger = logging.getLogger(__name__)


class WeatherFeedAgent:
    def __init__(self, obs_queue: asyncio.Queue[WeatherObservation], config: WeatherConfig) -> None:
        self._queue = obs_queue
        self._config = config

    async def run(self) -> None:
        logger.info("WeatherFeedAgent started | stations=%d", len(MONITORED_STATIONS))
        while True:
            await self._poll_all_stations()
            await asyncio.sleep(self._config.poll_minutes * 60)

    async def _poll_all_stations(self) -> None:
        max_age = timedelta(minutes=self._config.max_observation_age_minutes)
        now = datetime.now(tz=timezone.utc)

        for city, station_id in MONITORED_STATIONS.items():
            obs = await fetch_latest_observation(station_id, city)
            if obs is None:
                continue

            if now - obs.observed_at > max_age:
                logger.debug("WeatherFeedAgent: stale observation for %s (%s ago)", city,
                             str(now - obs.observed_at).split(".")[0])
                continue

            logger.debug(
                "WeatherFeedAgent: %s — temp=%.1f°F precip=%.3fin",
                city,
                obs.temperature_f if obs.temperature_f is not None else float("nan"),
                obs.precipitation_in if obs.precipitation_in is not None else 0.0,
            )
            await self._queue.put(obs)
