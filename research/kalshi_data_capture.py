"""
High-resolution Kalshi order book capture for lag / microstructure research.

Defaults: ~2s full cycle over a small market set (1–5s target via --interval),
sports-first discovery using the same heuristics as ``kalshi_sports_taxonomy_scan``.

Run from repo root:
  python trading/research/kalshi_data_capture.py
  python trading/research/kalshi_data_capture.py --interval 1.5 --targets 3 --mode sports
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env = _REPO / ".env"
    if env.is_file():
        load_dotenv(env, override=False)
    load_dotenv(override=False)


_load_env()

from latency.core.kalshi_client import KalshiClient, market_from_api_dict  # noqa: E402

from kalshi_sports_hints import is_sports_raw, raw_volume_24h  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("kalshi_capture")

DB_PATH = Path(__file__).parent / "data" / "kalshi_orderbooks.db"

# Stagger between tickers to stay under Kalshi burst limits (see also client 429 backoff).
STAGGER_SEC = float(os.environ.get("KALSHI_CAPTURE_STAGGER", "0.25"))


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orderbooks (
            timestamp TEXT,
            ticker TEXT,
            yes_bid REAL,
            yes_ask REAL,
            no_bid REAL,
            no_ask REAL,
            spread_pct REAL,
            yes_bids_json TEXT,
            yes_asks_json TEXT,
            no_bids_json TEXT,
            no_asks_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_orderbooks_ticker_time
        ON orderbooks (ticker, timestamp);
        """
    )
    conn.commit()
    return conn


async def _discover_targets_sports(client: KalshiClient, count: int, max_pages: int):
    raw = await client.list_open_markets_raw(max_pages=max_pages)
    sports = [r for r in raw if is_sports_raw(r)]
    sports.sort(key=raw_volume_24h, reverse=True)
    out = []
    for r in sports:
        if len(out) >= count:
            break
        m = market_from_api_dict(r)
        if m is None:
            continue
        if not (0.03 <= m.implied_prob <= 0.97):
            continue
        out.append(m)
    return out


async def _discover_targets_top(client: KalshiClient, count: int):
    markets = await client.get_top_markets(
        limit=max(25, count * 5),
        min_liquidity=0.0,
        min_volume_24h=0.0,
    )
    return [m for m in markets if 0.05 <= m.implied_prob <= 0.95][:count]


async def capture_loop(
    *,
    interval_sec: float,
    target_count: int,
    mode: str,
    max_pages: int,
) -> None:
    conn = init_db()

    async with KalshiClient() as client:
        if not client.authenticated:
            logger.error("KalshiClient is not authenticated. Set KALSHI_* in repo-root .env.")
            return

        logger.info(
            "Discovering markets mode=%s count=%d (sports uses up to %d /markets pages)",
            mode,
            target_count,
            max_pages,
        )
        if mode == "sports":
            targets = await _discover_targets_sports(client, target_count, max_pages)
        else:
            targets = await _discover_targets_top(client, target_count)

        if not targets:
            logger.error("No capture targets after discovery. Exiting.")
            return

        logger.info(
            "Capturing %d tickers every ~%.2fs (stagger %.2fs): %s",
            len(targets),
            interval_sec,
            STAGGER_SEC,
            [m.ticker for m in targets],
        )

        while True:
            cycle_start = time.time()
            rows_to_insert = []

            for m in targets:
                await asyncio.sleep(STAGGER_SEC)
                try:
                    market_state = await client.get_market(m.ticker)
                    if not market_state:
                        continue

                    ob = await client.get_orderbook(m.ticker, depth=5)
                    yes_side = ob.get("yes", [])
                    no_side = ob.get("no", [])

                    yes_bids = [level for level in yes_side if level[1] > 0]
                    no_bids = [level for level in no_side if level[1] > 0]

                    rows_to_insert.append(
                        (
                            datetime.now(tz=timezone.utc).isoformat(),
                            m.ticker,
                            market_state.yes_bid,
                            market_state.yes_ask,
                            market_state.no_bid,
                            market_state.no_ask,
                            market_state.spread_pct,
                            json.dumps(yes_bids),
                            "[]",
                            json.dumps(no_bids),
                            "[]",
                        )
                    )
                except Exception as exc:
                    logger.error("Error fetching %s: %s", m.ticker, exc)

            if rows_to_insert:
                conn.executemany(
                    """
                    INSERT INTO orderbooks (
                        timestamp, ticker, yes_bid, yes_ask, no_bid, no_ask,
                        spread_pct, yes_bids_json, yes_asks_json, no_bids_json, no_asks_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows_to_insert,
                )
                conn.commit()
                logger.info(
                    "Captured %d orderbooks at %s",
                    len(rows_to_insert),
                    datetime.now(tz=timezone.utc).strftime("%H:%M:%S"),
                )

            elapsed = time.time() - cycle_start
            sleep_time = max(0.05, interval_sec - elapsed)
            await asyncio.sleep(sleep_time)


def main() -> None:
    p = argparse.ArgumentParser(description="Kalshi orderbook SQLite capture (research).")
    p.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("KALSHI_CAPTURE_INTERVAL_SEC", "2.0")),
        help="Target seconds between end of one full poll cycle and start of next (1–5 typical).",
    )
    p.add_argument(
        "--targets",
        type=int,
        default=int(os.environ.get("KALSHI_CAPTURE_TARGET_COUNT", "3")),
        help="How many markets to poll each cycle.",
    )
    p.add_argument(
        "--mode",
        choices=("sports", "top"),
        default=os.environ.get("KALSHI_CAPTURE_MODE", "sports"),
        help="sports = heuristic sports titles/tickers; top = get_top_markets liquidity sort.",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=int(os.environ.get("KALSHI_CAPTURE_MAX_PAGES", "25")),
        help="Cursor pages for sports discovery (200 markets per page).",
    )
    args = p.parse_args()
    raw_interval = args.interval
    interval = max(1.0, min(10.0, raw_interval))
    if interval != raw_interval:
        logger.warning("Clamped --interval %s to %.2fs (allowed 1–10)", raw_interval, interval)
    asyncio.run(
        capture_loop(
            interval_sec=interval,
            target_count=max(1, min(12, args.targets)),
            mode=args.mode,
            max_pages=max(1, min(100, args.max_pages)),
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Data capture stopped.")
