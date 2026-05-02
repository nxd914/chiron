"""
Sports Scanner Agent

Receives GameResult events, scans Kalshi for matching winner markets,
and trades any that haven't yet priced in the known outcome.

Edge logic:
  After a game ends, the outcome is certain: P(YES) = 1.0 or 0.0.
  Only trades binary winner markets — skips spread/total/prop markets
  (those require score data, not just the winner).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
from core.db import connect as db_connect
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typing import Optional

from core.environment import Environment, resolve_environment
from ..core.config import SportsConfig
from ..core.models import GameResult

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "sports_trades.db"

# Regex to detect threshold/spread markets — skip these (need score, not just winner)
_THRESHOLD_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:%|points?|runs?|goals?|assists?|\+|-\d|\bover\b|\bunder\b|\bspread\b)",
    re.IGNORECASE,
)

# Words that suggest a "who wins" market
_WIN_WORDS = ("win", "wins", "winner", "beat", "beats", "defeat", "defeats", "advance", "advances")


class SportsScannerAgent:
    def __init__(
        self,
        result_queue: asyncio.Queue[GameResult],
        config: SportsConfig,
        environment: Optional[Environment] = None,
    ) -> None:
        self._queue = result_queue
        self._config = config
        self._env = environment or resolve_environment()
        self._db = _init_db()
        self._open_positions: int = 0
        from core.kalshi_client import KalshiClient  # noqa: PLC0415
        self._kalshi = KalshiClient(
            api_key=self._env.api_key,
            private_key_path=self._env.private_key_path,
            base_url=self._env.rest_base_url,
        )

    async def run(self) -> None:
        await self._kalshi.open()
        logger.info("SportsScannerAgent started")
        try:
            while True:
                result = await self._queue.get()
                await self._process_result(result)
        finally:
            await self._kalshi.close()

    async def _process_result(self, result: GameResult) -> None:
        if not result.winner:
            logger.info("SportsScannerAgent: skipping tie — %s vs %s", result.home_team, result.away_team)
            return

        logger.info(
            "SportsScannerAgent: processing %s — %s beat %s %d-%d",
            result.sport.value.upper(),
            result.winner,
            result.away_team if result.winner == result.home_team else result.home_team,
            max(result.home_score, result.away_score),
            min(result.home_score, result.away_score),
        )

        if self._open_positions >= self._config.max_concurrent_positions:
            logger.warning("SportsScannerAgent: position limit reached (%d), skipping", self._open_positions)
            return

        try:
            from research.kalshi_sports_hints import is_sports_raw  # noqa: PLC0415
            raw_markets = await self._kalshi.list_open_markets_raw(max_pages=20)
            sports_markets = [m for m in raw_markets if is_sports_raw(m)]
        except Exception as exc:
            logger.error("SportsScannerAgent: failed to fetch markets: %s", exc)
            return

        candidates = _match_markets(sports_markets, result)
        logger.info("SportsScannerAgent: %d candidate markets for %s vs %s", len(candidates), result.home_team, result.away_team)

        for raw in candidates:
            if self._open_positions >= self._config.max_concurrent_positions:
                break

            scored = _score_market(raw, result, self._config.min_edge)
            if scored is None:
                continue
            side, edge, model_prob, market_price = scored

            from core.kelly import capped_kelly, position_size  # noqa: PLC0415
            kelly = capped_kelly(model_prob, market_price)
            size_usdc = position_size(model_prob, market_price, self._config.bankroll_usdc)
            if size_usdc <= 0:
                continue

            ticker = raw.get("ticker", "")
            title = str(raw.get("title", ""))[:200]
            order_id = await self._place_order(ticker, side, size_usdc, market_price)

            _persist(self._db, {
                "order_id": order_id,
                "ticker": ticker,
                "title": title,
                "side": side,
                "sport": result.sport.value,
                "home_team": result.home_team,
                "away_team": result.away_team,
                "winner": result.winner,
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
                "SportsScannerAgent: ORDER %s | %s | side=%s edge=%.3f size=%.0f USDC",
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
            logger.error("SportsScannerAgent: live order failed for %s: %s", ticker, exc)
            return f"failed_{uuid.uuid4().hex[:8]}"


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _match_markets(raw_markets: list[dict], result: GameResult) -> list[dict]:
    """
    Filter Kalshi markets to those likely about this specific game.

    Requires: both team names present in title, a win-related word present,
    no threshold numbers (skip spread/total markets), and close_time
    within 2 days of game_date.
    """
    home_lower = result.home_team.lower()
    away_lower = result.away_team.lower()
    game_date = result.game_date

    matched = []
    for raw in raw_markets:
        title = str(raw.get("title") or "").lower()
        ticker = str(raw.get("ticker") or "").lower()
        haystack = f"{title} {ticker}"

        if home_lower not in haystack and away_lower not in haystack:
            continue
        if not (home_lower in haystack and away_lower in haystack):
            continue
        if not any(w in haystack for w in _WIN_WORDS):
            continue
        if _THRESHOLD_RE.search(haystack):
            continue

        close_time_str = raw.get("close_time") or raw.get("expiration_time") or ""
        if close_time_str:
            try:
                close_dt = datetime.fromisoformat(str(close_time_str).replace("Z", "+00:00"))
                close_date = close_dt.date()
                if abs((close_date - game_date).days) > 2:
                    continue
            except (ValueError, AttributeError):
                pass

        matched.append(raw)

    return matched


def _score_market(
    raw: dict,
    result: GameResult,
    min_edge: float,
) -> tuple[str, float, float, float] | None:
    """
    Determine if a Kalshi winner market has tradeable edge.

    Returns (side, edge, model_prob, market_price) or None.
    """
    title = str(raw.get("title") or "").lower()
    winner_lower = result.winner.lower()

    # Determine if YES resolves for the winner
    # Title typically: "Will the [team] win?" → YES if that team is the winner
    yes_resolves = winner_lower in title

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
        return float(val) / 100.0  # integer cents → decimal
    return float(val or 0)


def _persist(conn: sqlite3.Connection, row: dict) -> None:
    try:
        conn.execute(
            """
            INSERT INTO sports_trades (
                order_id, ticker, title, side, sport,
                home_team, away_team, winner,
                model_prob, market_prob, edge, kelly_fraction,
                size_usdc, fill_price, placed_at
            ) VALUES (
                :order_id, :ticker, :title, :side, :sport,
                :home_team, :away_team, :winner,
                :model_prob, :market_prob, :edge, :kelly_fraction,
                :size_usdc, :fill_price, :placed_at
            )
            """,
            row,
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.error("SportsScannerAgent: DB write error: %s", exc)


def _init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db_connect(str(DB_PATH), check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sports_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            ticker TEXT,
            title TEXT,
            side TEXT,
            sport TEXT,
            home_team TEXT,
            away_team TEXT,
            winner TEXT,
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
