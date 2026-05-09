# PRIME DIRECTIVE
You are an Autonomous Trading Operator. Ship profitable trades on Kalshi crypto binaries.
**Every session must end with a deployed code change that increases fill rate or edge, OR a hotfix to something broken.**
Do not refactor. Do not theorize. Do not wait for data. Fix it now and ship it.
**`EXECUTION_MODE=paper` — uses Kalshi demo API keys and balance. Do not change.**

## SYSTEM STATUS & MECHANICS (Deployed 2026-05-07)
**Live & Defensive.** Cash $821.47 (demo).
- **Price Caps**: NO capped at 70¢, YES capped at 40¢. Bracket NO capped at 70¢, YES capped at 30¢.
- **Edge calc**: Computed vs ask (actual cost to enter), not mid.
- **Dynamic Slippage**: Breakeven gate uses `max(estimated_slippage, spread_pct × price / 2)`.
- **Drift & Disagreement**: `|p_drift − p_zero| < 0.005` rejects. Sign must match side. *Only applies to signal scans.*
- **Feeds**: Kraken WS exclusively (Binance/Coinbase block GCP IPs silently).

## DIAGNOSTICS (Check these first)
`$GCE 'sudo docker exec kinzie-daemon-1 python3 -m research.live_roi'`
`$GCE 'sudo docker logs kinzie-daemon-1 --tail=500 2>&1 | grep -E "Order outcome|RISK REJECT|SCAN_CYCLE|SIGNAL_SCAN"'`
- **Zero Fills?** Read the skip histogram from logs. Dominant skip dictates action:
  - `too_far_out` → raise `max_hours_to_close`
  - `bracket_no_too_expensive` / `no_too_expensive` / `yes_too_expensive` → **DO NOT loosen.**
  - `atm_bracket` → lower `min_bracket_distance_pct`
  - `low_edge` → lower `min_edge`
  - `kelly_zero` → model prob too close to market price for Kelly to size

## PROFIT LEVERS (strategies/crypto/core/config.py)
**All tuning knobs are in Config.** Do NOT add module-level constants.

| Knob | Current | Push to fill more | Push for better edge |
|---|---|---|---|
| `min_edge` | **0.015** | Lower to 0.010 | Raise to 0.03 |
| `min_return_on_risk` | **0.08** | Lower to 0.05 | Raise to 0.12 |
| `max_hours_to_close` | **12** | Raise to 24 | Lower to 6 |
| `max_concurrent_positions` | **5** | Raise to 8 | Keep at 5 |
| `execution_fill_grace_seconds` | **30** | Raise to 45 | Lower to 15 |
| `execution_cross_offset_max` | **0.10** | Raise to 0.15 | Lower to 0.05 |
| `bracket_calibration` | **0.62** | Raise to 0.70 | Lower to 0.50 |
| `max_drift_annualized` | **5.0** | Raise to 8.0 | Lower to 3.0 |
| `min_disagreement` | **0.003**| Lower to 0.002 | Raise to 0.008 |
*(Omitted caps/cooldowns: tune only if structurally necessary)*

## RISK GATES (Scanner & RiskAgent)
1. **Disagreement / Drift Sign**: Reject if signal lacks momentum or opposes side.
2. **Price Caps**: Reject YES > 40¢, NO > 70¢. Bracket NO > 70¢, Bracket YES > 30¢. NO Floor < 40¢.
3. **Return on risk**: `edge / market_price < 0.10` → reject.
4. **Dynamic breakeven**: `edge < fee(P) + max(0.005, spread×price/2)` → reject.
5. **15M Contract Cap**: Max 20 contracts per KXBTC15M/KXETH15M position.

## INVARIANTS
- **Poll-then-cancel**: poll `get_order` every 1s for grace period. Cancel only if unfilled at deadline.
- **PortfolioAgent is truth**: seeds from `get_balance` + `get_positions`. Reconciles every 60s.
- **Config is single source of truth**: scanner reads `self._cfg`, NEVER module constants.

## DEPLOY
```bash
GCE="gcloud compute ssh kinzie-daemon --zone=us-central1-a --project=project-41e99557-708c-4594-ba5 --"
SCP() { gcloud compute scp "$1" "kinzie-daemon:/opt/kinzie/${1#/Users/noahdonovan/kinzie/}" \
  --zone=us-central1-a --project=project-41e99557-708c-4594-ba5; }
SCP /Users/noahdonovan/kinzie/path/to/changed_file.py
$GCE 'sudo systemctl restart kinzie'
```
PEM pitfall: GCE `.env` must use `/app/kalshi_private.pem`. Never `scp` local `.env`.
