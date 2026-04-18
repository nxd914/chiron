# contributor reference

## System overview

Spot-price propagation latency arbitrage on Kalshi crypto binary contracts. When BTC or ETH moves sharply on Binance or Coinbase, Kalshi's order book reprices seconds-to-minutes behind. The system measures that divergence with closed-form Black-Scholes N(d2) against Welford-estimated realized volatility, enters when edge exceeds 4%, and sizes positions with fee-adjusted Kelly criterion.

No learned parameters. No heuristics. Every decision in the execution path is a deterministic function of spot price, realized vol, and the pricing model.

## Pipeline

```
CryptoFeedAgent ──► FeatureAgent ──► ScannerAgent ──► RiskAgent ──► ExecutionAgent ──► ResolutionAgent
                                          ▲
                                    WebsocketAgent
                                   (real-time price cache)
```

All agents are `async`. Coordination is through typed `asyncio.Queue` instances and a read-only WebSocket price cache. No shared mutable state between agents.

Entry point: `daemon.py` at repo root.

## Package layout

```
latency/             ← repo root IS the package (has __init__.py)
  agents/            Async execution layer — seven concurrent agents
  core/              Pure math + models — no I/O, no side effects
  tests/             Pytest suite (11 modules, AAA pattern)
  benchmarks/        Hot-path profiling — RollingWindow, N(d2), Kelly
  research/          Data capture, market scanning, P&L analysis tools
  docs/              Strategy, risk model, calibration derivations
  data/              SQLite trade log (paper_trades.db — gitignored)
  deploy/            Docker / Railway deployment config
```

Run with `PYTHONPATH=/path/to/parent` (the directory containing `latency/`).

## Core invariants

- `core/pricing.py` and `core/kelly.py` are pure math — no side effects, no I/O, no state.
- `core/config.py` is the single source of truth for every numeric threshold. Agents import from `DEFAULT_CONFIG`; never redefine constants.
- All models in `core/models.py` are frozen dataclasses — immutable throughout the pipeline.
- Paper mode is default. `EXECUTION_MODE=live` raises `NotImplementedError` until `MIN_FILLS_FOR_LIVE=100` and `MIN_SHARPE_FOR_LIVE=1.0` are met.
- Every trade is persisted to SQLite with a full audit trail (spot price at signal, latency, vol, Kelly fraction).
- Spread floor 4%. Kelly cap 0.25×. Max 5 concurrent positions.
- `RiskAgent` encapsulates all position state. `ResolutionAgent` uses the public `restore_position()` / `restore_daily_pnl()` interface — never accesses private attributes directly.

## Running locally

```bash
pip install -e ".[dev]"
PYTHONPATH=. python3 daemon.py              # run all agents
pytest tests/                              # 11 test modules
python3 -m benchmarks.hot_path             # hot-path latency profile
python3 -m research.health_check           # P&L + process health
```

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KALSHI_API_KEY` | Yes | — | UUID from Kalshi dashboard |
| `KALSHI_PRIVATE_KEY_PATH` | Yes | — | Path to RSA-2048 PEM file |
| `BANKROLL_USDC` | No | 100000 | Starting capital |
| `EXECUTION_MODE` | No | paper | `paper` or `live` |
| `TRACKED_SYMBOLS` | No | BTC,ETH | Comma-separated symbols |

All `core/config.py` fields are also overridable via env var (see `Config.from_env()`).

## Kalshi API

- Base URL: `https://api.elections.kalshi.com/trade-api/v2`
- Auth: RSA-PSS SHA-256. Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, `KALSHI-ACCESS-TIMESTAMP`
- V2 price fields: `yes_ask`/`yes_bid` are integer cents (1–99); `_parse_market()` divides by 100.
- Rate limit: 429 → exponential backoff (max 5 retries, cap 30s). Never run two processes against the same key.
- WebSocket: global `ticker` channel for real-time price updates.

## Key design decisions

**Why Welford algorithm for vol?** O(1) amortized update with bounded memory (deque). Rolling window with exact tick expiry. No full-scan recompute on each tick.

**Why N(d2) not N(d1)?** Prediction markets pay $1 on resolution — no delta-hedging possible. N(d2) is the risk-neutral probability that S_T > K, which is exactly what the contract resolves on.

**Why 0.25× Kelly cap?** Unverified edge at current fill count. Standard conservative multiplier for research-grade systems. Review at N=100 fills with full Sharpe history.

**Why `BRACKET_CALIBRATION=0.55`?** Log-normal model overestimates narrow bracket probabilities due to TWAP settlement and discrete jump dynamics. See `docs/CALIBRATION.md`. Provisional — needs 50+ bracket fills to validate.
