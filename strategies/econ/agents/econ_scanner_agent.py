"""
Economic Scanner Agent

Receives EconRelease events from EconFeedAgent, finds matching Kalshi
markets, computes edge (post-release certainty vs pre-release price),
and places paper or live orders.

Edge logic:
  After a data release the outcome is certain: P(YES) = 1.0 or 0.0.
  Edge = |certainty - market.implied_prob|.
  Any market that hasn't repriced yet is an arb opportunity.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
from core.db import connect as db_connect
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.environment import Environment, resolve_environment
from ..core.config import EconConfig
from ..core.models import EconEventType, EconRelease

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "econ_trades.db"

# Keywords to match Kalshi market titles/tickers to each event type
_KEYWORDS: dict[EconEventType, tuple[str, ...]] = {
    EconEventType.CPI: ("cpi", "inflation", "consumer price"),
    EconEventType.NFP: ("nonfarm", "payroll", "employment situation", "jobs"),
    EconEventType.FOMC: ("fomc", "federal funds", "fed rate", "rate hike", "rate cut", "basis points"),
    EconEventType.PPI: ("ppi", "producer price", "wholesale price"),
}

# "above X.X%" / "above X,XXX" / "above XXXK"
_ABOVE_RE = re.compile(r"\babove\s+([0-9,]+(?:\.\d+)?)\s*(%|k\b)?", re.IGNORECASE)
_BELOW_RE = re.compile(r"\bbelow\s+([0-9,]+(?:\.\d+)?)\s*(%|k\b)?", re.IGNORECASE)
_BETWEEN_RE = re.compile(r"\bbetween\s+([0-9,]+(?:\.\d+)?)\s*%?\s+and\s+([0-9,]+(?:\.\d+)?)\s*%?", re.IGNORECASE)


class EconScannerAgent:
    def __init__(
        self,
        release_queue: asyncio.Queue[EconRelease],
        config: EconConfig,
        environment: Optional[Environment] = None,
    ) -> None:
        self._queue = release_queue
        self._config = config
        self._env = environment or resolve_environment()
        self._db = _init_db()
        # Import here so the econ package has no hard dep on the crypto pipeline
        from core.kalshi_client import KalshiClient  # noqa: PLC0415
        self._kalshi = KalshiClient(
            api_key=self._env.api_key,
            private_key_path=self._env.private_key_path,
            base_url=self._env.rest_base_url,
        )
        self._open_positions: int = 0

    async def run(self) -> None:
        await self._kalshi.open()
        logger.info("EconScannerAgent started")
        try:
            while True:
                release = await self._queue.get()
                await self._process_release(release)
        finally:
            await self._kalshi.close()

    async def _process_release(self, release: EconRelease) -> None:
        logger.info(
            "EconScannerAgent: processing %s — actual=%.2f",
            release.event.description,
            release.actual,
        )

        if self._open_positions >= self._config.max_concurrent_positions:
            logger.warning("EconScannerAgent: position limit reached (%d), skipping", self._open_positions)
            return

        try:
            markets = await self._kalshi.get_top_markets(limit=500, min_volume_24h=0, min_liquidity=0)
        except Exception as exc:
            logger.error("EconScannerAgent: failed to fetch markets: %s", exc)
            return

        keywords = _KEYWORDS.get(release.event.event_type, ())
        candidates = [
            m for m in markets
            if any(kw in f"{m.ticker} {m.title}".casefold() for kw in keywords)
        ]
        logger.info(
            "EconScannerAgent: %d candidate markets for %s",
            len(candidates), release.event.event_type.value,
        )

        for market in candidates:
            if self._open_positions >= self._config.max_concurrent_positions:
                break
            scored = _score_market(market, release, self._config.min_edge)
            if scored is None:
                continue
            side, edge, model_prob, market_price = scored

            from core.kelly import capped_kelly, position_size  # noqa: PLC0415
            kelly = capped_kelly(model_prob, market_price)
            size_usdc = position_size(model_prob, market_price, self._config.bankroll_usdc)
            if size_usdc <= 0:
                continue

            fill_price = market_price
            order_id = await self._place_order(market.ticker, side, size_usdc, fill_price, market_price)

            _persist(self._db, {
                "order_id": order_id,
                "ticker": market.ticker,
                "title": market.title[:200],
                "side": side,
                "event_type": release.event.event_type.value,
                "actual": release.actual,
                "model_prob": model_prob,
                "market_prob": market.implied_prob,
                "edge": edge,
                "kelly_fraction": kelly,
                "size_usdc": size_usdc,
                "fill_price": fill_price,
                "placed_at": datetime.now(tz=timezone.utc).isoformat(),
            })
            self._open_positions += 1
            logger.info(
                "EconScannerAgent: ORDER %s | %s | side=%s edge=%.3f size=%.0f USDC",
                order_id, market.ticker, side, edge, size_usdc,
            )

    async def _place_order(
        self,
        ticker: str,
        side: str,
        size_usdc: float,
        fill_price: float,
        market_price: float,
    ) -> str:
        if not self._env.place_real_orders:
            return f"sim_{uuid.uuid4().hex[:12]}"

        price_cents = max(1, min(99, round(market_price * 100)))
        if side == "NO":
            price_cents = 100 - price_cents
        count = max(1, int(size_usdc / fill_price))
        try:
            resp = await self._kalshi.place_limit_order(
                ticker=ticker,
                side=side.lower(),
                count=count,
                yes_price_cents=price_cents if side == "YES" else 100 - price_cents,
            )
            return str(resp.get("order", {}).get("order_id", "unknown"))
        except Exception as exc:
            logger.error("EconScannerAgent: live order failed for %s: %s", ticker, exc)
            return f"failed_{uuid.uuid4().hex[:8]}"


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _score_market(
    market,
    release: EconRelease,
    min_edge: float,
) -> Optional[tuple[str, float, float, float]]:
    """
    Determine if a Kalshi market has tradeable edge given an EconRelease.

    Returns (side, edge, model_prob, market_price) or None.

    After a data release, the outcome is certain (model_prob = 1.0 or 0.0).
    Edge = |certainty - market.implied_prob|.
    """
    text = f"{market.title} {market.ticker}".casefold()
    actual = release.actual

    # Try "between X and Y" first (range markets)
    m = _BETWEEN_RE.search(text)
    if m:
        lo = _parse_num(m.group(1), release.event.event_type)
        hi = _parse_num(m.group(2), release.event.event_type)
        yes_resolves = lo < actual < hi
        model_prob = 1.0 if yes_resolves else 0.0
        edge = abs(model_prob - market.implied_prob)
        if edge < min_edge:
            return None
        side = "YES" if yes_resolves else "NO"
        market_price = market.yes_ask if side == "YES" else market.no_ask
        return (side, edge, model_prob, market_price)

    # "above X"
    m = _ABOVE_RE.search(text)
    if m:
        threshold = _parse_num(m.group(1), release.event.event_type, suffix=m.group(2))
        yes_resolves = actual > threshold
        model_prob = 1.0 if yes_resolves else 0.0
        edge = abs(model_prob - market.implied_prob)
        if edge < min_edge:
            return None
        side = "YES" if yes_resolves else "NO"
        market_price = market.yes_ask if side == "YES" else market.no_ask
        return (side, edge, model_prob, market_price)

    # "below X"
    m = _BELOW_RE.search(text)
    if m:
        threshold = _parse_num(m.group(1), release.event.event_type, suffix=m.group(2))
        yes_resolves = actual < threshold
        model_prob = 1.0 if yes_resolves else 0.0
        edge = abs(model_prob - market.implied_prob)
        if edge < min_edge:
            return None
        side = "YES" if yes_resolves else "NO"
        market_price = market.yes_ask if side == "YES" else market.no_ask
        return (side, edge, model_prob, market_price)

    return None


def _parse_num(raw: str, event_type: EconEventType, suffix: Optional[str] = None) -> float:
    """Parse a threshold number from a market title fragment."""
    val = float(raw.replace(",", ""))
    # "K" suffix means thousands (NFP: "200K" → 200)
    if suffix and suffix.strip().casefold() == "k":
        val = val  # NFP already in thousands, no conversion needed
    return val


def _persist(conn: sqlite3.Connection, row: dict) -> None:
    try:
        conn.execute(
            """
            INSERT INTO econ_trades (
                order_id, ticker, title, side, event_type, actual,
                model_prob, market_prob, edge, kelly_fraction,
                size_usdc, fill_price, placed_at
            ) VALUES (
                :order_id, :ticker, :title, :side, :event_type, :actual,
                :model_prob, :market_prob, :edge, :kelly_fraction,
                :size_usdc, :fill_price, :placed_at
            )
            """,
            row,
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.error("EconScannerAgent: DB write error: %s", exc)


def _init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db_connect(str(DB_PATH), check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS econ_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            ticker TEXT,
            title TEXT,
            side TEXT,
            event_type TEXT,
            actual REAL,
            model_prob REAL,
            market_prob REAL,
            edge REAL,
            kelly_fraction REAL,
            size_usdc REAL,
            fill_price REAL,
            placed_at TEXT,
            resolved_at TEXT,
            resolution TEXT,
            pnl_usdc REAL
        )
    """)
    conn.commit()
    return conn
