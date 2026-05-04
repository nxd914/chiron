## ⚠ Session orientation — read before doing anything

- **Crypto-only.** Every other strategy (econ/sports/weather) was deleted on 2026-05-03 in
  the hard pivot. The only daemon, the only edge source, the only thing in this repo is
  the Kalshi crypto latency-arb bot.
- **GCE is canonical.** The local `data/paper_trades.db` is a synced snapshot of demo fills
  (see `scripts/sync_demo_fills.py`). Live state lives in the GCE `kinzie-data` Docker volume.
  Run monitoring on GCE: `<SSH prefix> sudo docker exec kinzie-daemon-1 python3 -m research.live_roi`
  SSH prefix and VM details live in `RUNBOOK.local.md` (gitignored).
- **`EXECUTION_MODE=paper`** means real orders against `demo-api.kalshi.co` (Kalshi demo).
  `EXECUTION_MODE=live` means real orders against prod (real money). `local_sim` is gone.
- **`environment` column** in the `trades` table stores uppercase: `PAPER`, `LIVE`.
  Filtering code must use case-insensitive comparison (`lower(environment) IN ('paper','live')`).
- **`core/db.py`** is the required SQLite WAL helper. It must be present in the Docker
  image. `ModuleNotFoundError: No module named 'core.db'` → image is stale; rebuild and redeploy.

## What this is

Single-strategy quant bot targeting **Kalshi crypto binary markets** (BTC, ETH; symbols
extendable via `TRACKED_SYMBOLS`). Closed-form Black-Scholes pricing vs Welford realized
vol drives a fee-adjusted Kelly sizing decision. Currently in **demo-book testing** —
N=4 real fills as of 2026-05-03 (3W/1L, -$35.50 net on $336 risked). No published
baseline yet; re-baselining from demo fills.

## Pipeline

`CryptoFeedAgent → FeatureAgent → ScannerAgent → RiskAgent → ExecutionAgent → ResolutionAgent`
(plus `WebsocketAgent` feeding ticker prices into FeatureAgent and account-level fill
events into a queue for sub-second confirms).

Entry point: `strategies/crypto/daemon.py`. Docker service: `daemon`. Container:
`kinzie-daemon-1`.

## Edge source

- `strategies/crypto/core/pricing.py` — `spot_to_implied_prob()` is Black-Scholes N(d2)
  with an **optional** `drift=0.0` parameter (default no-op; passes momentum-EWMA when
  N≥50 fills justify it). `bracket_prob()` is N(d2_floor) − N(d2_cap) × `BRACKET_CALIBRATION`
  (currently 0.55).
- `core/kelly.py` — fee-adjusted quarter-Kelly sizing. The `KALSHI_TAKER_FEE_RATE`
  parabolic fee (`0.07 × P × (1−P)` per contract) peaks at P=0.5 and is folded into
  the breakeven gate.
- `RiskAgent` rejects opportunities where `edge < kalshi_fee(P) + ESTIMATED_SLIPPAGE`.
  `ESTIMATED_SLIPPAGE` defaults to 0.005; **recalibrate from demo fills once N≥50**.
- Per-expiry position cap (1) prevents correlated same-hour bets — added 2026-04 after
  loss concentration analysis.

The math knobs are no longer "frozen" — they were unfrozen during the crypto pivot
and may be tuned as fill data accumulates. Tests in `tests/test_pricing.py` and
`tests/test_pricing_properties.py` lock in invariants (drift=0 backward-compat,
monotonicity, valid-probability ranges).

## Core invariants

- All trades persisted to SQLite at `data/paper_trades.db` (WAL mode via `core/db.connect()`).
  Canonical DB lives in the `kinzie-data` Docker volume on GCE; local DB is a synced snapshot.
- `trades.environment` column values: `PAPER`, `LIVE`. Filter case-insensitively.
- `EXECUTION_MODE` resolved exactly once at daemon startup via `core/environment.py`. Every
  agent receives the resolved `Environment` — they never read env vars directly.
  Demo and prod creds in separate env vars (`KALSHI_API_KEY_{DEMO,LIVE}` +
  matching `_PATH_*`). The resolver fails fast on missing creds and refuses obvious
  mismatches (e.g. `demo` in PEM filename when mode=live).
- **Order Groups safety net**: at startup, daemon creates an order group with a
  rolling 15-second matched-contracts cap (`ORDER_GROUP_CONTRACTS_LIMIT`, default 300).
  Every order placed via `ExecutionAgent` carries the group_id; if a pricing bug fires
  runaway orders, the exchange auto-cancels every order in the group. Free safety.
