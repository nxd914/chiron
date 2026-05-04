"""Phase 0 of crypto-only pivot: wipe local DBs to a clean canonical state.

Drops the `trades` table in data/paper_trades.db and recreates it with the
canonical schema (matching strategies/crypto/agents/execution_agent.py::_init_db),
which includes the `environment` column. Also deletes the base/ directory
(stale duplicate DB, slated for removal in Phase 2 anyway).

GCE `kinzie-data` Docker volume is NOT touched — local-only operation.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DB = REPO_ROOT / "data" / "paper_trades.db"
BASE_DIR = REPO_ROOT / "base"

CANONICAL_SCHEMA = """
CREATE TABLE trades (
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
    pnl_usdc REAL,
    spot_price_at_signal REAL,
    signal_latency_ms REAL,
    realized_vol REAL,
    kelly_fraction REAL,
    environment TEXT
)
"""


def wipe_data_db() -> None:
    DATA_DB.parent.mkdir(parents=True, exist_ok=True)
    if DATA_DB.exists():
        for sidecar in (DATA_DB.with_suffix(".db-wal"), DATA_DB.with_suffix(".db-shm")):
            if sidecar.exists():
                sidecar.unlink()
        DATA_DB.unlink()
        print(f"  removed: {DATA_DB.relative_to(REPO_ROOT)} (+ wal/shm sidecars)")
    else:
        print(f"  not present (skipping): {DATA_DB.relative_to(REPO_ROOT)}")

    conn = sqlite3.connect(str(DATA_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(CANONICAL_SCHEMA)
    conn.commit()

    cols = [row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()]
    count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    conn.close()

    print(f"  recreated: {DATA_DB.relative_to(REPO_ROOT)}")
    print(f"  schema columns ({len(cols)}): {', '.join(cols)}")
    print(f"  row count: {count}")
    assert "environment" in cols, "environment column missing — schema drift!"
    assert count == 0


def remove_base_dir() -> None:
    if BASE_DIR.exists():
        shutil.rmtree(BASE_DIR)
        print(f"  removed: {BASE_DIR.relative_to(REPO_ROOT)}/")
    else:
        print(f"  not present (skipping): {BASE_DIR.relative_to(REPO_ROOT)}/")


def main() -> None:
    print("[wipe] data/paper_trades.db")
    wipe_data_db()
    print()
    print("[wipe] base/")
    remove_base_dir()
    print()
    print("Done. Local DB is empty with canonical schema; base/ is gone.")
    print("Next: run scripts/sync_demo_fills.py to populate from Kalshi demo API.")


if __name__ == "__main__":
    main()
