"""Single-purpose ROI dashboard for the crypto strategy.

Reads `data/paper_trades.db` (mounted from the GCE `kinzie-data` Docker volume
in production; local copy is stale per CLAUDE.md). Prints win/loss/open counts,
gross + today ROI, fill latency percentiles, recent error count, and an open
positions table.

Usage:
  python3 -m research.live_roi
  BANKROLL_USDC=10000 python3 -m research.live_roi
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.db import connect as db_connect  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "paper_trades.db"
LATENCY_LOOKBACK = 100
RECENT_LOG_LINES = 10


def _has_environment_column(conn: sqlite3.Connection) -> bool:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    return "environment" in cols


def _env_filter_sql(has_env_col: bool) -> str:
    """Case-insensitive environment filter per CLAUDE.md invariant."""
    if not has_env_col:
        return ""
    return "AND lower(environment) IN ('paper', 'live')"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct / 100.0 * (len(s) - 1)))))
    return s[k]


def _pretty_age(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found.")
        return 1

    bankroll = float(os.environ.get("BANKROLL_USDC", "100000"))
    conn = db_connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    has_env_col = _has_environment_column(conn)
    env_filter = _env_filter_sql(has_env_col)

    if not has_env_col:
        print("WARN: trades.environment column missing — counting ALL rows (sim + live mixed).")

    print(f"\n=== Crypto live ROI — DB: {DB_PATH.relative_to(REPO_ROOT)} ===")
    print(f"Bankroll basis: ${bankroll:,.2f}\n")

    # Win/loss/open
    resolved_rows = conn.execute(
        f"""
        SELECT side, resolution, pnl_usdc
        FROM trades
        WHERE status = 'RESOLVED' {env_filter}
        """
    ).fetchall()
    open_count = conn.execute(
        f"""
        SELECT COUNT(*) FROM trades
        WHERE status IN ('FILLED', 'PENDING') AND resolved_at IS NULL {env_filter}
        """
    ).fetchone()[0]

    wins = sum(1 for r in resolved_rows if (r["pnl_usdc"] or 0) > 0)
    losses = sum(1 for r in resolved_rows if (r["pnl_usdc"] or 0) < 0)
    breakevens = len(resolved_rows) - wins - losses
    total_resolved = wins + losses
    win_rate = (wins / total_resolved) if total_resolved else 0.0
    gross_pnl = sum((r["pnl_usdc"] or 0.0) for r in resolved_rows)
    gross_roi = gross_pnl / bankroll if bankroll else 0.0

    # Today (UTC) ROI
    today_start = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    today_pnl_row = conn.execute(
        f"""
        SELECT COALESCE(SUM(pnl_usdc), 0) AS pnl
        FROM trades
        WHERE status = 'RESOLVED' AND placed_at >= ? {env_filter}
        """,
        (today_start,),
    ).fetchone()
    today_pnl = float(today_pnl_row["pnl"] or 0.0)
    today_roi = today_pnl / bankroll if bankroll else 0.0

    print(f"Wins:        {wins}")
    print(f"Losses:      {losses}")
    if breakevens:
        print(f"Breakeven:   {breakevens}")
    print(f"Open:        {open_count}")
    print(f"Win rate:    {win_rate:.1%}  ({wins}/{total_resolved})")
    print(f"Gross P&L:   ${gross_pnl:+,.2f}")
    print(f"Gross ROI:   {gross_roi:+.3%}  (vs ${bankroll:,.0f})")
    print(f"Today P&L:   ${today_pnl:+,.2f}  ({today_roi:+.3%})")

    # Fill latency
    latency_rows = conn.execute(
        f"""
        SELECT signal_latency_ms FROM trades
        WHERE signal_latency_ms IS NOT NULL AND signal_latency_ms > 0 {env_filter}
        ORDER BY id DESC LIMIT ?
        """,
        (LATENCY_LOOKBACK,),
    ).fetchall()
    latencies = [float(r["signal_latency_ms"]) for r in latency_rows]
    if latencies:
        mean = sum(latencies) / len(latencies)
        print(
            f"\nFill latency (last {len(latencies)} fills): "
            f"mean={mean:.0f}ms  p50={_percentile(latencies, 50):.0f}ms  "
            f"p95={_percentile(latencies, 95):.0f}ms"
        )
    else:
        print("\nFill latency: no signal_latency_ms recorded yet")

    # Open positions table
    open_rows = conn.execute(
        f"""
        SELECT ticker, side, fill_price, size_usdc, placed_at, edge, model_prob
        FROM trades
        WHERE status IN ('FILLED', 'PENDING') AND resolved_at IS NULL {env_filter}
        ORDER BY placed_at DESC
        """
    ).fetchall()

    if open_rows:
        print(f"\nOpen positions ({len(open_rows)}):")
        print(
            f"  {'ticker':38}  {'side':4}  {'fill':>6}  "
            f"{'size':>8}  {'edge':>6}  {'age':>6}"
        )
        print(f"  {'-' * 38}  {'-' * 4}  {'-' * 6}  {'-' * 8}  {'-' * 6}  {'-' * 6}")
        now = datetime.now(tz=timezone.utc)
        for r in open_rows:
            ticker = (r["ticker"] or "")[:38]
            side = r["side"] or "-"
            fp = f"{r['fill_price']:.3f}" if r["fill_price"] is not None else "-"
            sz = f"${r['size_usdc']:.2f}" if r["size_usdc"] is not None else "-"
            edge = f"{r['edge']:.3f}" if r["edge"] is not None else "-"
            try:
                placed = datetime.fromisoformat((r["placed_at"] or "").replace("Z", "+00:00"))
                age = _pretty_age((now - placed).total_seconds())
            except (ValueError, TypeError):
                age = "?"
            print(f"  {ticker:38}  {side:4}  {fp:>6}  {sz:>8}  {edge:>6}  {age:>6}")
    else:
        print("\nNo open positions.")

    # Recent ERROR-level log lines (best-effort)
    log_path = REPO_ROOT / "data" / "kinzie.log"
    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            errors = [ln.rstrip() for ln in lines if " ERROR " in ln][-RECENT_LOG_LINES:]
            if errors:
                print(f"\nRecent ERROR log lines (last {len(errors)}):")
                for ln in errors:
                    print(f"  {ln[:200]}")
        except OSError as exc:
            print(f"\n(log tail unavailable: {exc})")
    else:
        print("\n(no kinzie.log on disk — daemon logs go to stdout/Docker)")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
