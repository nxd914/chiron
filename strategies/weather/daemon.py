"""
Weather Latency Arb Strategy Daemon

Continuously monitors NOAA NWS stations and trades Kalshi weather markets
that haven't yet priced in a confirmed temperature reading.

Usage:
  python3 -m strategies.weather.daemon

Environment variables:
  EXECUTION_MODE      paper (default) | live
  BANKROLL_USDC       position sizing bankroll (default: 10000)
  KALSHI_API_KEY      required for Kalshi API access
  KALSHI_PRIVATE_KEY_PATH  required for Kalshi API signing

Weather-specific (optional):
  WEATHER_MIN_EDGE          minimum edge threshold (default: 0.08)
  WEATHER_POLL_MINUTES      NWS polling interval in minutes (default: 15)
  WEATHER_MAX_OBS_AGE_MINUTES  max observation age to act on (default: 60)
  WEATHER_MAX_POSITIONS     max concurrent open positions (default: 5)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Awaitable

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.environment import log_environment_banner, resolve_environment
from strategies.weather.agents.weather_feed_agent import WeatherFeedAgent
from strategies.weather.agents.weather_scanner_agent import WeatherScannerAgent
from strategies.weather.core.config import WeatherConfig
from strategies.weather.core.models import WeatherObservation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("weather.daemon")

_SHUTDOWN_TIMEOUT = 10.0


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(_REPO_ROOT / ".env", override=False)
    load_dotenv(override=False)


async def _guarded(coro: Awaitable, name: str) -> None:
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("Agent %s crashed: %s", name, exc, exc_info=True)
        raise


async def main() -> None:
    _load_dotenv()
    config = WeatherConfig.from_env()
    env = resolve_environment()
    log_environment_banner(env)

    logger.info(
        "Weather daemon starting | mode=%s bankroll=%.0f poll_minutes=%d min_edge=%.2f",
        env.label, config.bankroll_usdc, config.poll_minutes, config.min_edge,
    )

    obs_queue: asyncio.Queue[WeatherObservation] = asyncio.Queue(maxsize=100)

    feed = WeatherFeedAgent(obs_queue=obs_queue, config=config)
    scanner = WeatherScannerAgent(obs_queue=obs_queue, config=config, environment=env)

    tasks = [
        asyncio.create_task(_guarded(feed.run(), "weather_feed"), name="weather_feed"),
        asyncio.create_task(_guarded(scanner.run(), "weather_scanner"), name="weather_scanner"),
    ]

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: [t.cancel() for t in tasks])

    logger.info("Weather daemon running (%d tasks). Ctrl+C to stop.", len(tasks))
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Shutdown signal received.")
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=_SHUTDOWN_TIMEOUT,
            )
        except asyncio.TimeoutError:
            still = [t.get_name() for t in tasks if not t.done()]
            logger.warning("Shutdown timed out — tasks still running: %s", still)
    logger.info("Weather daemon stopped.")


if __name__ == "__main__":
    asyncio.run(main())
