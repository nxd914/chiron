"""
Resolution Agent

Polls Kalshi for open paper positions and resolves them when markets settle.
This closes the P&L loop that ExecutionAgent leaves open:
  - Writes resolved_at / resolution / pnl_usdc back to SQLite
  - Calls risk_agent.record_fill() to free position slots
  - Without this, RiskAgent._open_positions fills to MAX_CONCURRENT_POSITIONS
    after 5 trades and trading stops permanently until restart.
"""

from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.config import Config, DEFAULT_CONFIG
from core.db import connect as db_connect
from core.kalshi_client import KalshiClient
from .risk_agent import _ticker_to_symbol

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60     # check open positions every minute
MAX_OPEN_HOURS = 6             # force-resolve positions open longer than this (assume total loss)
DB_PATH = Path(__file__).resolve().parents[3] / "data" / "paper_trades.db"


@dataclass
class _OpenRow:
    order_id: str
    ticker: str
    side: str
    entry_price: float
    size_usdc: float
    placed_at: Optional[datetime] = None


class ResolutionAgent:
    """
    Background agent that resolves paper positions once markets settle.

    On startup, re-syncs RiskAgent._open_positions from the DB so slots
    are correct even after a process restart.  Then polls every
    POLL_INTERVAL_SECONDS.
    """

    def __init__(
        self,
        risk_agent,                          # RiskAgent — typed weakly to avoid circular import
        db_path: Path = DB_PATH,
        poll_interval: int = POLL_INTERVAL_SECONDS,
        config: Config = DEFAULT_CONFIG,
    ) -> None:
        self._risk = risk_agent
        self._db_path = db_path
        self._poll_interval = poll_interval
        self._cfg = config
        self._client = KalshiClient()
        self._db: Optional[sqlite3.Connection] = None
        # In-memory guard against double-firing timeout resolutions when the DB write fails.
        # If _write_resolution raises, the row stays resolution=NULL and would re-accumulate
        # record_fill() losses every cycle. Tracking here stops that regardless of DB state.
        self._resolved_ids: set[str] = set()
        # Fill history for running performance metrics.
        # Each entry: (pnl_usdc, model_prob, won: bool)
        # Bounded to 500 fills; enough for meaningful Sharpe and calibration estimates.
        self._fill_history: deque[tuple[float, float, bool]] = deque(maxlen=500)
        # Minimum fills before computing Sharpe / calibration (avoids noise at n<20)
        self._min_fills_for_metrics: int = 20

    async def run(self) -> None:
        self._db = db_connect(str(self._db_path), check_same_thread=False)
        await self._client.open()
        try:
            self._sync_risk_positions()
            logger.info("ResolutionAgent: started, polling every %ds", self._poll_interval)
            while True:
                try:
                    await self._resolve_cycle()
                except Exception as exc:
                    logger.warning("ResolutionAgent cycle error: %s", exc)
                await asyncio.sleep(self._poll_interval)
        finally:
            await self._client.close()
            if self._db:
                self._db.close()

    # ------------------------------------------------------------------
    # Startup sync
    # ------------------------------------------------------------------

    def _sync_risk_positions(self) -> None:
        """Re-populate RiskAgent state from DB on startup so slots and circuit breakers survive restarts."""
        rows = self._load_open_rows()
        for row in rows:
            self._risk.restore_position(row.ticker, row.size_usdc)
        if rows:
            logger.info(
                "ResolutionAgent: synced %d open positions into RiskAgent from DB",
                len(rows),
            )

        # Rehydrate today's realized P&L so the circuit breaker survives restarts
        daily_pnl = self._load_daily_pnl()
        if daily_pnl != 0.0:
            self._risk.restore_daily_pnl(daily_pnl)
            logger.info("ResolutionAgent: rehydrated daily P&L = $%.2f", daily_pnl)

    # ------------------------------------------------------------------
    # Resolution cycle
    # ------------------------------------------------------------------

    async def _resolve_cycle(self) -> None:
        rows = self._load_open_rows()
        logger.info("ResolutionAgent: %d open positions to check", len(rows))
        if not rows:
            return

        now = datetime.now(tz=timezone.utc)

        for row in rows:
            # Timeout fallback — force-resolve zombie positions that have been open too long.
            # Assume total loss (conservative) to prevent permanently blocking position slots.
            if row.placed_at is not None:
                age_hours = (now - row.placed_at).total_seconds() / 3600.0
                if age_hours > MAX_OPEN_HOURS:
                    if row.order_id in self._resolved_ids:
                        # Already processed in-memory (DB write may have failed last cycle).
                        # Skip to prevent re-accumulating record_fill() losses.
                        continue
                    pnl = -(row.size_usdc)
                    db_ok = self._write_resolution(row.order_id, "EXPIRED_TIMEOUT", pnl)
                    if db_ok:
                        self._risk.record_fill(row.ticker, pnl)
                    else:
                        logger.error(
                            "Timeout resolution DB write failed for %s — skipping record_fill to prevent loss accumulation",
                            row.order_id,
                        )
                    # Always mark resolved in-memory regardless of DB outcome.
                    # Prevents re-accumulating record_fill() losses on next cycle.
                    self._resolved_ids.add(row.order_id)
                    logger.warning(
                        "Timeout-resolved: [%s] open %.1fh > %dh limit → EXPIRED_TIMEOUT P&L: $%+.2f",
                        row.ticker, age_hours, MAX_OPEN_HOURS, pnl,
                    )
                    continue

            raw = await self._client.get_market_for_resolution(row.ticker)
            if raw is None:
                continue

            resolution = _check_resolution_from_raw(raw)
            if resolution is None:
                continue

            pnl = _compute_pnl(row, resolution)
            db_ok = self._write_resolution(row.order_id, resolution, pnl)
            if db_ok:
                self._risk.record_fill(row.ticker, pnl)
                won = (row.side == "YES" and resolution == "YES") or \
                      (row.side == "NO"  and resolution == "NO")
                # model_prob not stored in _OpenRow; use a sentinel of -1 when unavailable.
                # Future: join on trades table to retrieve model_prob here.
                self._fill_history.append((pnl, -1.0, won))
                self._log_performance_metrics(pnl, resolution)
                logger.info(
                    "Resolved: [%s] %s @ %.3f → %s  P&L: $%+.2f",
                    row.ticker,
                    row.side,
                    row.entry_price,
                    resolution,
                    pnl,
                )
            else:
                logger.error(
                    "Resolution DB write failed for %s — position slot NOT freed, will retry next cycle",
                    row.order_id,
                )

    # ------------------------------------------------------------------
    # Performance metrics
    # ------------------------------------------------------------------

    def _log_performance_metrics(self, latest_pnl: float, resolution: str) -> None:
        """Log running Sharpe ratio and win rate after each resolution."""
        n = len(self._fill_history)
        if n < self._min_fills_for_metrics:
            logger.info(
                "Performance: %d/%d fills for metrics | latest P&L: $%+.2f → %s",
                n, self._min_fills_for_metrics, latest_pnl, resolution,
            )
            return

        wins = sum(1 for _, _, won in self._fill_history if won)
        win_rate = wins / n
        sharpe = self._running_sharpe()
        logger.info(
            "Performance: n=%d | win_rate=%.1f%% | Sharpe=%.2f | latest P&L: $%+.2f → %s",
            n, win_rate * 100, sharpe, latest_pnl, resolution,
        )

    def _running_sharpe(self) -> float:
        """
        Annualized Sharpe ratio over all fills in history.

        Uses per-fill P&L (not time-series). Annualization assumes ~4 fills/day
        as a rough baseline — replace with actual cadence once fill rate is known.
        """
        if len(self._fill_history) < self._min_fills_for_metrics:
            return float("nan")

        returns = [pnl for pnl, _, _ in self._fill_history]
        n = len(returns)
        mean = sum(returns) / n
        variance = sum((r - mean) ** 2 for r in returns) / (n - 1) if n > 1 else 0.0
        std = math.sqrt(variance) if variance > 0 else 0.0

        if std == 0.0:
            return float("inf") if mean > 0 else 0.0

        annualization = math.sqrt(self._cfg.assumed_fills_per_day * 365)
        return (mean / std) * annualization

    def get_metrics_snapshot(self) -> dict:
        """Return current performance metrics as a dict. For dashboard / health check."""
        n = len(self._fill_history)
        if n == 0:
            return {"n_fills": 0, "win_rate": None, "sharpe": None, "total_pnl": None}
        wins = sum(1 for _, _, won in self._fill_history if won)
        total_pnl = sum(pnl for pnl, _, _ in self._fill_history)
        return {
            "n_fills": n,
            "win_rate": wins / n,
            "sharpe": self._running_sharpe() if n >= self._min_fills_for_metrics else None,
            "total_pnl": total_pnl,
        }

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _load_open_rows(self) -> list[_OpenRow]:
        assert self._db is not None
        try:
            existing = {
                row[1] for row in
                self._db.execute("PRAGMA table_info(trades)").fetchall()
            }
            ticker_col = "ticker" if "ticker" in existing else "condition_id"
            cur = self._db.execute(
                f"""
                SELECT order_id, {ticker_col}, side, fill_price, size_usdc, placed_at
                FROM trades
                WHERE resolution IS NULL AND pnl_usdc IS NULL
                  AND {ticker_col} IS NOT NULL
                """
            )
            rows: list[_OpenRow] = []
            for r in cur.fetchall():
                placed_at = None
                if r[5]:
                    try:
                        placed_at = datetime.fromisoformat(str(r[5]).replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass
                rows.append(_OpenRow(
                    order_id=r[0],
                    ticker=r[1],
                    side=r[2],
                    entry_price=float(r[3] or 0),
                    size_usdc=float(r[4] or 0),
                    placed_at=placed_at,
                ))
            return rows
        except sqlite3.Error as exc:
            logger.error("ResolutionAgent DB read error: %s", exc)
            return []

    def _load_daily_pnl(self) -> float:
        """Sum today's resolved P&L from the trades table."""
        assert self._db is not None
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        try:
            cur = self._db.execute(
                """
                SELECT COALESCE(SUM(pnl_usdc), 0.0)
                FROM trades
                WHERE pnl_usdc IS NOT NULL
                  AND resolved_at >= ?
                """,
                (today,),
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0
        except sqlite3.Error as exc:
            logger.error("ResolutionAgent: failed to load daily P&L: %s", exc)
            return 0.0

    def _write_resolution(self, order_id: str, resolution: str, pnl: float) -> bool:
        """Write resolution to DB. Returns True on success, False on failure."""
        assert self._db is not None
        now = datetime.now(tz=timezone.utc).isoformat()
        try:
            self._db.execute(
                """
                UPDATE trades
                SET resolved_at = ?, resolution = ?, pnl_usdc = ?, status = 'RESOLVED'
                WHERE order_id = ?
                """,
                (now, resolution, pnl, order_id),
            )
            self._db.commit()
            return True
        except sqlite3.Error as exc:
            logger.error("ResolutionAgent DB write error: %s", exc)
            return False


# ------------------------------------------------------------------
# Pure helpers (no state)
# ------------------------------------------------------------------

def _check_resolution_from_raw(raw: dict) -> Optional[str]:
    """Determine resolution from raw Kalshi API market response.

    Primary: uses the authoritative ``status`` / ``result`` fields.
    Fallback: price-level heuristic for markets where the API hasn't
    populated ``result`` yet.  Operates on the raw dict so we bypass
    the ``_parse_market`` price filter that discards settled markets.
    """
    status = raw.get("status", "")
    result = raw.get("result", "")

    # Primary path — Kalshi's authoritative settlement
    if status == "settled" and result:
        return result.upper()  # "yes" -> "YES"

    # Fallback — price-level heuristic (works on raw dict fields)
    yes_bid = _safe_price(raw, "yes_bid")
    yes_ask = _safe_price(raw, "yes_ask")

    if yes_bid >= 0.99:
        return "YES"
    if yes_ask <= 0.01:
        return "NO"

    close_time = raw.get("close_time", "")
    if close_time:
        try:
            close = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
            if close <= datetime.now(tz=timezone.utc):
                implied = (yes_bid + yes_ask) / 2.0 if yes_bid > 0 and yes_ask > 0 else 0.0
                if implied >= 0.95:
                    return "YES"
                elif implied <= 0.05:
                    return "NO"
        except (ValueError, TypeError):
            pass

    return None


def _safe_price(raw: dict, field: str) -> float:
    """Extract a price field from raw API dict, handling cents vs dollars.

    Kalshi V2 sends integer cents (1-99) for bid/ask fields.
    Dollar fallback fields (``*_dollars`` suffix) are already in [0.0, 1.0].
    Values >= 1 are treated as cents and divided by 100.
    """
    # Prefer dollar fallback if available (already in 0-1 range)
    dollar_field = f"{field}_dollars"
    dollar_val = raw.get(dollar_field)
    if dollar_val is not None:
        return float(dollar_val)

    val = raw.get(field, 0)
    if val is None:
        return 0.0
    fval = float(val)
    # Integer cents: divide by 100. Values in [0, 1) are already dollars.
    if fval >= 1.0:
        fval /= 100.0
    return fval


KALSHI_TAKER_FEE_RATE = 0.07  # from api-docs/kalshi-fee-schedule.pdf


def _compute_pnl(row: _OpenRow, resolution: str) -> float:
    """Binary P&L with Kalshi taker fee deducted.

    Fee formula: ceil(0.07 × C × P × (1-P)) rounded up to next cent.
    Fee is paid at entry regardless of outcome (no settlement fee).
    """
    num_contracts = row.size_usdc / row.entry_price if row.entry_price > 0 else 0.0
    total_fee = math.ceil(
        KALSHI_TAKER_FEE_RATE * num_contracts * row.entry_price * (1.0 - row.entry_price) * 100
    ) / 100

    won = (row.side == "YES" and resolution == "YES") or \
          (row.side == "NO" and resolution == "NO")
    if won:
        gross = (row.size_usdc / row.entry_price) - row.size_usdc
        return gross - total_fee
    return -(row.size_usdc + total_fee)
