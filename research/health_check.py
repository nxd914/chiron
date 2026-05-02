"""
Health check for the Quant paper trading loop.

Reads the live log tail and queries paper_trades.db to print a concise
CLI summary.  Exit code 0 = healthy, 1 = stale / no recent activity.

Usage:
    python -m trading.research.health_check
    python trading/research/health_check.py
"""

from __future__ import annotations

import os
import sqlite3
from core.db import connect as db_connect
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT    = Path(__file__).parent.parent
DATA_DIR     = REPO_ROOT / "data"
DB_PATH      = REPO_ROOT / "data" / "paper_trades.db"
LOG_PATH     = DATA_DIR / "paper_fund.log"
PID_PATH     = DATA_DIR / "paper_fund.pid"
LOG_TAIL     = 200        # lines to read from log
STALE_HOURS  = 2          # alert if no fill in 2+ hours during market hours
BANKROLL_USDC = float(os.environ.get("BANKROLL_USDC", "100000.0"))


# ── formatting helpers ──────────────────────────────────────────────────────

def _bar(val: float, total: float, width: int = 20) -> str:
    if total <= 0:
        return "─" * width
    filled = int(round(val / total * width))
    return "█" * filled + "░" * (width - filled)


def _pnl_str(val: float, pct: float | None = None) -> str:
    sign = "+" if val >= 0 else ""
    color = "\033[92m" if val >= 0 else "\033[91m"
    reset = "\033[0m"
    base = f"{color}{sign}${val:.2f}{reset}"
    if pct is None:
        return base
    pct_sign = "+" if pct >= 0 else ""
    return f"{base}  ({color}{pct_sign}{pct:.2f}%{reset})"


def _age(ts_str: str) -> str:
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        delta = datetime.now(tz=timezone.utc) - ts
        s = int(delta.total_seconds())
        if s < 60:
            return f"{s}s ago"
        if s < 3600:
            return f"{s//60}m ago"
        return f"{s//3600}h {(s%3600)//60}m ago"
    except Exception:
        return ts_str


# ── process status ──────────────────────────────────────────────────────────

def _process_status() -> tuple[str, int | None]:
    if not PID_PATH.exists():
        return "STOPPED", None
    try:
        pid = int(PID_PATH.read_text().strip())
        os.kill(pid, 0)
        return "RUNNING", pid
    except (ValueError, ProcessLookupError, PermissionError):
        return "DEAD", None


# ── log analysis ────────────────────────────────────────────────────────────

def _read_log_tail() -> list[str]:
    if not LOG_PATH.exists():
        return []
    try:
        with open(LOG_PATH, "r", errors="replace") as f:
            lines = f.readlines()
        return [l.rstrip() for l in lines[-LOG_TAIL:]]
    except OSError:
        return []


def _log_stats(lines: list[str]) -> dict:
    stats: dict = {
        "rate_limits": 0,
        "sports_events": 0,
        "scanner_fetches": [],
        "signal_matches": [],
        "orders": 0,
        "errors": 0,
    }
    for line in lines:
        ll = line.lower()
        if "rate limit" in ll:
            stats["rate_limits"] += 1
        if "sports:" in ll and ("goal" in ll or "card" in ll):
            stats["sports_events"] += 1
        if "scanner: fetched" in ll:
            try:
                n = int(line.split("fetched")[1].split("markets")[0].strip())
                stats["scanner_fetches"].append(n)
            except Exception:
                pass
        if "signal" in ll and "matched" in ll:
            try:
                n = int(line.split("matched")[1].split("/")[0].strip())
                stats["signal_matches"].append(n)
            except Exception:
                pass
        if "order" in ll and "filled" in ll:
            stats["orders"] += 1
        if " error" in ll or "exception" in ll:
            stats["errors"] += 1
    return stats


# ── db queries ──────────────────────────────────────────────────────────────

def _db_stats() -> dict:
    result: dict = {
        "total": 0,
        "resolved": 0,
        "open": 0,
        "wins": 0,
        "losses": 0,
        "total_pnl": 0.0,
        "daily_pnl": 0.0,
        "last_fill": None,
        "recent_trades": [],
        "db_ok": False,
    }
    if not DB_PATH.exists():
        return result
    try:
        conn = db_connect(str(DB_PATH))
        # Detect ticker column name
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        ticker_col = "ticker" if "ticker" in cols else "condition_id"
        title_col  = "title"  if "title"  in cols else "question"

        row = conn.execute("SELECT COUNT(*) FROM trades").fetchone()
        result["total"] = row[0] if row else 0

        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE resolution IS NOT NULL"
        ).fetchone()
        result["resolved"] = row[0] if row else 0
        result["open"] = result["total"] - result["resolved"]

        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE resolution IS NOT NULL AND pnl_usdc > 0"
        ).fetchone()
        result["wins"] = row[0] if row else 0

        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE resolution IS NOT NULL AND pnl_usdc <= 0"
        ).fetchone()
        result["losses"] = row[0] if row else 0

        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_usdc), 0.0) FROM trades WHERE pnl_usdc IS NOT NULL"
        ).fetchone()
        result["total_pnl"] = float(row[0]) if row else 0.0

        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl_usdc), 0.0) FROM trades "
            "WHERE pnl_usdc IS NOT NULL AND placed_at LIKE ?",
            (f"{today}%",),
        ).fetchone()
        result["daily_pnl"] = float(row[0]) if row else 0.0

        row = conn.execute(
            "SELECT MAX(filled_at) FROM trades WHERE filled_at IS NOT NULL"
        ).fetchone()
        result["last_fill"] = row[0] if row and row[0] else None

        rows = conn.execute(
            f"SELECT {ticker_col}, side, fill_price, size_usdc, resolution, pnl_usdc, filled_at "
            f"FROM trades ORDER BY id DESC LIMIT 8"
        ).fetchall()
        result["recent_trades"] = rows
        result["db_ok"] = True
        conn.close()
    except Exception as exc:
        result["db_error"] = str(exc)
    return result


