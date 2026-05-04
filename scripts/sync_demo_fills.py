"""Phase 0 of crypto-only pivot: sync real Kalshi demo fills into local DB.

Hits demo-api.kalshi.co with the DEMO credentials, pages /portfolio/fills,
and inserts each fill as a row into data/paper_trades.db with environment='PAPER'.
Enriches with /portfolio/positions to populate resolved fills' P&L where the
exchange has settled the market.

Fields the bot would normally write at decision time (model_prob, edge,
signal_latency_ms, realized_vol, kelly_fraction) are left NULL — these fills
were placed manually or by a daemon whose decision context isn't in the API
response.

Auth pre-flight calls /portfolio/balance first so we fail loudly on bad creds
rather than silently writing zero rows.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.kalshi_client import KalshiClient  # noqa: E402

DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
DATA_DB = REPO_ROOT / "data" / "paper_trades.db"

INSERT_SQL = """
INSERT INTO trades (
    order_id, ticker, side, fill_price, size_usdc,
    status, placed_at, filled_at, resolved_at, resolution, pnl_usdc,
    environment
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def load_demo_creds() -> tuple[str, str]:
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("KALSHI_API_KEY_DEMO") or os.environ.get("KALSHI_API_KEY")
    pem_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH_DEMO") or os.environ.get(
        "KALSHI_PRIVATE_KEY_PATH"
    )
    if not api_key or not pem_path:
        sys.exit("ERROR: KALSHI_API_KEY_DEMO + KALSHI_PRIVATE_KEY_PATH_DEMO required in .env")
    if not Path(pem_path).exists():
        sys.exit(f"ERROR: PEM file not found at {pem_path}")
    return api_key, pem_path


async def fetch_all_fills(client: KalshiClient) -> list[dict]:
    """Page /portfolio/fills until exhausted."""
    fills: list[dict] = []
    cursor: Optional[str] = None
    page = 0
    while True:
        params: dict = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = await client._get("/portfolio/fills", params=params)
        batch = data.get("fills") or []
        fills.extend(batch)
        page += 1
        print(f"  page {page}: +{len(batch)} fills (running total {len(fills)})")
        cursor = data.get("cursor")
        if not cursor or not batch:
            break
    return fills


async def fetch_settlements(client: KalshiClient) -> dict[str, dict]:
    """Map market ticker → settlement dict (for P&L + resolution enrichment).

    Pages /portfolio/settlements until exhausted.
    """
    by_ticker: dict[str, dict] = {}
    cursor: Optional[str] = None
    page = 0
    while True:
        params: dict = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = await client._get("/portfolio/settlements", params=params)
        batch = data.get("settlements") or []
        for s in batch:
            t = s.get("ticker")
            if t:
                by_ticker[t] = s
        page += 1
        print(f"  page {page}: +{len(batch)} settlements (running total {len(by_ticker)})")
        cursor = data.get("cursor")
        if not cursor or not batch:
            break
    return by_ticker


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def fill_to_row(fill: dict, settlements: dict[str, dict]) -> tuple:
    """Map a Kalshi V2 fill JSON → trades-table row tuple.

    Kalshi V2 fields used: market_ticker, side ("yes"/"no"), no_price_dollars /
    yes_price_dollars (decimal-string dollars), count_fp (decimal-string contracts),
    created_time, fill_id/trade_id/order_id.
    """
    ticker = fill.get("market_ticker") or fill.get("ticker")
    side = (fill.get("side") or "").upper()

    if side == "NO":
        fill_price = _to_float(fill.get("no_price_dollars"))
    elif side == "YES":
        fill_price = _to_float(fill.get("yes_price_dollars"))
    else:
        fill_price = _to_float(fill.get("yes_price_dollars"))

    count = _to_float(fill.get("count_fp")) or 0.0
    size_usdc = fill_price * count if fill_price is not None else None
    created_at = fill.get("created_time")

    s = settlements.get(ticker)
    if s is not None:
        market_result = (s.get("market_result") or "").upper()
        revenue_cents = s.get("revenue")
        revenue_dollars = (revenue_cents / 100.0) if isinstance(revenue_cents, (int, float)) else 0.0
        cost_field = "no_total_cost_dollars" if side == "NO" else "yes_total_cost_dollars"
        cost_dollars = _to_float(s.get(cost_field)) or 0.0
        pnl_usdc = revenue_dollars - cost_dollars
        resolved_at = s.get("settled_time")
        resolution = market_result or None
        status = "RESOLVED"
    else:
        pnl_usdc = None
        resolved_at = None
        resolution = None
        status = "FILLED"

    return (
        fill.get("trade_id") or fill.get("fill_id") or fill.get("order_id"),
        ticker,
        side,
        fill_price,
        size_usdc,
        status,
        created_at,
        created_at,
        resolved_at,
        resolution,
        pnl_usdc,
        "PAPER",
    )


