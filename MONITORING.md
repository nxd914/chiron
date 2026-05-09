# Kinzie Daemon Monitoring Runbook

Quick reference for monitoring the paper trading daemon on GCE.

## Live P&L Dashboard

```bash
GCE="gcloud compute ssh kinzie-daemon --zone=us-central1-a --project=project-41e99557-708c-4594-ba5 --"
$GCE 'sudo docker exec kinzie-daemon-1 python3 -m research.live_roi'
```

**Key metrics to watch:**
- Win rate (target >50% with current edge)
- Today P&L vs bankroll basis
- Cumulative settled P&L trend
- Per-family (BTC/ETH/SOL) breakdown

## Skip Histogram Analysis

```bash
# Real-time skip patterns (last 100 lines)
$GCE 'sudo docker logs kinzie-daemon-1 --tail=100 2>&1 | grep -E "SCAN_CYCLE|SIGNAL_SCAN"'

# Full skip history for pattern analysis
$GCE 'sudo docker logs kinzie-daemon-1 2>&1 | grep -E "SCAN_CYCLE|SIGNAL_SCAN|RISK REJECT|Order outcome"' | tail -200
```

**Expected log format:**
```
SIGNAL_SCAN skips (total=45 passed=3): low_edge=22 | kelly_zero=12 | below_breakeven=5 | low_disagreement=3
SCAN_CYCLE skips (total=120 passed=8): too_far_out=45 | low_edge=38 | bracket_no_too_expensive=18
```

## Skip Pattern → Action Matrix

| Dominant Skip | Meaning | Suggested Action |
|--------------|---------|------------------|
| `low_edge` | Model edge < 0.015 threshold | Lower `min_edge` to 0.010 OR raise `bracket_calibration` to 0.65-0.70 |
| `kelly_zero` | Model prob too close to market price | Edge fine but Kelly won't size — raise `min_edge` slightly to 0.018-0.020 |
| `below_breakeven` | Edge doesn't cover fees + slippage | Revisit spread calculation OR raise `execution_cross_offset_max` to 0.15 |
| `low_disagreement` | Drift effect < 0.003 threshold | Already aggressive at 0.003 — monitor before lowering further |
| `too_far_out` | Contract > 12h to expiry | Already widened to 12h — monitor edge on long-dated books before extending |
| `bracket_no_too_expensive` | NO ask > 70¢ | **DO NOT loosen** — price cap protects against ruin |
| `bracket_yes_too_expensive` | YES ask > 30¢ | **DO NOT loosen** — inverted risk/reward |
| `yes_too_expensive` / `no_too_expensive` | Universal price caps hit | **DO NOT loosen** — structural risk limits |
| `drift_sign_mismatch` | Drift opposes chosen side | Expected on some signals — indicates valid momentum filter |

## 24-48h Monitoring Cadence

**Hour 0-6 (Initial burn-in):**
- Run `live_roi` every 2 hours
- Check skip histograms for dominant pattern
- Do NOT adjust knobs yet — need statistical sample

**Hour 6-24 (First assessment):**
- If `low_edge` > 40% of skips → lower `min_edge` to 0.010
- If `kelly_zero` > 30% of skips → raise `min_edge` to 0.018
- If win rate < 40% with >20 fills → tighten `min_return_on_risk` to 0.10

**Hour 24-48 (Validation):**
- Confirm adjustment improved fill rate without collapsing win rate
- If below_breakeven dominates → investigate spread/slippage model
- Keep price caps fixed unless structurally necessary

## Emergency Procedures

**Win rate collapses (< 30% with >30 fills):**
```bash
# Immediate tightening (deploy via env vars)
$GCE 'sudo systemctl restart kinzie'
# With MIN_EDGE=0.020 MIN_RETURN_ON_RISK=0.12 in container env
```

**Daily loss approaches -20% circuit breaker:**
- Daemon auto-halts via `max_daily_loss_pct`
- Manual intervention: `$GCE 'sudo systemctl stop kinzie'`

## Log Tailing

```bash
# Follow logs in real-time
$GCE 'sudo docker logs -f kinzie-daemon-1 2>&1 | grep -E "Order outcome|RISK REJECT|SIGNAL_SCAN|fill"'

# Extract just order outcomes for win/loss tracking
$GCE 'sudo docker logs kinzie-daemon-1 2>&1 | grep "Order outcome"' | tail -50
```