- **V2 order endpoint**: `core/kalshi_client.py::place_limit_order()` POSTs
  `count_fp` + `yes_price_dollars` (string decimal) per the V2 schema. Legacy integer
  cents fields are deprecated and removed from response payloads as of 2026-03-12.
- **WS user_fills subscription**: `WebsocketAgent` subscribes to `["ticker", "fill"]`
  and exposes `fill_events: asyncio.Queue` for sub-second account-level fill confirms.
  ExecutionAgent's place→fill confirmation can be wired through this queue when needed
  (currently uses synchronous POST response).
- Flip-to-live: see `RUNBOOK.local.md`. Requires `_LIVE` creds set.

## Monitoring

> Always run on GCE — local DB is a snapshot. SSH prefix in `RUNBOOK.local.md`.

| What | Command (on GCE via `docker exec kinzie-daemon-1`) |
|------|----------------------------------------------------|
| **Headline ROI** (win rate, gross+today P&L, latency, open table) | `python3 -m research.live_roi` |
| Crypto P&L (Sharpe, age, full open table) | `python3 -m research.pnl_dashboard` |
| Health / last fill / errors | `python3 -m research.health_check` |
| Replay backtest | `python3 -m research.replay_backtest` |
| Per-trade edge analysis | `python3 -m research.edge_analysis` |

## Production deployment — GCE

e2-small VM, us-central1-a, ~$14/mo. Source `/opt/kinzie/` · Secrets `/opt/kinzie/.env`
+ `kalshi_private.pem` · DB volume `kinzie-data`.

Single Docker service: `daemon`. Container: `kinzie-daemon-1`.

Gotcha: `kinzie.service` passes `--project-directory /opt/kinzie` so `.env` loads from
repo root, not `deploy/`.

SSH prefix, redeploy commands, VM IP, and project ID are in **`RUNBOOK.local.md`**
(gitignored).

## Environment variables

- `EXECUTION_MODE` — `paper` (default, Kalshi demo) | `live` (Kalshi prod).
- `KALSHI_API_KEY_DEMO` + `KALSHI_PRIVATE_KEY_PATH_DEMO` — demo creds.
- `KALSHI_API_KEY_LIVE` + `KALSHI_PRIVATE_KEY_PATH_LIVE` — prod creds.
- Legacy `KALSHI_API_KEY` / `KALSHI_PRIVATE_KEY_PATH` — research-script fallback only.
- `BANKROLL_USDC` — sizing basis (default 100000; use 1064 to match real demo balance).
- `TRACKED_SYMBOLS` — default `BTC,ETH`. Add `SOL,XRP,DOGE,BNB,HYPE` once symbol-feed
  configs exist.
- `ESTIMATED_SLIPPAGE` — breakeven-gate slippage (default 0.005). Recalibrate from
  fills once N≥50.
- `ORDER_GROUP_CONTRACTS_LIMIT` — rolling 15s contracts cap (default 300).
- All other `strategies/crypto/core/config.py` numerics overridable via `Config.from_env()`.

## Kalshi API

- Base: `https://api.elections.kalshi.com/trade-api/v2` (prod) / `https://demo-api.kalshi.co/trade-api/v2` (demo).
- Auth: RSA-PSS SHA-256 signed headers per request.
- Order placement: POST `/portfolio/orders` with `count_fp` + `yes_price_dollars`.
- Order groups: POST `/portfolio/order_groups/create` returns `order_group_id`.
- WebSocket: `wss://demo-api.kalshi.co/trade-api/ws/v2` — subscribe to `ticker` for
  prices and `fill` for account-level fill events.
- `_parse_market()` divides legacy cents fields by 100; modern `*_dollars` fields used as-is.
- 429 → exponential backoff (max 5 retries, cap 30s).

## Future ROI levers (not yet implemented)

- Expand `TRACKED_SYMBOLS` to SOL/XRP/DOGE/BNB/HYPE (wiring exists; needs symbol-specific
  feed configs).
- 15-Minute Up/Down market support (different market type, different pricing horizon).
- Batch `Create Orders V2` — single round-trip for multi-strike opportunities.
- Wire `pricing.py` `drift` parameter from `FeatureAgent.short_return` / momentum-EWMA
  once N≥50 demo fills justify the bias.
- Replace synchronous order-confirm with `WebsocketAgent.fill_events` consumer in
  `ExecutionAgent` for sub-second fill verification.
