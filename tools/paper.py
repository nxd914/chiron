"""
Paper Trading Engine — Kalshi Edition

Autonomous strategy loop: scan Kalshi → evaluate → size → paper-execute → track P&L.
Runs continuously, printing trade signals and portfolio state to the terminal.

    from tools.paper import PaperTrader
    trader = PaperTrader(bankroll=100_000.0)
    asyncio.run(trader.run())
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from core.db import connect as db_connect
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.kalshi_client import KalshiClient
from core.kelly import MIN_EDGE, capped_kelly, position_size
from strategies.crypto.core.models import (
    KalshiMarket,
    Order,
    OrderStatus,
    Side,
    Signal,
    SignalType,
    TradeOpportunity,
    FeatureVector,
)
from tools.pipeline import Pipeline

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "paper_trades.db"

DEFAULT_SCAN_LIMIT = 50
DEFAULT_CYCLE_SECONDS = 300
MIN_VOLUME_24H = 5_000          # $5k 24h volume minimum on Kalshi
MIN_LIQUIDITY = 1_000           # $1k liquidity minimum
MIN_YES_PRICE = 0.04
MAX_YES_PRICE = 0.96
MIN_SPREAD_PCT = 0.04           # 4% full spread minimum to overcome Kalshi maker fees
MAX_SPREAD_PCT = 0.15
MAX_CONCURRENT_POSITIONS = 5
MAX_EXPOSURE_PCT = 0.10
MAX_DAILY_LOSS_PCT = 0.20


@dataclass(frozen=True)
class PaperPosition:
    order_id: str
    ticker: str
    title: str
    side: str
    entry_price: float
    size_usdc: float
    model_prob: float
    market_prob_at_entry: float
    edge_at_entry: float
    opened_at: str


@dataclass(frozen=True)
class PortfolioSnapshot:
    timestamp: str
    bankroll: float
    open_positions: int
    total_exposure: float
    unrealized_pnl: float
    realized_pnl: float
    daily_pnl: float
    trades_today: int
    win_rate: float


class PaperTrader:
    """
    Autonomous Kalshi paper trading loop.

    Each cycle:
      1. Fetch top Kalshi markets by 24h volume
      2. Filter by quality (volume, liquidity, spread, price range)
      3. Check existing positions for resolution
      4. Evaluate remaining candidates through Pipeline (Kelly + optional LLM analysts)
      5. Paper-fill qualifying opportunities
      6. Print portfolio state
    """

    def __init__(
        self,
        bankroll: float = 100_000.0,
        cycle_seconds: int = DEFAULT_CYCLE_SECONDS,
        scan_limit: int = DEFAULT_SCAN_LIMIT,
        min_edge: float = MIN_EDGE,
    ) -> None:
        self._initial_bankroll = bankroll
        self._bankroll = bankroll
        self._cycle_seconds = cycle_seconds
        self._scan_limit = scan_limit
        self._min_edge = min_edge
        self._pipeline = Pipeline(
            bankroll=bankroll,
            min_edge=min_edge,
        )
        self._client = KalshiClient()
        self._db = self._init_db()
        self._open_positions: dict[str, PaperPosition] = {}  # keyed by ticker
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0
        self._total_realized_pnl: float = 0.0
        self._halted: bool = False
        self._today: str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        self._load_open_positions()

    async def run(self) -> None:
        await self._client.open()
        try:
            _print_header(self._bankroll, self._cycle_seconds, self._min_edge)
            cycle = 0
            while True:
                cycle += 1
                self._maybe_reset_daily()
                _print_cycle_header(cycle)

                try:
                    markets = await self._client.get_top_markets(
                        limit=self._scan_limit,
                        min_volume_24h=MIN_VOLUME_24H,
                        min_liquidity=MIN_LIQUIDITY,
                    )
                    candidates = self._filter_candidates(markets)
                    print(f"  Fetched {len(markets)} markets, {len(candidates)} candidates")

                    await self._check_resolutions(markets)
                    await self._evaluate_and_trade(candidates)
                    self._print_portfolio()
                except Exception as exc:
                    print(f"  [ERROR] Cycle {cycle} failed: {exc}")
                    logger.exception("Cycle %d error", cycle)

                print(f"\n  Next cycle in {self._cycle_seconds}s...")
                await asyncio.sleep(self._cycle_seconds)
        finally:
            await self._client.close()

    async def run_once(self) -> PortfolioSnapshot:
        """Run a single scan-evaluate-trade cycle. Returns portfolio snapshot."""
        await self._client.open()
        try:
            self._maybe_reset_daily()
            markets = await self._client.get_top_markets(
                limit=self._scan_limit,
                min_volume_24h=MIN_VOLUME_24H,
                min_liquidity=MIN_LIQUIDITY,
            )
            candidates = self._filter_candidates(markets)
            await self._check_resolutions(markets)
            await self._evaluate_and_trade(candidates)
            return self._snapshot()
        finally:
            await self._client.close()

    # ------------------------------------------------------------------
    # Candidate filtering
    # ------------------------------------------------------------------

    def _filter_candidates(self, markets: list[KalshiMarket]) -> list[KalshiMarket]:
        results = []
        for m in markets:
            if m.ticker in self._open_positions:
                continue
            if m.implied_prob < MIN_YES_PRICE or m.implied_prob > MAX_YES_PRICE:
                continue
            if m.spread_pct > MAX_SPREAD_PCT:
                continue
            results.append(m)
        return results

    # ------------------------------------------------------------------
    # Evaluation and trading
    # ------------------------------------------------------------------

    async def _evaluate_and_trade(self, candidates: list[KalshiMarket]) -> None:
        if self._halted:
            print("  [HALTED] Circuit breaker active — skipping evaluation")
            return

        if len(self._open_positions) >= MAX_CONCURRENT_POSITIONS:
            print(f"  Max concurrent positions ({MAX_CONCURRENT_POSITIONS}) reached — skipping")
            return

        slots = MAX_CONCURRENT_POSITIONS - len(self._open_positions)
        traded = 0

        for m in candidates:
            if traded >= slots:
                break
            
            if m.spread_pct < MIN_SPREAD_PCT:
                continue

            try:
                result = await self._pipeline.evaluate(
                    market_question=m.title,
                    odds=m.implied_prob,
                    context=(
                        f"vol_24h=${m.volume_24h:,.0f}, "
                        f"liquidity=${m.liquidity:,.0f}, "
                        f"spread={m.spread_pct:.1%}"
                    ),
                )
            except Exception as exc:
                logger.warning("Evaluate failed for %s: %s", m.ticker, exc)
                continue

            if result.edge < self._min_edge or result.kelly_fraction <= 0:
                continue

            size = min(
                result.position_size_usdc,
                self._bankroll * MAX_EXPOSURE_PCT,
            )
            if size < 1.0:
                continue

            side = Side.YES if result.model_probability > m.implied_prob else Side.NO
            fill_price = m.yes_ask if side == Side.YES else m.no_ask
            if fill_price <= 0 or fill_price >= 1:
                continue

            pos = self._paper_fill(m, side, size, fill_price, result)
            self._record_trade(pos, result)
            traded += 1
            _print_trade(pos, result, side)

        if traded == 0:
            print("  No new trades this cycle")

    # ------------------------------------------------------------------
    # Paper fill
    # ------------------------------------------------------------------

    def _paper_fill(
        self,
        market: KalshiMarket,
        side: Side,
        size_usdc: float,
        fill_price: float,
        result,
    ) -> PaperPosition:
        now = datetime.now(tz=timezone.utc)
        order_id = f"paper_{now.timestamp():.0f}_{market.ticker[:12]}"

        pos = PaperPosition(
            order_id=order_id,
            ticker=market.ticker,
            title=market.title[:200],
            side=side.value,
            entry_price=fill_price,
            size_usdc=size_usdc,
            model_prob=result.model_probability,
            market_prob_at_entry=result.current_odds,
            edge_at_entry=result.edge,
            opened_at=now.isoformat(),
        )

        self._open_positions[market.ticker] = pos
        self._bankroll -= size_usdc
        self._daily_trades += 1
        return pos

    # ------------------------------------------------------------------
    # Resolution checking
    # ------------------------------------------------------------------

    async def _check_resolutions(self, markets: list[KalshiMarket]) -> None:
        """
        Check open positions against fresh market data.
        A position resolves when Kalshi closes the market (price → 0 or 1).
        """
        market_lookup = {m.ticker: m for m in markets}

        resolved = []
        for ticker, pos in list(self._open_positions.items()):
            m = market_lookup.get(ticker)
            if m is None:
                # Market no longer in top-N — fetch directly
                m = await self._client.get_market(ticker)
                if m is None:
                    continue

            # A resolved market has implied_prob near 0 or 1
            # or close_time is in the past
            resolution = _check_resolution(m)
            if resolution is not None:
                pnl = self._compute_pnl(pos, resolution)
                self._close_position(pos, resolution, pnl)
                resolved.append((pos, resolution, pnl))

        for pos, resolution, pnl in resolved:
            _print_resolution(pos, resolution, pnl)

    def _compute_pnl(self, pos: PaperPosition, resolution: str) -> float:
        won = (
            (pos.side == "YES" and resolution == "YES")
            or (pos.side == "NO" and resolution == "NO")
        )
        if won:
            payout = pos.size_usdc / pos.entry_price
            return payout - pos.size_usdc
        return -pos.size_usdc

    def _close_position(
        self, pos: PaperPosition, resolution: str, pnl: float
    ) -> None:
        self._open_positions.pop(pos.ticker, None)
        self._bankroll += pos.size_usdc + pnl
        self._daily_pnl += pnl
        self._total_realized_pnl += pnl

        if self._daily_pnl <= -(self._bankroll * MAX_DAILY_LOSS_PCT):
            self._halted = True
            print(f"  [CIRCUIT BREAKER] Daily loss ${abs(self._daily_pnl):.2f} exceeds limit")

        now = datetime.now(tz=timezone.utc).isoformat()
        try:
            self._db.execute(
                """
                UPDATE trades
                SET resolved_at = ?, resolution = ?, pnl_usdc = ?
                WHERE order_id = ?
                """,
                (now, resolution, pnl, pos.order_id),
            )
            self._db.commit()
        except sqlite3.Error as exc:
            logger.error("DB update error: %s", exc)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _record_trade(self, pos: PaperPosition, result) -> None:
        try:
            self._db.execute(
                """
                INSERT INTO trades (
                    order_id, ticker, title, side,
                    model_prob, market_prob, edge,
                    size_usdc, fill_price, status,
                    placed_at, filled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pos.order_id,
                    pos.ticker,
                    pos.title,
                    pos.side,
                    pos.model_prob,
                    pos.market_prob_at_entry,
                    pos.edge_at_entry,
                    pos.size_usdc,
                    pos.entry_price,
                    "FILLED",
                    pos.opened_at,
                    pos.opened_at,
                ),
            )
            self._db.commit()
        except sqlite3.Error as exc:
            logger.error("DB write error: %s", exc)

    def _load_open_positions(self) -> None:
        try:
            cursor = self._db.execute(
                """
                SELECT order_id, ticker, title, side,
                       fill_price, size_usdc, model_prob, market_prob,
                       edge, placed_at
                FROM trades WHERE resolution IS NULL AND pnl_usdc IS NULL
                """
            )
            for row in cursor.fetchall():
                pos = PaperPosition(
                    order_id=row[0],
                    ticker=row[1],
                    title=row[2],
                    side=row[3],
                    entry_price=row[4],
                    size_usdc=row[5],
                    model_prob=row[6],
                    market_prob_at_entry=row[7],
                    edge_at_entry=row[8],
                    opened_at=row[9],
                )
                self._open_positions[pos.ticker] = pos
                self._bankroll -= pos.size_usdc

            if self._open_positions:
                print(f"  Loaded {len(self._open_positions)} open positions from DB")
        except sqlite3.Error:
            pass

    def _init_db(self) -> sqlite3.Connection:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = db_connect(str(DB_PATH), check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                ticker TEXT,
                title TEXT,
                side TEXT,
                model_prob REAL,
                market_prob REAL,
                edge REAL,
                size_usdc REAL,
                fill_price REAL,
                status TEXT,
                placed_at TEXT,
                filled_at TEXT,
                resolved_at TEXT,
                resolution TEXT,
                pnl_usdc REAL
            )
        """)
        conn.commit()
        return conn

    # ------------------------------------------------------------------
    # Portfolio display
    # ------------------------------------------------------------------

    def _print_portfolio(self) -> None:
        snap = self._snapshot()
        print()
        print("  " + "─" * 56)
        print(f"  PORTFOLIO")
        print(f"    Bankroll:      ${snap.bankroll:>10,.2f}")
        print(f"    Open:          {snap.open_positions} positions (${snap.total_exposure:,.2f} deployed)")
        print(f"    Unrealized:    ${snap.unrealized_pnl:>+10,.2f}")
        print(f"    Realized:      ${snap.realized_pnl:>+10,.2f}")
        print(f"    Daily P&L:     ${snap.daily_pnl:>+10,.2f}")
        print(f"    Trades today:  {snap.trades_today}")
        if snap.win_rate >= 0:
            print(f"    Win rate:      {snap.win_rate:.0%}")
        print("  " + "─" * 56)

    def _snapshot(self) -> PortfolioSnapshot:
        total_exposure = sum(p.size_usdc for p in self._open_positions.values())

        cursor = self._db.execute(
            "SELECT COUNT(*), SUM(CASE WHEN pnl_usdc > 0 THEN 1 ELSE 0 END) "
            "FROM trades WHERE pnl_usdc IS NOT NULL"
        )
        row = cursor.fetchone()
        total_resolved = row[0] or 0
        total_wins = row[1] or 0
        win_rate = total_wins / total_resolved if total_resolved > 0 else -1

        return PortfolioSnapshot(
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            bankroll=self._bankroll,
            open_positions=len(self._open_positions),
            total_exposure=total_exposure,
            unrealized_pnl=0.0,  # needs live MTM pricing to compute
            realized_pnl=self._total_realized_pnl,
            daily_pnl=self._daily_pnl,
            trades_today=self._daily_trades,
            win_rate=win_rate,
        )

    def print_history(self, limit: int = 20) -> None:
        cursor = self._db.execute(
            """
            SELECT order_id, title, side, model_prob, market_prob, edge,
                   size_usdc, fill_price, placed_at, resolution, pnl_usdc
            FROM trades ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        if not rows:
            print("  No trade history yet.")
            return

        print(f"\n  {'Side':<4} {'Edge':>5} {'Size':>7} {'P&L':>8} {'Resolution':>10}  Title")
        print("  " + "─" * 70)
        for row in rows:
            _, title, side, _, _, edge, size, _, _, resolution, pnl = row
            pnl_str = f"${pnl:+.2f}" if pnl is not None else "open"
            res_str = resolution or "open"
            print(
                f"  {side:<4} {edge:>5.1%} ${size:>6.2f} {pnl_str:>8} {res_str:>10}  "
                f"{title[:45]}"
            )

    def _maybe_reset_daily(self) -> None:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        if today != self._today:
            self._daily_pnl = 0.0
            self._daily_trades = 0
            self._halted = False
            self._today = today


