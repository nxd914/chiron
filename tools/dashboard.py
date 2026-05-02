"""
Quant Observation Dashboard

Premium web interface for monitoring the autonomous trading firm performance.
Uses FastAPI to serve real-time metrics from the paper_trades.db.
"""

import math
import sqlite3
from core.db import connect as db_connect
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn

# --- Configuration -----------------------------------------------------------

DB_PATH = Path(__file__).parent.parent / "data" / "paper_trades.db"
TEMPLATES_PATH = Path(__file__).parent / "web" / "templates"
STARTING_BANKROLL = 5_000.00

app = FastAPI(title="Quant Dashboard")
templates = Jinja2Templates(directory=str(TEMPLATES_PATH))

# --- Helpers -----------------------------------------------------------------

def get_db_stats():
    if not DB_PATH.exists():
        return {
            "realized_pnl": 0.0,
            "win_rate": 0.0,
            "open_risk": 0.0,
            "total_resolved": 0,
            "total_open": 0,
            "sharpe": 0.0,
            "trades": []
        }

    conn = db_connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # Load all trades sorted by recency
        cursor.execute("SELECT * FROM trades ORDER BY id DESC")
        all_trades = cursor.fetchall()

        # Metrics
        open_positions = [t for t in all_trades if t["status"] == "FILLED"]
        resolved_trades = [t for t in all_trades if t["status"] == "RESOLVED"]

        total_resolved = len(resolved_trades)
        total_open = len(open_positions)
        
        realized_pnl = sum((t["pnl_usdc"] or 0.0) for t in resolved_trades)
        open_risk = sum((t["size_usdc"] or 0.0) for t in open_positions)
        
        wins = sum(1 for t in resolved_trades if (t["pnl_usdc"] or 0) > 0)
        win_rate = (wins / total_resolved * 100) if total_resolved > 0 else 0.0

        # Sharpe calculation (Daily)
        daily_pnl = defaultdict(float)
        for t in resolved_trades:
            ts_str = t["resolved_at"] or t["filled_at"] or t["placed_at"]
            if ts_str:
                day = ts_str[:10]
                daily_pnl[day] += (t["pnl_usdc"] or 0.0)
        
        sharpe = 0.0
        if len(daily_pnl) >= 2:
            returns = []
            curr_br = STARTING_BANKROLL
            for _, pnl in sorted(daily_pnl.items()):
                returns.append(pnl / curr_br)
                curr_br += pnl
            
            mean_ret = statistics.mean(returns)
            std_ret = statistics.stdev(returns)
            if std_ret > 0:
                sharpe = (mean_ret / std_ret) * math.sqrt(365)

        return {
            "realized_pnl": realized_pnl,
            "win_rate": win_rate,
            "open_risk": open_risk,
            "total_resolved": total_resolved,
            "total_open": total_open,
            "sharpe": sharpe,
            "trades": all_trades[:20]  # Send last 20 trades to UI
        }
    except Exception as e:
        print(f"Dashboard error: {e}")
        return {
            "realized_pnl": 0.0,
            "win_rate": 0.0,
            "open_risk": 0.0,
            "total_resolved": 0,
            "total_open": 0,
            "sharpe": 0.0,
            "trades": []
        }
    finally:
        conn.close()

# --- Routes ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    stats = get_db_stats()
    return templates.TemplateResponse("index.html", {
        "request": request,
        **stats
    })

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

# --- Entry point -------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
