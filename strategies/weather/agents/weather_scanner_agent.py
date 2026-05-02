"""
Weather Scanner Agent

Receives WeatherObservation events, scans Kalshi for matching weather markets,
and trades any that haven't priced in a certain outcome based on current readings.

Edge logic:
  After an observation confirms a threshold has been crossed, the outcome is certain.
  E.g., if NWS reads 91°F in NYC and Kalshi market asks "Will NYC exceed 90°F today?",
  the outcome is YES with probability 1.0. Any market price below ~0.97 is edge.
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
from ..core.config import WeatherConfig
from ..core.models import WeatherObservation
from ..core.stations import CITY_ALIASES

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "weather_trades.db"

_WEATHER_HINTS = (
    "temperature", "temp", "high", "low", "degrees",
    "rainfall", "precipitation", "rain", "snow", "snowfall",
    "wind", "humidity", "heat", "cold", "frost",
)

_ABOVE_RE = re.compile(r"\babove\s+([0-9]+(?:\.\d+)?)\s*(?:°?[fF]|degrees?)?", re.IGNORECASE)
_BELOW_RE = re.compile(r"\bbelow\s+([0-9]+(?:\.\d+)?)\s*(?:°?[fF]|degrees?)?", re.IGNORECASE)
_EXCEED_RE = re.compile(r"\bexceed(?:s)?\s+([0-9]+(?:\.\d+)?)\s*(?:°?[fF]|degrees?)?", re.IGNORECASE)
_REACH_RE  = re.compile(r"\breach(?:es)?\s+([0-9]+(?:\.\d+)?)\s*(?:°?[fF]|degrees?)?", re.IGNORECASE)


class WeatherScannerAgent:
    def __init__(
        self,
        obs_queue: asyncio.Queue[WeatherObservation],
        config: WeatherConfig,
        environment: Optional[Environment] = None,
    ) -> None:
        self._queue = obs_queue
        self._config = config
        self._env = environment or resolve_environment()
        self._db = _init_db()
        self._open_positions: int = 0
        self._traded_today: set[str] = set()  # tickers traded this session
        from core.kalshi_client import KalshiClient  # noqa: PLC0415
        self._kalshi = KalshiClient(
            api_key=self._env.api_key,
            private_key_path=self._env.private_key_path,
            base_url=self._env.rest_base_url,
        )

    async def run(self) -> None:
        await self._kalshi.open()
        logger.info("WeatherScannerAgent started")
        try:
            while True:
                obs = await self._queue.get()
                await self._process_observation(obs)
        finally:
            await self._kalshi.close()

    async def _process_observation(self, obs: WeatherObservation) -> None:
        if obs.temperature_f is None:
            return

        if self._open_positions >= self._config.max_concurrent_positions:
            return

        try:
            raw_markets = await self._kalshi.list_open_markets_raw(max_pages=10)
            weather_markets = [m for m in raw_markets if _is_weather_raw(m)]
        except Exception as exc:
            logger.error("WeatherScannerAgent: failed to fetch markets: %s", exc)
            return

        candidates = _match_markets(weather_markets, obs)
        if not candidates:
            return

        logger.info(
            "WeatherScannerAgent: %d weather candidates for %s (%.1f°F)",
            len(candidates), obs.city, obs.temperature_f,
        )

        for raw in candidates:
            ticker = raw.get("ticker", "")
            if ticker in self._traded_today:
                continue
            if self._open_positions >= self._config.max_concurrent_positions:
                break

            scored = _score_market(raw, obs, self._config.min_edge)
            if scored is None:
                continue
            side, edge, model_prob, market_price = scored

            from core.kelly import capped_kelly, position_size  # noqa: PLC0415
            kelly = capped_kelly(model_prob, market_price)
            size_usdc = position_size(model_prob, market_price, self._config.bankroll_usdc)
            if size_usdc <= 0:
                continue

            title = str(raw.get("title", ""))[:200]
            order_id = await self._place_order(ticker, side, size_usdc, market_price)
            self._traded_today.add(ticker)

            _persist(self._db, {
                "order_id": order_id,
                "ticker": ticker,
                "title": title,
                "side": side,
                "city": obs.city,
                "temperature_f": obs.temperature_f,
                "model_prob": model_prob,
                "market_prob": _implied_prob(raw),
                "edge": edge,
                "kelly_fraction": kelly,
                "size_usdc": size_usdc,
                "fill_price": market_price,
                "placed_at": datetime.now(tz=timezone.utc).isoformat(),
            })
            self._open_positions += 1
            logger.info(
                "WeatherScannerAgent: ORDER %s | %s | side=%s edge=%.3f size=%.0f USDC",
                order_id, ticker, side, edge, size_usdc,
            )

    async def _place_order(self, ticker: str, side: str, size_usdc: float, market_price: float) -> str:
        if not self._env.place_real_orders:
            return f"sim_{uuid.uuid4().hex[:12]}"

        price_cents = max(1, min(99, round(market_price * 100)))
        count = max(1, int(size_usdc / market_price))
        try:
            resp = await self._kalshi.place_limit_order(
                ticker=ticker,
                side=side.lower(),
                count=count,
                yes_price_cents=price_cents if side == "YES" else 100 - price_cents,
            )
            return str(resp.get("order", {}).get("order_id", "unknown"))
        except Exception as exc:
            logger.error("WeatherScannerAgent: live order failed for %s: %s", ticker, exc)
            return f"failed_{uuid.uuid4().hex[:8]}"


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _is_weather_raw(raw: dict) -> bool:
    haystack = f"{raw.get('title') or ''} {raw.get('ticker') or ''} {raw.get('event_ticker') or ''}".lower()
    return any(hint in haystack for hint in _WEATHER_HINTS)


def _match_markets(raw_markets: list[dict], obs: WeatherObservation) -> list[dict]:
    """Filter markets to those about this city's temperature."""
    city_lower = obs.city.lower()
    matched = []
    for raw in raw_markets:
        title = str(raw.get("title") or "").lower()
        ticker = str(raw.get("ticker") or "").lower()
        haystack = f"{title} {ticker}"

        # Check city match
        city_matched = city_lower in haystack
        if not city_matched:
            city_matched = any(alias in haystack for alias, canonical in CITY_ALIASES.items()
                               if canonical == obs.city)
        if not city_matched:
            continue

        # Must have a temperature-related threshold we can evaluate
        if not (_ABOVE_RE.search(haystack) or _BELOW_RE.search(haystack)
                or _EXCEED_RE.search(haystack) or _REACH_RE.search(haystack)):
            continue

        matched.append(raw)

    return matched


