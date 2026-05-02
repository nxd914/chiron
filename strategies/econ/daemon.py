"""
Economic Data Strategy Daemon

Standalone process — runs completely independently from the crypto pipeline.
Monitors the economic release calendar, fetches BLS data at release time,
and trades Kalshi economic event markets when the actual diverges from price.

Usage:
  python3 -m strategies.econ.daemon

Environment variables (shared with crypto daemon):
  EXECUTION_MODE         paper (default) | live
  BANKROLL_USDC          position sizing bankroll (default: 10000)
  KALSHI_API_KEY         required for Kalshi API access
  KALSHI_PRIVATE_KEY_PATH  required for Kalshi API signing

Econ-specific (optional):
  BLS_API_KEY            free BLS API key for higher rate limits
  ECON_MIN_EDGE          minimum edge threshold (default: 0.08)
  ECON_TIMEOUT_MINUTES   BLS polling timeout after release (default: 5)
  ECON_POLL_SECONDS      BLS poll interval after release (default: 3)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Awaitable

# Ensure repo root is on sys.path so we can import from core/ and strategies/
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.environment import log_environment_banner, resolve_environment
from strategies.econ.agents.econ_feed_agent import EconFeedAgent
from strategies.econ.agents.econ_scanner_agent import EconScannerAgent
from strategies.econ.core.config import EconConfig
from strategies.econ.core.models import EconRelease

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("econ.daemon")

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
    config = EconConfig.from_env()
    env = resolve_environment()
    log_environment_banner(env)

    logger.info(
        "Econ daemon starting | mode=%s bankroll=%.0f min_edge=%.2f",
        env.label, config.bankroll_usdc, config.min_edge,
    )

    release_queue: asyncio.Queue[EconRelease] = asyncio.Queue(maxsize=20)

    feed = EconFeedAgent(release_queue=release_queue, config=config)
    scanner = EconScannerAgent(release_queue=release_queue, config=config, environment=env)

    tasks = [
        asyncio.create_task(_guarded(feed.run(), "econ_feed"), name="econ_feed"),
        asyncio.create_task(_guarded(scanner.run(), "econ_scanner"), name="econ_scanner"),
    ]

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: [t.cancel() for t in tasks])

    logger.info("Econ daemon running (%d tasks). Ctrl+C to stop.", len(tasks))
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
    logger.info("Econ daemon stopped.")


if __name__ == "__main__":
    asyncio.run(main())
