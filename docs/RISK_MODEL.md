# Risk Model

Every control in this system has a specific derivation. This document explains each one: what it is, why it exists, and what evidence or reasoning motivated the value.

The authoritative source for all numeric values is `core/config.py`. This document explains the reasoning; the code enforces it.

---

## Kelly Fraction Cap: 0.25×

**What**: Maximum fraction of bankroll allocated to any single position, expressed as a multiple of the full Kelly fraction.

**Why**: Full Kelly (f* = (p·b - q)/b) maximizes geometric growth rate but requires perfect probability estimates. In practice, model_prob is estimated from a finite realized vol sample with a log-normal assumption that breaks down near ATM and at short horizons. Standard quant risk management caps Kelly at 0.25×–0.5× to absorb estimation error without ruin. At 0.25×, a 50% overestimate of edge reduces the bet to near-zero rather than causing aggressive overbetting.

**What it means in practice**: at 0.25× Kelly with 10% max single-exposure, the largest position is `min(0.25 × f* × bankroll, 0.10 × bankroll)`. Both caps apply; the tighter one binds.

---

## Minimum Edge: 4%

**What**: The scanner rejects any opportunity where `|model_prob - market_implied_prob| < 0.04`.

**Why**: Kalshi's taker fee is `0.07 × P × (1-P)` per contract. At P=0.5 (worst case), that's 1.75%. With a typical bid-ask spread of 2–4% on liquid markets, effective round-trip cost is 3–5%. A 4% edge floor ensures positive expected value after fees even in the worst-case spread scenario.

**Calibration risk**: the 4% floor assumes we pay the full ask. If we improve to limit orders at mid, the effective cost drops and this floor could be lowered. Currently set conservatively for taker-only execution.

---

## Max Concurrent Positions: 5

**What**: At most 5 open positions at any time.

**Why**: With 10% max single exposure, 5 positions = 50% of bankroll deployed. Leaves 50% undeployed as margin against adverse simultaneous moves across all positions. Binary contracts can go to zero; with 5 positions of 10% each, a complete wipeout of all 5 (p ≈ 0.35^5 ≈ 0.5% under typical model accuracy) costs 50% of bankroll — covered by the daily loss circuit breaker before that point.

---

## Daily Loss Circuit Breaker: 20% of bankroll

**What**: If realized daily P&L drops below -20% of bankroll, all trading halts until the next UTC day.

**Why**: 20% represents a plausible worst-case scenario from 5 simultaneous losses at full single-exposure (5 × 10% = 50% at risk, but Kelly-sized positions are typically 3–7% each). The 20% level gives meaningful protection while allowing recovery from a bad-luck cluster without being so tight that a single losing day shuts down valid trading.

**Proactive gate**: the circuit breaker also blocks new positions if `daily_pnl - pending_worst_case_exposure - new_size < -20% × bankroll`. This prevents the gate from tripping mid-position on a correlated loss.

---

## Consecutive-Loss Streak Halt: 3 consecutive losses → 24h pause

**What**: If the last 3 resolved fills are all losses, trading pauses for 24 hours.

**Why**: The daily loss gate is percentage-based and doesn't catch *rate* of loss. Three consecutive losses from small positions might not breach 20% of bankroll, but they may signal edge decay — Kalshi pricing caught up, the model is miscalibrated for current vol regime, or there's a systematic error in signal generation. 24-hour pause allows manual review before resuming. Distinct from the daily gate; both can fire independently.

**Threshold choice**: 3 is empirically chosen as the minimum sample to distinguish bad luck from edge decay. At model_prob=0.65, the probability of 3 consecutive losses is 0.35^3 ≈ 4.3% — low enough to warrant pause without triggering on normal variance.

---

## Max Signal Age: 2 seconds

**What**: Opportunities derived from signals older than 2 seconds are rejected by RiskAgent.

**Why**: The edge is spot-price propagation latency. If a signal fires because spot moved at t=0, and we evaluate the opportunity at t=2.5s, the Kalshi book may have already repriced. 2 seconds is conservative for Kalshi's update frequency (observed: 3–10s lag in paper trading, but this is not formally measured). The gate should be tuned down once we have empirical data on Kalshi's actual update cadence.

**Most important architectural implication**: the 2-second gate means the periodic scan (every 120s) will almost always fail the signal freshness check — opportunities from the periodic path use synthetic signals with `timestamp = now`, bypassing the age gate. Signal-triggered scans are the only path where this gate has real effect.

---

## Spread Floor: 4%

**What**: Markets with `(yes_ask - yes_bid) / implied_prob < 4%` are rejected.

**Why**: Tight spreads indicate either high liquidity (competitive market, edge already arbed away) or a data artifact. Kalshi doesn't offer maker rebates, so there's no advantage to participating in a tight-spread market as a taker. The 4% floor is symmetric with the min-edge requirement: we need 4% edge to trade, so markets with spreads narrower than 4% likely have all mispricing immediately absorbed.

---

## NO Fill Price Band: [0.40, 0.95]

**Floor (0.40)**: At NO=0.39, you risk $0.39 per contract to win $0.61 (1.56:1 payout). The risk/reward is acceptable but the absolute stake is large relative to YES-side alternatives with similar edge. Below 0.40, NO positions require very high win rates to overcome fee drag.

**Cap (0.95)**: At NO=0.95, you risk $0.95 to win $0.05 (19:1 against). Requires 95%+ win rate to break even after fees. Even with a model probability of 0.97, the Kelly fraction is near zero — the cap prevents edge-free positions from slipping through at extreme prices.

---

## Max Hours to Expiry: 4

**What**: Contracts expiring more than 4 hours out are skipped.

**Why**: The latency arb thesis requires a convergence mechanism. For near-expiry contracts, any rational participant observing the same spot price will reprice toward fair value as settlement approaches. Far-dated contracts have no such pressure — they can stay mispriced for days. 4 hours is the balance between having enough time for settlement to converge and having a meaningful mispricing window to enter.

---

## Burst Cooldown: 30 seconds between fills

**What**: No new position within 30 seconds of the previous fill.

**Why**: A single large spot move can generate multiple signals across BTC and ETH simultaneously. Without a cooldown, a flash crash could fill all 5 position slots in <1 second from correlated signals, all losing when the move reverses. 30 seconds is long enough for the market to partially absorb a shock before the next position is evaluated.

---

## Per-Symbol Concentration: max 2 positions per BTC/ETH

**What**: At most 2 open positions for BTC contracts and 2 for ETH contracts simultaneously.

**Why**: BTC and ETH are highly correlated (30-day correlation ≈ 0.85). Two BTC positions that both lose when BTC drops 3% is worse than one BTC and one ETH position under the same move. The per-symbol limit forces diversification across the two assets and reduces correlated-loss scenarios.

---

## Vol Floor: 0.30 annualized

**What**: If realized vol from the rolling window is below 0.30, the scanner skips the opportunity.

**Why**: In low-vol regimes, the Welford estimate may be statistically meaningless (few large returns, high relative estimation error). More importantly, at very low vol, N(d2) becomes nearly deterministic: a contract strikes far from spot will price at 0.01 or 0.99 with high confidence, and the edge from any Kalshi mispricing becomes tiny. The 0.30 floor represents approximately 0.016% per minute — consistent with typical BTC/ETH intraday vol and far below crisis levels.
