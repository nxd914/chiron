# Model Calibration

## What Needs Calibration

The Black-Scholes N(d2) model for threshold contracts (YES resolves if spot > K) is theoretically well-grounded and requires no empirical calibration — given accurate vol and spot inputs, N(d2) is the correct log-normal probability.

Bracket contracts require calibration. `bracket_prob()` returns:

```
P(K_floor < S_T < K_cap) = (N(d2_floor) - N(d2_cap)) × BRACKET_CALIBRATION
```

The `BRACKET_CALIBRATION` constant (currently 0.55) is the only empirically-tuned parameter in the entire pricing path.

---

## Why Brackets Need a Haircut

Two structural reasons why raw N(d2) overestimates bracket probability:

**1. CF Benchmarks averaging**: Kalshi crypto contracts settle against the CF Benchmarks Real-Time Index — a 60-second TWAP of exchange prices immediately before the settlement time, not the instantaneous spot price. The TWAP averages out price volatility within the last minute. For a contract whose bracket boundary is near current spot, the TWAP makes the outcome *less* extreme than the raw spot price would suggest. Effectively, the settlement price has lower realized variance than the spot price in the final 60 seconds. N(d2) using spot vol overestimates the tail probability.

**2. Discrete jump dynamics**: Log-normal models assume continuous diffusion. In practice, crypto spot prices can jump discretely (large trades, news events, liquidation cascades) in ways that violate the continuous assumption. For narrow brackets (1–2% wide), a jump past the bracket boundary is less likely under the discrete process than the log-normal model predicts, because jumps can skip the bracket entirely. The net effect is overestimation of bracket probability near ATM.

---

## The Incident That Motivated BRACKET_CALIBRATION = 0.55

During paper trading, one bracket contract was entered with:
- Model probability (raw N(d2)): **0.81**
- Kalshi implied probability (market): **0.51**
- Computed edge: **0.30** (30 percentage points — extremely high)

The contract resolved against us. Post-hoc analysis showed:
- The bracket was roughly ATM (spot within 1% of bracket midpoint)
- The 60-second TWAP at settlement crossed the bracket boundary by a small margin
- The market (at 0.51) was essentially pricing it as a coin flip, which was correct

The raw N(d2) model at 0.81 was wrong by ~30 percentage points for an ATM bracket. This is consistent with the structural arguments above: ATM brackets with narrow bands are the regime where log-normal assumption breaks most severely.

**Response**: Added `MIN_BRACKET_DISTANCE_PCT = 0.005` to skip brackets where spot is within 0.5% of bracket midpoint (ATM proximity guard). Reduced `BRACKET_CALIBRATION` from 0.70 to 0.55 — a 45% haircut on model probability.

---

## Statistical Status

**BRACKET_CALIBRATION = 0.55 is provisional.** It is tuned from a single data point. The correct value cannot be determined from 8 total fills.

Required for validation: 50+ bracket contract fills with logged (model_prob, settlement_outcome) pairs. Then:

```python
# Calibration error
realized_bracket_win_rate = wins / n_bracket_fills
mean_model_prob = mean([model_prob for each bracket fill])
calibration_error = realized_bracket_win_rate - mean_model_prob
# Target: |calibration_error| < 0.05
```

Until this is measured, `BRACKET_CALIBRATION = 0.55` is a reasonable conservative estimate that prevents overconfident bracket bets.

---

## Threshold Contract Calibration

Threshold contracts (YES = spot > K, NO = spot < K) use raw N(d2) with no calibration multiplier. From 5 wins in 8 fills (mix of threshold and bracket), there is no statistically meaningful evidence of systematic miscalibration on threshold contracts.

The measurement to add once n ≥ 20 threshold fills:

```
calibration_error = realized_win_rate - mean(model_prob_at_entry)
```

Expected value near zero if the model is correct. Systematic positive error = model underestimates probability (could lower min_edge). Systematic negative error = model overestimates (raise min_edge or add calibration multiplier).

---

## Inputs That Drive Calibration

**Realized volatility** (15-minute Welford window): The primary source of pricing error. If vol spikes briefly after we enter, the market will reprice the contract — our vol estimate at entry may be stale by the time of settlement.

**Spot vs CF Benchmark basis**: Our feed (Binance/Coinbase raw WS) differs from the CF Benchmarks TWAP by 0.1–0.5% on typical days, up to 1–2% during fast markets. For contracts with strikes close to spot, this basis directly creates calibration error. There is no correction for this in the current model — it is treated as irreducible noise.

---

## Next Steps

1. Add model_prob logging to `_OpenRow` (requires DB join in resolution agent) so we can compute per-fill calibration error
2. Collect 50+ bracket fills before making further adjustments to `BRACKET_CALIBRATION`
3. Collect 50+ threshold fills to confirm no calibration multiplier is needed on the threshold path
4. Consider separate calibration constants for different time horizons (1h vs 4h contracts may behave differently)
