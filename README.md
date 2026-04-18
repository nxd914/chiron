# chiron

[![Python](https://img.shields.io/badge/python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Kalshi](https://img.shields.io/badge/exchange-Kalshi-0a1628.svg)](https://kalshi.com)
[![Tests](https://img.shields.io/badge/tests-pytest-009688.svg?logo=pytest&logoColor=white)](./tests)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

Automated latency arbitrage between real-time CEX spot feeds and Kalshi BTC/ETH binary prediction markets.

**The edge**: Binance.US and Coinbase WebSocket feeds deliver spot price updates sub-second. Kalshi contract prices lag by several seconds to minutes after a large spot move. During that window, the Black-Scholes implied probability diverges from Kalshi's order book price. We enter when the divergence exceeds 4%, sized by Kelly criterion, and exit at settlement.

> Paper trading only. Not financial advice.

## Architecture

```
Binance.US WS ──┐
                ├── Tick → RollingWindow (Welford O(1)) → FeatureVector → Signal
Coinbase WS ────┘                                                            │
                                                                             ▼
Kalshi WS ──────────────────────────────── price_cache → ScannerAgent._score()
                                                          N(d2) vs Kalshi ask
                                                                             │
                                                          TradeOpportunity (edge > 4%)
                                                                             │
                                                          RiskAgent._evaluate()
                                                          Kelly sizing, position limits
                                                                             │
                                                          ExecutionAgent (paper fill → SQLite)
                                                                             │
                                                          ResolutionAgent (settlement poll, P&L)
```

All agents are async tasks. No learned parameters in the execution path — every decision is a deterministic function of spot price, realized vol, Kelly math, and hard-coded risk limits.

## Quick Start

```bash
git clone https://github.com/nxd914/chiron.git && cd chiron
pip install -e ".[dev]"

mkdir -p ~/.chiron
openssl genrsa -out ~/.chiron/private.pem 2048
openssl rsa -in ~/.chiron/private.pem -pubout -out ~/.chiron/public.pem
```

Create `.env` at repo root:

```bash
KALSHI_API_KEY=your-uuid-here
KALSHI_PRIVATE_KEY_PATH=~/.chiron/private.pem
BANKROLL_USDC=100000
```

Run:

```bash
PYTHONPATH=. python3 daemon.py          # all agents
pytest tests/                           # test suite (11 modules)
python3 -m benchmarks.hot_path          # profile hot path
python3 -m research.health_check        # P&L + process health
```

## Pricing Model

Threshold contracts (YES resolves if spot > K):

```
d2 = (ln(S/K) − 0.5σ²t) / (σ√t)
P(S_T > K) = N(d2)
```

No risk-free rate — prediction markets carry no financing cost. Volatility is the 15-minute Welford realized vol, annualized. No learned parameters.

Kelly sizing with fee adjustment:

```
effective_price = ask + 0.07 × P × (1−P)   # Kalshi taker fee
b = (1 / effective_price) − 1               # net odds
f* = (p·b − (1−p)) / b                      # Kelly fraction
position = min(f* × 0.25, 0.10) × bankroll  # capped at 0.25× Kelly, 10% max
```

## Risk Controls

All parameters are defined in `core/config.py` with documented derivations. See `docs/RISK_MODEL.md` for the reasoning behind each value.

| Control | Value | Derivation |
|---------|-------|-----------|
| Kelly cap | 0.25× | Absorbs model prob estimation error without ruin |
| Min edge | 4% | Covers Kalshi taker fee at worst-case spread |
| Max concurrent positions | 5 | 50% max capital deployed; leaves margin buffer |
| Max single exposure | 10% of bankroll | Per-position concentration limit |
| Daily loss circuit breaker | 20% of bankroll | Halt on correlated loss scenario |
| Consecutive-loss halt | 3 losses → 24h pause | Catches edge decay below percentage threshold |
| Max signal age | 2 seconds | Stale signal = Kalshi already repriced |
| Max hours to expiry | 4h | Latency arb requires convergence pressure |
| Spread floor | 4% | No maker rebates; tight spread = edge already gone |
| Burst cooldown | 30s between fills | Prevents correlated fill cascade from single signal |
| NO fill range | [0.40, 0.95] | Risk/reward bounds on NO-side positions |

## Repository Structure

```
core/           Pure math — pricing, Kelly, features, models, config
agents/         Async execution layer — seven concurrent agents
tests/          Pytest suite — 11 test modules, AAA pattern
benchmarks/     Hot-path profiling — RollingWindow, N(d2), Kelly
research/       Data capture, P&L analysis, market scanning tools
docs/
  STRATEGY.md   Edge thesis, pricing model, execution flow
  RISK_MODEL.md Every risk control with derivation and motivation
  CALIBRATION.md BRACKET_CALIBRATION derivation and validation plan
  strategy.md   Detailed code walkthrough
deploy/         Railway / Docker configuration
```

## Validation Status

Paper trading is live. Live mode requires:

- 100+ resolved fills (current: 8)
- Sharpe ≥ 1.0 over all fills (current: n insufficient)
- Calibration error < 10% on threshold contracts

These gates are machine-readable constants in `core/config.py` (`MIN_FILLS_FOR_LIVE`, `MIN_SHARPE_FOR_LIVE`). `EXECUTION_MODE=live` raises `NotImplementedError` until they are met.

## Development

```bash
pip install -e ".[dev]"
pytest tests/                    # full test suite
python3 -m benchmarks.hot_path   # measure RollingWindow/N(d2)/Kelly latency
```

## License

MIT — see [LICENSE](./LICENSE).
