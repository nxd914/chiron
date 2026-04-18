/con"""
One-off diagnostic: paginate open Kalshi markets (no volume filter) and print
likely sports rows so we can see real titles/tickers for signal matching.

Usage (repo root):
  python trading/research/kalshi_sports_taxonomy_scan.py
  python trading/research/kalshi_sports_taxonomy_scan.py --max-pages 30 --limit 200
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
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

from latency.core.kalshi_client import KalshiClient  # noqa: E402

from kalshi_sports_hints import is_sports_raw, raw_volume_24h  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("kalshi_sports_scan")


async def _run(max_pages: int, limit: int) -> None:
    async with KalshiClient() as client:
        if not client.authenticated:
            log.warning(
                "KalshiClient is unauthenticated — set KALSHI_API_KEY and "
                "KALSHI_PRIVATE_KEY_PATH in repo-root .env for full /markets access."
            )
        raw = await client.list_open_markets_raw(max_pages=max_pages)
    sports = [r for r in raw if is_sports_raw(r)]
    sports.sort(key=raw_volume_24h, reverse=True)
    sports = sports[:limit]

    print(f"open_markets_fetched={len(raw)} sports_heuristic_matched={len(sports)}", file=sys.stderr)
    print("ticker\tevent_ticker\tvolume_24h\ttitle")
    for r in sports:
        title = (r.get("title") or "").replace("\t", " ")[:200]
        print(
            f"{r.get('ticker', '')}\t{r.get('event_ticker', '')}\t"
            f"{raw_volume_24h(r):.0f}\t{title}",
        )


def main() -> None:
    p = argparse.ArgumentParser(description="List likely sports Kalshi markets (taxonomy probe).")
    p.add_argument("--max-pages", type=int, default=50, help="Max /markets cursor pages (200 markets each).")
    p.add_argument("--limit", type=int, default=400, help="Max sports rows to print after sort by volume.")
    args = p.parse_args()
    asyncio.run(_run(args.max_pages, args.limit))


if __name__ == "__main__":
    main()