# ── staleness check ─────────────────────────────────────────────────────────

def _is_stale(last_fill: str | None) -> bool:
    if last_fill is None:
        return False  # no fills yet — not stale, just new
    try:
        ts = datetime.fromisoformat(last_fill.replace("Z", "+00:00"))
        hours = (datetime.now(tz=timezone.utc) - ts).total_seconds() / 3600
        return hours >= STALE_HOURS
    except Exception:
        return False


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    lines  = _read_log_tail()
    log    = _log_stats(lines)
    db     = _db_stats()
    status, pid = _process_status()

    W = "═" * 50

    print(f"\n{W}")
    print(f"  QUANT PAPER FUND — HEALTH CHECK")
    print(f"  {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(W)

    # Process
    proc_icon = "🟢" if status == "RUNNING" else "🔴"
    pid_str   = f"PID {pid}" if pid else "no PID"
    print(f"\n  Process  {proc_icon} {status} ({pid_str})")

    # P&L
    total_pct = db["total_pnl"] / BANKROLL_USDC * 100
    daily_pct = db["daily_pnl"] / BANKROLL_USDC * 100
    print(f"\n  ── P&L ──────────────────────────────────")
    print(f"  Total realized P&L : {_pnl_str(db['total_pnl'], total_pct)}")
    print(f"  Today's P&L        : {_pnl_str(db['daily_pnl'], daily_pct)}")

    # Trades
    resolved = db["resolved"]
    wins     = db["wins"]
    wr       = (wins / resolved * 100) if resolved > 0 else 0.0
    print(f"\n  ── Trades ───────────────────────────────")
    print(f"  Total fills        : {db['total']}")
    print(f"  Open positions     : {db['open']}")
    print(f"  Resolved           : {resolved}  (wins {wins} / losses {db['losses']})")
    print(f"  Win rate           : {wr:.1f}%  {_bar(wins, max(resolved, 1))}")

    # Last fill staleness
    if db["last_fill"]:
        stale = _is_stale(db["last_fill"])
        age_icon = "⚠️ " if stale else "  "
        print(f"  Last fill          : {age_icon}{_age(db['last_fill'])}")

    # Log activity (last 200 lines)
    print(f"\n  ── Log activity (last {LOG_TAIL} lines) ───────")
    avg_fetch = (
        sum(log["scanner_fetches"]) / len(log["scanner_fetches"])
        if log["scanner_fetches"] else 0
    )
    print(f"  Sports events      : {log['sports_events']}")
    print(f"  Scanner fetches    : {len(log['scanner_fetches'])}  (avg {avg_fetch:.0f} markets)")
    print(f"  Signal scans (cum) : {sum(log['signal_matches'])}")
    print(f"  Rate limits (429)  : {log['rate_limits']}")
    print(f"  Errors             : {log['errors']}")

    # Recent trades table
    if db["recent_trades"]:
        print(f"\n  ── Recent trades ────────────────────────")
        print(f"  {'TICKER':<32} {'SIDE':<4} {'ENTRY':>6} {'SIZE':>7} {'RES':<4} {'P&L':>8}  AGE")
        print(f"  {'─'*32} {'─'*4} {'─'*6} {'─'*7} {'─'*4} {'─'*8}  {'─'*8}")
        for t in db["recent_trades"]:
            ticker, side, entry, size, res, pnl, filled_at = t
            ticker_s = (ticker or "")[:32]
            res_s    = res or "open"
            pnl_s    = f"${pnl:+.2f}" if pnl is not None else "  —"
            age_s    = _age(filled_at) if filled_at else "?"
            print(f"  {ticker_s:<32} {side or '?':<4} {entry or 0:>6.3f} ${size or 0:>6.2f} {res_s:<4} {pnl_s:>8}  {age_s}")

    print(f"\n{W}\n")

    # Exit code
    stale = _is_stale(db["last_fill"])
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