def _score_market(
    raw: dict,
    obs: WeatherObservation,
    min_edge: float,
) -> Optional[tuple[str, float, float, float]]:
    """
    Determine if a Kalshi weather market has tradeable edge given an observation.
    Only evaluates temperature threshold markets.
    """
    if obs.temperature_f is None:
        return None

    text = f"{raw.get('title') or ''} {raw.get('ticker') or ''}".casefold()
    actual = obs.temperature_f

    # "exceed X" / "reach X" → YES if actual >= threshold (already crossed)
    for pattern in (_EXCEED_RE, _REACH_RE):
        m = pattern.search(text)
        if m:
            threshold = float(m.group(1))
            yes_resolves = actual >= threshold
            return _make_score(yes_resolves, raw, min_edge)

    # "above X" → YES if actual > threshold
    m = _ABOVE_RE.search(text)
    if m:
        threshold = float(m.group(1))
        yes_resolves = actual > threshold
        return _make_score(yes_resolves, raw, min_edge)

    # "below X" → YES if actual < threshold
    m = _BELOW_RE.search(text)
    if m:
        threshold = float(m.group(1))
        yes_resolves = actual < threshold
        return _make_score(yes_resolves, raw, min_edge)

    return None


def _make_score(
    yes_resolves: bool,
    raw: dict,
    min_edge: float,
) -> Optional[tuple[str, float, float, float]]:
    model_prob = 1.0 if yes_resolves else 0.0
    implied = _implied_prob(raw)
    edge = abs(model_prob - implied)
    if edge < min_edge:
        return None
    side = "YES" if yes_resolves else "NO"
    yes_ask = _price(raw, "yes_ask")
    no_ask = _price(raw, "no_ask")
    market_price = yes_ask if side == "YES" else no_ask
    if market_price <= 0:
        return None
    return (side, edge, model_prob, market_price)


def _implied_prob(raw: dict) -> float:
    yes_bid = _price(raw, "yes_bid")
    yes_ask = _price(raw, "yes_ask")
    if yes_bid <= 0 or yes_ask <= 0:
        return 0.5
    return (yes_bid + yes_ask) / 2.0


def _price(raw: dict, field: str) -> float:
    val = raw.get(field, 0)
    if val and float(val) > 1:
        return float(val) / 100.0
    return float(val or 0)


def _persist(conn: sqlite3.Connection, row: dict) -> None:
    try:
        conn.execute(
            """
            INSERT INTO weather_trades (
                order_id, ticker, title, side, city, temperature_f,
                model_prob, market_prob, edge, kelly_fraction,
                size_usdc, fill_price, placed_at
            ) VALUES (
                :order_id, :ticker, :title, :side, :city, :temperature_f,
                :model_prob, :market_prob, :edge, :kelly_fraction,
                :size_usdc, :fill_price, :placed_at
            )
            """,
            row,
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.error("WeatherScannerAgent: DB write error: %s", exc)


def _init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db_connect(str(DB_PATH), check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weather_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            ticker TEXT,
            title TEXT,
            side TEXT,
            city TEXT,
            temperature_f REAL,
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
