"""
Economic Feed Agent

Watches the economic release calendar and publishes EconRelease events
when BLS actual data becomes available after a scheduled release.

Flow per event:
  1. Sleep until pre_release_window_minutes before scheduled time
  2. At scheduled time: begin polling BLS API every poll_interval_seconds
  3. When BLS returns the expected period → compute value, publish to queue
  4. Give up after post_release_timeout_minutes if data never appears
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..core.bls_client import get_cpi_yoy, get_nfp_change, get_ppi_mom
from ..core.calendar import upcoming_events
from ..core.config import EconConfig
from ..core.fred_client import get_fomc_rate
from ..core.models import EconEvent, EconEventType, EconRelease

logger = logging.getLogger(__name__)


class EconFeedAgent:
    def __init__(self, release_queue: asyncio.Queue[EconRelease], config: EconConfig) -> None:
        self._queue = release_queue
        self._config = config
        self._bls_api_key: Optional[str] = os.environ.get("BLS_API_KEY")
        self._fred_api_key: Optional[str] = os.environ.get("FRED_API_KEY")
        if not self._fred_api_key:
            logger.warning("FRED_API_KEY not set — FOMC data fetching disabled")
        self._dispatch = {
            EconEventType.CPI:  self._fetch_cpi,
            EconEventType.NFP:  self._fetch_nfp,
            EconEventType.PPI:  self._fetch_ppi,
            EconEventType.FOMC: self._fetch_fomc,
        }

    async def run(self) -> None:
        logger.info("EconFeedAgent started")
        while True:
            events = upcoming_events()
            if not events:
                logger.warning("EconFeedAgent: calendar exhausted — sleeping 24h (add 2027 schedule)")
                await asyncio.sleep(86400)
                continue

            next_event = events[0]
            pre_window = timedelta(minutes=self._config.pre_release_window_minutes)
            sleep_until = next_event.scheduled_utc - pre_window
            wait = (sleep_until - datetime.now(tz=timezone.utc)).total_seconds()

            if wait > 3600:
                logger.info(
                    "EconFeedAgent: next — %s at %s UTC (sleeping %dh)",
                    next_event.description,
                    next_event.scheduled_utc.strftime("%Y-%m-%d %H:%M"),
                    int(wait // 3600),
                )
                await asyncio.sleep(3600)  # re-check hourly so we pick up calendar changes
                continue

            if wait > 0:
                logger.info(
                    "EconFeedAgent: %s in %.1f min — standing by",
                    next_event.description,
                    wait / 60,
                )
                await asyncio.sleep(wait)

            await self._watch_release(next_event)

    async def _watch_release(self, event: EconEvent) -> None:
        # Sleep exactly to scheduled time (we're already in the pre-release window)
        wait = (event.scheduled_utc - datetime.now(tz=timezone.utc)).total_seconds()
        if wait > 0:
            logger.info("EconFeedAgent: %s — waiting %.0fs for release", event.description, wait)
            await asyncio.sleep(wait)

        deadline = event.scheduled_utc + timedelta(minutes=self._config.post_release_timeout_minutes)
        expected_year, expected_period = event.expected_bls_period()
        logger.info(
            "EconFeedAgent: polling for %s (BLS period %s-%s)",
            event.description, expected_year, expected_period,
        )

        while datetime.now(tz=timezone.utc) < deadline:
            actual = await self._fetch_actual(event, expected_year, expected_period)
            if actual is not None:
                release = EconRelease(
                    event=event,
                    actual=actual,
                    prior=None,
                    released_at=datetime.now(tz=timezone.utc),
                )
                logger.info("EconFeedAgent: %s — actual=%.2f", event.description, actual)
                await self._queue.put(release)
                return
            await asyncio.sleep(self._config.poll_interval_seconds)

        logger.warning(
            "EconFeedAgent: timed out after %.0f min waiting for %s",
            self._config.post_release_timeout_minutes,
            event.description,
        )

    async def _fetch_cpi(self, year: str, period: str) -> Optional[float]:
        return await get_cpi_yoy(year, period, self._bls_api_key)

    async def _fetch_nfp(self, year: str, period: str) -> Optional[float]:
        return await get_nfp_change(year, period, self._bls_api_key)

    async def _fetch_ppi(self, year: str, period: str) -> Optional[float]:
        return await get_ppi_mom(year, period, self._bls_api_key)

    async def _fetch_fomc(self, year: str, period: str) -> Optional[float]:  # noqa: ARG002
        return await get_fomc_rate(self._fred_api_key)

    async def _fetch_actual(
        self,
        event: EconEvent,
        expected_year: str,
        expected_period: str,
    ) -> Optional[float]:
        handler = self._dispatch.get(event.event_type)
        if handler is None:
            logger.debug("EconFeedAgent: no data source for %s", event.event_type.value)
            return None
        return await handler(expected_year, expected_period)