# ------------------------------------------------------------------
# Resolution detection
# ------------------------------------------------------------------

def _check_resolution(market: KalshiMarket) -> Optional[str]:
    """
    Detect if a Kalshi market has resolved.
    Returns "YES", "NO", or None if still open.

    Kalshi resolves YES at yes_bid ≈ 1.0, NO at yes_bid ≈ 0.0.
    Also resolves if close_time is in the past and price is extreme.
    """
    # Near-certain YES resolution
    if market.yes_bid >= 0.99:
        return "YES"
    if market.yes_ask <= 0.01:
        return "NO"

    # Check if close_time has passed
    if market.close_time:
        try:
            close = datetime.fromisoformat(market.close_time.replace("Z", "+00:00"))
            if close <= datetime.now(tz=timezone.utc):
                # Market is past close — resolution by price
                if market.implied_prob >= 0.95:
                    return "YES"
                elif market.implied_prob <= 0.05:
                    return "NO"
        except (ValueError, TypeError):
            pass

    return None


# ------------------------------------------------------------------
# Print helpers
# ------------------------------------------------------------------

def _print_header(bankroll: float, cycle_s: int, min_edge: float) -> None:
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║           QUANT PAPER TRADING ENGINE                ║")
    print("  ║                   Kalshi Markets                     ║")
    print("  ╚══════════════════════════════════════════════════════╝")
    print()
    print(f"  Bankroll:        ${bankroll:,.2f}")
    print(f"  Cycle:           every {cycle_s}s")
    print(f"  Min edge:        {min_edge:.0%}")
    print(f"  Max positions:   {MAX_CONCURRENT_POSITIONS}")
    print(f"  Max per trade:   {MAX_EXPOSURE_PCT:.0%} of bankroll (${bankroll * MAX_EXPOSURE_PCT:,.0f})")
    print(f"  Circuit breaker: {MAX_DAILY_LOSS_PCT:.0%} of bankroll (${bankroll * MAX_DAILY_LOSS_PCT:,.0f} daily loss)")
    print()


def _print_cycle_header(cycle: int) -> None:
    now = datetime.now(tz=timezone.utc).strftime("%H:%M:%S UTC")
    print(f"\n  ── Cycle {cycle} ({now}) {'─' * 36}")


def _print_trade(pos: PaperPosition, result, side: Side) -> None:
    arrow = "▲" if side == Side.YES else "▼"
    print(
        f"\n  {arrow} PAPER TRADE: {side.value} @ {pos.entry_price:.3f}"
        f"  ${pos.size_usdc:.2f}"
        f"  edge={pos.edge_at_entry:.1%}"
        f"  kelly={result.kelly_fraction:.1%}"
    )
    print(f"    [{pos.ticker}] {pos.title[:65]}")


def _print_resolution(pos: PaperPosition, resolution: str, pnl: float) -> None:
    icon = "✓" if pnl > 0 else "✗"
    print(
        f"\n  {icon} RESOLVED: {pos.title[:55]}"
        f"\n    {pos.side} @ {pos.entry_price:.3f} → {resolution}"
        f"  P&L: ${pnl:+.2f}"
    )
