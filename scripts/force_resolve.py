"""
Force-resolve stuck paper positions.

Marks unresolved positions as EXPIRED_MANUAL with pnl_usdc=0.0 so the
daemon can resume trading.  Shows what it will do and asks for confirmation.

Usage:
    python scripts/force_resolve.py              # resolve all stuck positions
    python scripts/force_resolve.py KXETH-26APR  # resolve only matching tickers
"""

from __future__ import annotations

import sqlite3
from core.db import connect as db_connect
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "paper_trades.db"


def main() -> int:
    pattern = sys.argv[1] if len(sys.argv) > 1 else None

    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        return 1

    conn = db_connect(str(DB_PATH))

    # Find unresolved positions
    query = "SELECT id, ticker, side, fill_price, size_usdc, filled_at FROM trades WHERE resolution IS NULL"
    params: tuple = ()
    if pattern:
        query += " AND ticker LIKE ?"
        params = (f"%{pattern}%",)

    rows = conn.execute(query, params).fetchall()

    if not rows:
        print("No unresolved positions found.")
        conn.close()
        return 0

    print(f"\nFound {len(rows)} unresolved position(s):\n")
    print(f"  {'ID':<4} {'TICKER':<32} {'SIDE':<4} {'ENTRY':>6} {'SIZE':>10} {'FILLED_AT'}")
    print(f"  {'--':<4} {'--':<32} {'--':<4} {'--':>6} {'--':>10} {'--'}")
    for row in rows:
        pid, ticker, side, entry, size, filled = row
        print(f"  {pid:<4} {ticker:<32} {side:<4} {entry:>6.3f} ${size:>9.2f} {filled}")

    print(f"\nThis will set resolution='EXPIRED_MANUAL' and pnl_usdc=0.0 for all {len(rows)} positions.")
    answer = input("Proceed? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        conn.close()
        return 0

    now = datetime.now(tz=timezone.utc).isoformat()
    ids = [row[0] for row in rows]
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE trades SET resolved_at = ?, resolution = 'EXPIRED_MANUAL', pnl_usdc = 0.0 WHERE id IN ({placeholders})",
        [now, *ids],
    )
    conn.commit()
    conn.close()

    print(f"Resolved {len(rows)} position(s). Restart the daemon to resume trading.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
