"""
Post-trade edge analysis for paper trading.

Reads resolved trades from paper_trades.db and reports:
  - Win rate by side (YES/NO) and contract type (bracket/threshold)
  - P&L by edge bucket
  - Signal latency distribution
  - Sharpe ratio (daily, annualized)
  - Fee impact comparison

Usage:
    python -m research.edge_analysis
"""

from __future__ import annotations

import math
import sqlite3
from core.db import connect as db_connect
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "paper_trades.db"
KALSHI_TAKER_FEE_RATE = 0.07
TRADING_DAYS_PER_YEAR = 252


def _load_trades(db_path: Path) -> list[dict]:
    conn = db_connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT ticker, title, side, model_prob, market_prob, edge,
               size_usdc, fill_price, placed_at, resolved_at,
               resolution, pnl_usdc, spot_price_at_signal, signal_latency_ms
        FROM trades
        ORDER BY placed_at
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _raw_pnl(trade: dict) -> float:
    """Compute raw P&L without fees (for comparison)."""
    entry = trade["fill_price"] or 0.0
    size = trade["size_usdc"] or 0.0
    resolution = trade["resolution"]
    side = trade["side"]
    if not resolution or entry <= 0:
        return 0.0
    won = (side == "YES" and resolution == "YES") or \
          (side == "NO" and resolution == "NO")
    if won:
        return (size / entry) - size
    return -size


def _contract_type(ticker: str) -> str:
    if "-B" in ticker:
        return "bracket"
    if "-T" in ticker:
        return "threshold"
    return "unknown"


def _edge_bucket(edge: float) -> str:
    if edge < 0.04:
        return "3-4%"
    if edge < 0.05:
        return "4-5%"
    if edge < 0.07:
        return "5-7%"
    return "7%+"


def _report(trades: list[dict]) -> None:
    resolved = [t for t in trades if t["resolution"] is not None]
    open_trades = [t for t in trades if t["resolution"] is None]

    print("=" * 60)
    print("QUANT PAPER TRADE ANALYSIS")
    print("=" * 60)
    print(f"Total trades: {len(trades)}")
    print(f"Resolved:     {len(resolved)}")
    print(f"Open:         {len(open_trades)}")
    print()

    if not resolved:
        print("No resolved trades yet — nothing to analyze.")
        return

    # Win rate
    wins = [t for t in resolved if t["pnl_usdc"] is not None and t["pnl_usdc"] > 0]
    losses = [t for t in resolved if t["pnl_usdc"] is not None and t["pnl_usdc"] <= 0]
    win_rate = len(wins) / len(resolved) * 100
    print(f"Win rate: {len(wins)}/{len(resolved)} ({win_rate:.1f}%)")

    # Win rate by side
    for side in ("YES", "NO"):
        side_trades = [t for t in resolved if t["side"] == side]
        if side_trades:
            side_wins = [t for t in side_trades if t["pnl_usdc"] is not None and t["pnl_usdc"] > 0]
            pct = len(side_wins) / len(side_trades) * 100
            print(f"  {side}: {len(side_wins)}/{len(side_trades)} ({pct:.1f}%)")

    # Win rate by contract type
    print()
    for ctype in ("bracket", "threshold"):
        type_trades = [t for t in resolved if _contract_type(t["ticker"]) == ctype]
        if type_trades:
            type_wins = [t for t in type_trades if t["pnl_usdc"] is not None and t["pnl_usdc"] > 0]
            pct = len(type_wins) / len(type_trades) * 100
            print(f"  {ctype}: {len(type_wins)}/{len(type_trades)} ({pct:.1f}%)")

    # P&L summary
    total_pnl = sum(t["pnl_usdc"] for t in resolved if t["pnl_usdc"] is not None)
    total_raw = sum(_raw_pnl(t) for t in resolved)
    total_fee = total_raw - total_pnl
    print(f"\nTotal P&L (fee-adjusted): ${total_pnl:+.2f}")
    print(f"Total P&L (raw):          ${total_raw:+.2f}")
    print(f"Total fees paid:          ${total_fee:.2f}")

    # P&L by edge bucket
    print("\nP&L by edge bucket:")
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in resolved:
        if t["edge"] and t["pnl_usdc"] is not None:
            buckets[_edge_bucket(t["edge"])].append(t["pnl_usdc"])
    for bucket in ("3-4%", "4-5%", "5-7%", "7%+"):
        pnls = buckets.get(bucket, [])
        if pnls:
            avg = sum(pnls) / len(pnls)
            print(f"  {bucket}: {len(pnls)} trades, avg P&L ${avg:+.2f}, total ${sum(pnls):+.2f}")

    # Signal latency
    latencies = [t["signal_latency_ms"] for t in resolved if t["signal_latency_ms"] is not None and t["signal_latency_ms"] > 0]
    if latencies:
        print(f"\nSignal latency (ms):")
        print(f"  min={min(latencies):.0f}  median={sorted(latencies)[len(latencies)//2]:.0f}  max={max(latencies):.0f}  avg={sum(latencies)/len(latencies):.0f}")

    # Sharpe ratio (daily)
    daily_pnl: dict[str, float] = defaultdict(float)
    for t in resolved:
        if t["placed_at"] and t["pnl_usdc"] is not None:
            day = t["placed_at"][:10]
            daily_pnl[day] += t["pnl_usdc"]

    if len(daily_pnl) >= 2:
        returns = list(daily_pnl.values())
        mean_r = sum(returns) / len(returns)
        var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 0.0
        sharpe = (mean_r / std_r * math.sqrt(TRADING_DAYS_PER_YEAR)) if std_r > 0 else 0.0
        print(f"\nSharpe ratio (annualized): {sharpe:.2f}")
        print(f"  Daily mean: ${mean_r:+.2f}, daily std: ${std_r:.2f}")
        print(f"  Trading days: {len(daily_pnl)}")
    else:
        print("\nSharpe ratio: need 2+ trading days of data")

    # Average edge at entry
    edges = [t["edge"] for t in resolved if t["edge"]]
    if edges:
        print(f"\nAvg entry edge: {sum(edges)/len(edges)*100:.1f}%")

    print()


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"No database at {DB_PATH}")
        raise SystemExit(1)
    trades = _load_trades(DB_PATH)
    _report(trades)