def write_rows(rows: list[tuple]) -> int:
    conn = sqlite3.connect(str(DATA_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executemany(INSERT_SQL, rows)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    conn.close()
    return n


def print_synced_table() -> None:
    conn = sqlite3.connect(str(DATA_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT placed_at, ticker, side, fill_price, size_usdc, status, "
        "resolution, pnl_usdc FROM trades ORDER BY placed_at DESC"
    ).fetchall()
    conn.close()

    if not rows:
        print("  (no rows)")
        return

    print(f"  {'placed_at':30}  {'ticker':35}  {'side':4}  {'fill':>6}  {'size':>8}  {'status':9}  {'res':4}  {'pnl':>8}")
    print(f"  {'-'*30}  {'-'*35}  {'-'*4}  {'-'*6}  {'-'*8}  {'-'*9}  {'-'*4}  {'-'*8}")
    for r in rows:
        placed = (r["placed_at"] or "")[:30]
        ticker = (r["ticker"] or "")[:35]
        fp = f"{r['fill_price']:.3f}" if r["fill_price"] is not None else "-"
        sz = f"{r['size_usdc']:.2f}" if r["size_usdc"] is not None else "-"
        res = r["resolution"] or "-"
        pnl = f"{r['pnl_usdc']:+.2f}" if r["pnl_usdc"] is not None else "-"
        print(f"  {placed:30}  {ticker:35}  {r['side']:4}  {fp:>6}  {sz:>8}  {r['status']:9}  {res:4}  {pnl:>8}")


async def main() -> int:
    api_key, pem_path = load_demo_creds()
    print(f"[sync] base_url={DEMO_BASE_URL}")
    print(f"[sync] api_key={api_key[:8]}…  pem={pem_path}")

    if not DATA_DB.exists():
        sys.exit(f"ERROR: {DATA_DB} missing. Run scripts/wipe_local_dbs.py first.")

    async with KalshiClient(
        api_key=api_key, private_key_path=pem_path, base_url=DEMO_BASE_URL
    ) as client:
        print("\n[sync] auth pre-flight: GET /portfolio/balance")
        bal = await client._get("/portfolio/balance")
        if not bal:
            sys.exit(
                "ERROR: balance call returned empty — auth likely failed. "
                "Check that the public key for KALSHI_API_KEY_DEMO is uploaded "
                "to demo-api.kalshi.co (separate dashboard from prod)."
            )
        cents = bal.get("balance", 0)
        print(f"  balance: ${cents/100:,.2f}  ({bal})")

        print("\n[sync] fetching fills…")
        fills = await fetch_all_fills(client)
        if not fills:
            print("  no fills returned. If you expected some, check that this account has demo trades.")
            return 0

        print("\n[sync] fetching settlements for resolution + P&L enrichment…")
        settlements = await fetch_settlements(client)
        print(f"  {len(settlements)} settlements on file")

        rows = [fill_to_row(f, settlements) for f in fills]
        n_after = write_rows(rows)
        print(f"\n[sync] inserted {len(rows)} rows. trades table now has {n_after} rows.")

    print("\n[sync] === synced rows (verify against your demo account) ===\n")
    print_synced_table()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
