## System overview

Spot-price propagation latency arbitrage on Kalshi crypto binary contracts. When BTC or ETH moves on Binance/Coinbase, Kalshi reprices seconds-to-minutes behind. The system measures that divergence with Black-Scholes N(d2) against Welford-estimated realized vol, enters when edge exceeds the threshold, and sizes positions with fee-adjusted Kelly criterion.

No learned parameters. No heuristics. Every decision is a deterministic function of spot price, realized vol, and the pricing model.

## Pipeline

```
CryptoFeedAgent ──► FeatureAgent ──► ScannerAgent ──► RiskAgent ──► ExecutionAgent ──► ResolutionAgent
                                          ▲
                                    WebsocketAgent
                                   (real-time price cache)
```

All agents are `async`. Coordination via typed `asyncio.Queue` instances and a read-only WebSocket price cache.

Entry point: `daemon.py` at repo root.

## Package layout

```
latency/             ← repo root IS the package (has __init__.py)
  agents/            Async execution layer — seven concurrent agents
  core/              Pure math + models — no I/O, no side effects
  tests/             Pytest suite (11 modules, AAA pattern)
  benchmarks/        Hot-path profiling
  research/          P&L analysis tools (replay_backtest, health_check)
  data/              SQLite trade log (paper_trades.db — gitignored)
  deploy/            Docker + GCE deployment config
```

## Core invariants

- `core/pricing.py` and `core/kelly.py` are pure math — frozen, do not touch.
- `core/config.py` is the single source of truth for every numeric threshold.
- Paper mode is default. `EXECUTION_MODE=live` raises `NotImplementedError` until gates are met.
- Every trade persisted to SQLite with full audit trail.
- `RiskAgent` encapsulates all position state — never access private attributes directly.

## Empirical status (as of 2026-04-24)

Paper trading at **BANKROLL_USDC=10,000**. ~80 resolved fills.

Latest replay_backtest output:
- Win rate: 95.0%
- Mean return per fill: +17.3% (normalized: pnl / size_usdc — bankroll-agnostic)
- Best fill: +113.6% | Worst fill: -106.5%
- Annualized Sharpe (est.): 14.73 (paper; will compress in live due to queue/market impact)
- Calibration: 0.90–1.00 bucket at 97.5% model vs 97.8% realized — well-calibrated

**Live trading gate** (both required):
```python
min_fills_for_live: int = 100
min_sharpe_for_live: float = 1.0
```

~20 more resolved fills needed at ~4 fills/day.

## Next milestone: 100 fills → live

At 100 fills + Sharpe ≥ 1.0, implement `_live_order()` in `agents/execution_agent.py:85–93` (currently raises `NotImplementedError`). Start at **$10k live capital**, 25% Kelly sizing for first 30 live fills.

## Production deployment — GCE

The daemon runs 24/7 on a GCP Compute Engine VM (`e2-small`, `us-central1-a`, ~$14/mo).

**VM:** `kinzie-daemon` | IP: `34.134.196.29` | Project: `project-41e99557-708c-4594-ba5`

The daemon runs inside Docker, managed by systemd (`kinzie.service`) with `Restart=always`. It survives crashes and reboots automatically.

**Monitor logs (live tail):**
```bash
gcloud compute ssh kinzie-daemon --zone=us-central1-a --project=project-41e99557-708c-4594-ba5 -- sudo journalctl -fu kinzie
```

**Check service status:**
```bash
gcloud compute ssh kinzie-daemon --zone=us-central1-a --project=project-41e99557-708c-4594-ba5 -- sudo systemctl status kinzie
```

**Run health check on VM:**
```bash
gcloud compute ssh kinzie-daemon --zone=us-central1-a --project=project-41e99557-708c-4594-ba5 -- sudo docker exec deploy-daemon-1 python3 -m research.health_check
```

**Run replay_backtest on VM:**
```bash
gcloud compute ssh kinzie-daemon --zone=us-central1-a --project=project-41e99557-708c-4594-ba5 -- sudo docker exec deploy-daemon-1 python3 -m research.replay_backtest
```

**Restart the daemon:**
```bash
gcloud compute ssh kinzie-daemon --zone=us-central1-a --project=project-41e99557-708c-4594-ba5 -- sudo systemctl restart kinzie
```

**Redeploy after code changes:**
```bash
# Upload new source
gcloud compute scp --recurse --compress --zone=us-central1-a --project=project-41e99557-708c-4594-ba5 /Users/noahdonovan/kinzie/. kinzie-daemon:/opt/kinzie/

# Restart (docker will rebuild the image)
gcloud compute ssh kinzie-daemon --zone=us-central1-a --project=project-41e99557-708c-4594-ba5 -- sudo systemctl restart kinzie
```

**Files on VM:**
- Source: `/opt/kinzie/`
- Secrets: `/opt/kinzie/.env` and `/opt/kinzie/kalshi_private.pem`
- SQLite DB: Docker named volume `deploy_kinzie-data` (persists across restarts)

**Gotcha:** `docker-compose.yml` uses `--project-directory /opt/kinzie` in `kinzie.service` so variable substitution for the PEM volume mount picks up `.env` from the repo root, not the `deploy/` subdirectory.

## Running locally

```bash
pip install -e ".[dev]"
BANKROLL_USDC=10000 PYTHONPATH=. python3 daemon.py
pytest tests/
python3 -m research.health_check
python3 -m research.replay_backtest
```

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KALSHI_API_KEY` | Yes | — | UUID from Kalshi dashboard |
| `KALSHI_PRIVATE_KEY_PATH` | Yes | — | Path to RSA-2048 PEM file |
| `BANKROLL_USDC` | No | 100000 | Starting capital — **set to 10000 for paper** |
| `EXECUTION_MODE` | No | paper | `paper` or `live` |
| `TRACKED_SYMBOLS` | No | BTC,ETH | Comma-separated symbols |

All `core/config.py` fields overridable via env var (see `Config.from_env()`).

## Kalshi API

- Base URL: `https://api.elections.kalshi.com/trade-api/v2`
- Auth: RSA-PSS SHA-256. Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, `KALSHI-ACCESS-TIMESTAMP`
- V2 price fields: `yes_ask`/`yes_bid` are integer cents (1–99); `_parse_market()` divides by 100.
- Rate limit: 429 → exponential backoff (max 5 retries, cap 30s).

## Key design decisions

**Why N(d2) not N(d1)?** Prediction markets pay $1 on resolution — no delta-hedging. N(d2) is the risk-neutral probability that S_T > K.

**Why 0.25× Kelly cap?** Unverified edge at current fill count. Review at N=100 fills.

**Why `BRACKET_CALIBRATION=0.55`?** Log-normal model overestimates narrow bracket probabilities. Provisional — needs 50+ bracket fills to validate.

**Why normalized returns in replay_backtest?** Mixed dataset: first 81 fills at $100k bankroll, remainder at $10k. `pnl / size_usdc` is bankroll-agnostic.
