"""
Sports Latency Arb Strategy Daemon

Monitors live ESPN scoreboards, detects finalized games, and trades Kalshi
sports winner markets that haven't yet priced in the known outcome.

Usage:
  python3 -m strategies.sports.daemon

Environment variables:
  EXECUTION_MODE      paper (default) | live
  BANKROLL_USDC       position sizing bankroll (default: 10000)
  KALSHI_API_KEY      required for Kalshi API access
  KALSHI_PRIVATE_KEY_PATH  required for Kalshi API signing

Sports-specific (optional):
  SPORTS_LEAGUES      comma-separated leagues (default: NFL,NBA,MLB)
  SPORTS_MIN_EDGE     minimum edge threshold (default: 0.08)
  SPORTS_POLL_SECONDS polling interval during active games (default: 30)
  SPORTS_IDLE_SECONDS polling interval when no games in progress (default: 3600)
  SPORTS_MAX_POSITIONS max concurrent open positions (default: 5)
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
from strategies.sports.agents.sports_feed_agent import SportsFeedAgent
from strategies.sports.agents.sports_scanner_agent import SportsScannerAgent
from strategies.sports.core.config import SportsConfig
from strategies.sports.core.models import GameResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("sports.daemon")

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
    config = SportsConfig.from_env()
    env = resolve_environment()
    log_environment_banner(env)

    logger.info(
        "Sports daemon starting | mode=%s bankroll=%.0f leagues=%s min_edge=%.2f",
        env.label, config.bankroll_usdc, ",".join(config.leagues), config.min_edge,
    )

    result_queue: asyncio.Queue[GameResult] = asyncio.Queue(maxsize=50)

    feed = SportsFeedAgent(result_queue=result_queue, config=config)
    scanner = SportsScannerAgent(result_queue=result_queue, config=config, environment=env)

    tasks = [
        asyncio.create_task(_guarded(feed.run(), "sports_feed"), name="sports_feed"),
        asyncio.create_task(_guarded(scanner.run(), "sports_scanner"), name="sports_scanner"),
    ]

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: [t.cancel() for t in tasks])

    logger.info("Sports daemon running (%d tasks). Ctrl+C to stop.", len(tasks))
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
    logger.info("Sports daemon stopped.")


if __name__ == "__main__":
    asyncio.run(main())
