"""
Quant Paper P&L Dashboard

Displays metrics, open positions, and resolved trades from the paper ledger.
"""

import math
import sqlite3
from core.db import connect as db_connect
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "paper_trades.db"
STARTING_BANKROLL = 5_000.00


def _age_str(placed_at_str: str) -> str:
    try:
        dt = datetime.fromisoformat(placed_at_str)
        delta = datetime.now(timezone.utc) - dt
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return f"{total_seconds}s"
        if total_seconds < 3600:
            return f"{total_seconds // 60}m"
        if total_seconds < 86400:
            return f"{total_seconds // 3600}h"
        return f"{total_seconds // 86400}d"
    except (ValueError, TypeError):
        return "N/A"


def _ticker_str(row: sqlite3.Row) -> str:
    ticker = row["ticker"]
    if not ticker:
        ticker = "UNKNOWN"
    return ticker[:15]


def calculate_daily_sharpe(resolved_trades: list[sqlite3.Row]) -> float:
    if not resolved_trades:
        return 0.0

    daily_pnl = defaultdict(float)
    for t in resolved_trades:
        # Group by day resolved (or day filled if resolved_at missing)
        ts_str = t["resolved_at"] or t["filled_at"] or t["placed_at"]
        if not ts_str:
            continue
        try:
            day = ts_str[:10]  # YYYY-MM-DD
            daily_pnl[day] += (t["pnl_usdc"] or 0.0)
        except Exception:
            continue

    if len(daily_pnl) < 2:
        return 0.0

    # Calculate daily returns (rough estimation relative to starting bankroll)
    returns = []
    current_br = STARTING_BANKROLL
    # Sort days
    for day, pnl in sorted(daily_pnl.items()):
        if current_br <= 0:
            current_br = 1.0  # arbitrary floor to prevent div zero
        returns.append(pnl / current_br)
        current_br += pnl

    if not returns:
        return 0.0

    mean_ret = statistics.mean(returns)
    std_ret = statistics.stdev(returns) if len(returns) > 1 else 0.0
    if std_ret == 0.0:
        return 0.0
    return (mean_ret / std_ret) * math.sqrt(365)


def main() -> None:
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        return

    conn = db_connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Load trades
    cursor.execute("SELECT * FROM trades ORDER BY id DESC")
    all_trades = cursor.fetchall()
    
    # Pre-process subsets
    open_positions = [t for t in all_trades if t["status"] == "FILLED"]
    resolved_trades = [t for t in all_trades if t["status"] == "RESOLVED"]
    
    total_trades = len(open_positions) + len(resolved_trades)
    realized_pnl = sum((t["pnl_usdc"] or 0.0) for t in resolved_trades)
    
    wins = sum(1 for t in resolved_trades if (t["pnl_usdc"] or 0) > 0)
    win_rate = (wins / len(resolved_trades) * 100) if resolved_trades else 0.0
    
    sharpe = calculate_daily_sharpe(resolved_trades)
    
    # Header
    print("════════════════════════════════════")
    print("  QUANT PAPER P&L DASHBOARD")
    print("════════════════════════════════════")
    print(f"  Bankroll start:    ${STARTING_BANKROLL:,.2f}")
    print(f"  Realized P&L:       {'+' if realized_pnl >= 0 else ''}${realized_pnl:,.2f}")
    print(f"  Open positions:     {len(open_positions)}")
    print(f"  Total trades:       {total_trades}")
    print(f"  Win rate:           {win_rate:.1f}%")
    print(f"  Sharpe (daily):     {sharpe:.2f}")
    print("\nOPEN POSITIONS")
    print("  {:<15} {:<5} {:<7} {:<7} {:<5}".format("TICKER", "SIDE", "ENTRY", "SIZE", "AGE"))
    if not open_positions:
        print("  (None)")
    else:
        for t in open_positions:
            ticker = _ticker_str(t)
            side = str(t["side"])[:4]
            entry = f"${t['fill_price']:.2f}" if t["fill_price"] else "N/A"
            size = f"${t['size_usdc']:.2f}" if t["size_usdc"] else "N/A"
            age = _age_str(t["filled_at"] or t["placed_at"])
            print("  {:<15} {:<5} {:<7} {:<7} {:<5}".format(ticker, side, entry, size, age))

    print("\nRESOLVED TRADES (last 20)")
    print("  {:<15} {:<5} {:<7} {:<8} {:<8}".format("TICKER", "SIDE", "ENTRY", "OUTCOME", "P&L"))
    if not resolved_trades:
        print("  (None)")
    else:
        for t in resolved_trades[:20]:
            ticker = _ticker_str(t)
            side = str(t["side"])[:4]
            entry = f"${t['fill_price']:.2f}" if t["fill_price"] else "N/A"
            outcome = str(t["resolution"])[:7] if t["resolution"] else "N/A"
            pnl = (t["pnl_usdc"] or 0.0)
            pnl_str = f"{'+' if pnl >= 0 else ''}${pnl:.2f}"
            print("  {:<15} {:<5} {:<7} {:<8} {:<8}".format(ticker, side, entry, outcome, pnl_str))


if __name__ == "__main__":
    main()
