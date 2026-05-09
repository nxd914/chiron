"""
Canonical configuration for all tunable parameters.

Single source of truth for every threshold, limit, and constant in the
trading system. Agents import from here rather than defining magic numbers
inline. Pass a Config instance to override defaults without touching code.

All values match the empirically-calibrated production defaults.
To run with a custom config: Config(min_edge=0.06, max_concurrent_positions=3)
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # ── Kelly sizing ────────────────────────────────────────────────────────
    kelly_fraction_cap: float = 0.25
    # Conservative half-Kelly analog. Accounts for estimation error in model_prob.
    # Full Kelly requires perfect probability estimates; 0.25× is standard for
    # research-grade systems where edge is unverified at scale.

    min_edge: float = 0.015
    # Ask-based edge; 0.015 aggressive fill push (2026-05-09 deploy).

    min_kelly: float = 0.01
    # Minimum Kelly fraction. Below 1% of bankroll, transaction costs dominate.

    kalshi_taker_fee_rate: float = 0.07
    # Per-contract fee from Kalshi fee schedule (PDF).
    # Formula: fee = 0.07 × P × (1-P) per contract (parabolic, max at P=0.5).



    # ── Risk limits ─────────────────────────────────────────────────────────
    max_concurrent_positions: int = 7
    # Portfolio-level concurrency. At $5k bankroll with 10% max per position,
    # 5 positions = 50% max deployed capital. Leaves margin for adverse moves.

    max_daily_loss_pct: float = 0.20
    # Circuit breaker: halt trading if daily realized P&L drops below -20% of bankroll.
    # Proactive gate also blocks new positions if pending worst-case loss would breach this.

    max_single_exposure_pct: float = 0.10
    # Max fraction of bankroll in any single position. 10% per position.

    min_spread_pct: float = 0.04
    # Minimum full bid-ask spread (as fraction of mid). Markets tighter than 4%
    # have insufficient edge after fees — Kalshi doesn't offer maker rebates.

    min_seconds_between_fills: int = 30
    # Burst protection. Prevents a single momentum signal from filling all 5 slots
    # in rapid succession before the market reprices.

    max_positions_per_symbol: int = 2
    # Per-symbol concentration limit (BTC / ETH separately).
    # Prevents correlated loss if one asset makes a large adverse move.

    max_positions_per_expiry: int = 1
    # Max positions sharing the same expiry hour. Adjacent-range NO bets on the same
    # expiry are correlated: if price lands in either bracket, one must lose. Default
    # of 1 ensures we take only the highest-edge bracket per hour.

    max_signal_age_seconds: float = 30.0
    # Freshness gate. The scanner has a 5s burst cooldown before evaluating a
    # signal, so 2s was unreachable. 30s keeps signals current (Kalshi prices
    # reprice on the order of minutes, not seconds) while discarding truly stale
    # signals from queue backup. Periodic-scan opportunities use synthetic
    # signals timestamped at evaluation time and always pass this gate.

    min_no_fill_price: float = 0.40
    # NO-side fill floor. At NO=0.39, you risk $0.39 to win $0.61 (1.56:1).
    # Below 0.40 the risk/reward deteriorates relative to YES-side alternatives.

    max_no_fill_price: float = 0.70
    # NO-side fill cap. Lowered 0.95→0.70 (2026-05-07). At 0.70 you risk
    # $0.70 to win $0.30 (2.3:1 against). Requires ~70% win rate after fees.
    # At 0.95 (old), one loss wiped 19 wins. Portfolio showed -$334 in losses
    # from 95-99¢ NO fills.

    max_yes_fill_price: float = 0.40
    # YES-side fill cap. Only take YES bets at 40¢ or below — risk $0.40
    # to win $0.60 (1.5:1 for). Favorable risk/reward.

    min_return_on_risk: float = 0.08
    # Edge / market_price floor; 0.08 favors fill rate vs 0.10 (caps unchanged).

    ticker_reject_cooldown_seconds: int = 120
    # After Kalshi rejects a ticker, don't re-attempt for 2 minutes.
    # Prevents hammering one mispriced strike repeatedly.

    max_15m_contracts: int = 20
    # Position size cap for 15M Up/Down markets (number of contracts).
    # 179 contracts on a single 15M target lost $116. Cap at 20.

    # ── Consecutive-loss circuit breaker ────────────────────────────────────
    consecutive_loss_pause_fills: int = 3
    # If the last N consecutive fills are all losses, pause for 24 hours.
    # Distinct from the daily loss gate: catches streak-based edge decay
    # that isn't yet large enough to trigger the percentage-based halt.

    # ── Scanner ─────────────────────────────────────────────────────────────
    scan_interval_seconds: int = 120
    # Periodic re-pricing cadence. Primarily a rate-limit safety valve —
    # the signal-triggered path fires reactively (within 5s of a momentum event).

    scan_startup_delay_seconds: int = 15
    # Brief warmup before first scan so feature windows accumulate enough ticks.

    scan_concurrency: int = 12
    # Semaphore cap for parallel market evaluation.

    scan_limit: int = 200
    # Max markets per periodic scan. Reconciled 50→200 to match scanner's
    # module constant; the old 50 was stale.

    signal_scan_candidate_limit: int = 400
    # Wider market fetch for signal-triggered scans (cache refresh limit).

    min_time_to_close_minutes: int = 5
    # Skip markets with less than 5 minutes to settlement.

    max_hours_to_close: int = 12
    # Widen horizon to cut too_far_out skips; monitor edge on long-dated books.

    signal_cooldown_seconds: int = 2
    # Rate limit on signal-triggered scan. Reconciled 5→2 to match scanner.

    min_crypto_vol: float = 0.30
    # Vol floor for BTC/ETH. 30% annualized ≈ 0.016% per minute.

    max_bracket_yes_price: float = 0.30
    # Don't buy YES on bracket contracts above 30¢.

    max_bracket_no_price: float = 0.70
    # Don't buy NO on bracket contracts above 70¢. At 85¢ you risk $0.85
    # to win $0.15 — suicidal with any model error. Lowered from 0.85.

    max_bracket_near_spot_pct: float = 0.015
    # YES brackets only allowed within 1.5% of spot.

    min_bracket_distance_pct: float = 0.0025
    # Slightly tighter ATM skip → more bracket candidates.

    enable_brackets: bool = True
    # Enable bracket contract scanning.

    horizon_15m_hours: float = 0.5
    # Below this we use short-half-life drift; above, long-half-life.

    min_live_liquidity_usd: float = 2500.0
    # Skip markets too thin to absorb our orders (live mode only).

    min_disagreement: float = 0.003
    # Signal scans: min |p_drift − p_zero|; lower → more momentum passes.

    trading_start_hour_utc: int = 0
    trading_end_hour_utc: int = 24
    # Crypto is 24/7.

    idle_scan_interval_seconds: int = 600
    # 10 min between scans outside trading hours.

    # ── Feature computation ─────────────────────────────────────────────────
    short_return_window_seconds: float = 5.0
    # 5-second lookback for short return and jump detection.

    vol_window_seconds: float = 60.0
    # 60-second window for realized vol (signal detection and momentum z-score).

    vol_window_long_seconds: float = 900.0
    # 15-minute window for pricing vol. Longer lookback gives more stable vol
    # estimates for 1-4 hour contracts (avoids noise from brief vol spikes).

    min_ticks_for_features: int = 10
    # Minimum observations before emitting features. Prevents cold-start trading
    # with statistically meaningless vol estimates.

    jump_return_threshold: float = 0.0018
    # Slightly more sensitive jump detect → more signal-triggered scans.

    # ── Drift / EWMA ────────────────────────────────────────────────────────
    ewma_short_half_life_s: float = 15.0
    # 15s half-life for ≤15min horizons. Lowered 30→15s to catch fast moves.

    ewma_long_half_life_s: float = 300.0
    # 5min half-life for ≥1h contracts.

    max_drift_annualized: float = 7.0
    # Cap |drift| fed into pricing; higher lets strong trends express in model_prob.

    # ── Pricing ─────────────────────────────────────────────────────────────
    bracket_calibration: float = 0.62
    # Applied in scanner to raw bracket_prob (both p_drift and p_zero). Higher
    # = less haircut vs 0.55 → more bracket YES edge when model runs hot.

    min_time_to_expiry_hours: float = 1.0 / 60.0
    # 1-minute floor on time-to-expiry in BS formula. Prevents d2 singularity as
    # t→0. Scanner guards at 5min, but this floor catches the race condition between
    # scan and execution timing.

    # ── Signal detection ────────────────────────────────────────────────────
    momentum_z_threshold: float = 1.75
    # Z-score to fire momentum (FeatureAgent + features_to_signal).

    min_confidence: float = 0.52
    # Minimum confidence to emit a signal downstream.

    # ── Performance metrics ─────────────────────────────────────────────────
    assumed_fills_per_day: int = 4
    # Conservative baseline for Sharpe annualization in _running_sharpe().
    # One fill every ~6 hours during active trading hours. Update from live
    # data once fill cadence stabilizes — the Sharpe estimate is sensitive to
    # this assumption at low sample counts.

    # ── Execution ───────────────────────────────────────────────────────────
    execution_fill_grace_seconds: float = 40.0
    # Kalshi V2 limit orders fill asynchronously. After POST we poll
    # get_order(order_id) up to this many seconds; cancel if still unfilled.
    # Required: a synchronous fill_count==0 check would cancel every order
    # before it has a chance to match. 30s gives thin demo books a chance
    # for crossing flow to land — 8s was too short on Up/Down 15m markets.

    execution_fill_poll_interval_seconds: float = 1.0

    execution_cross_offset_max: float = 0.12
    execution_cross_offset_min: float = 0.01
    # Cross-the-spread bounds applied in ExecutionAgent. The limit price is
    # base_ask + clamp(edge*0.5, min, max). Raising max from 5¢→10¢ unblocks
    # thin 15m Up/Down books where 5¢ above the cached ask still didn't cross.

    min_fill_register_usd: float = 1.0
    # Minimum WS-fill cost (USD) to register as a real position with RiskAgent.
    # Sub-dollar partial fills routinely never settle as a real Kalshi position
    # (Kalshi's matching engine occasionally reports 1-2¢ partial-fills that
    # are immediately reversed). Registering them locks the per-expiry slot for
    # up to one reconcile cycle, blocking dozens of real trade attempts.
    # PortfolioAgent's reconcile loop still picks up real positions from
    # get_positions() within ~60s, so the worst-case effect of this filter is
    # a brief delay in registering a genuine micro-fill.

    # ── Live mode gate ──────────────────────────────────────────────────────
    min_fills_for_live: int = 100
    # Minimum resolved paper fills before live mode is permitted.

    min_sharpe_for_live: float = 1.0
    # Minimum rolling Sharpe ratio (over all fills, n >= min_fills_for_live)
    # before live mode is permitted.

    @classmethod
    def from_env(cls) -> Config:
        """
        Construct Config with environment variable overrides.
        All env vars are optional — missing vars use dataclass defaults.

        Example:
            KELLY_FRACTION_CAP=0.15 MIN_EDGE=0.05 python3 daemon.py
        """
        def _float(key: str, default: float) -> float:
            v = os.environ.get(key)
            return float(v) if v is not None else default

        def _int(key: str, default: int) -> int:
            v = os.environ.get(key)
            return int(v) if v is not None else default

        base = cls()
        return cls(
            kelly_fraction_cap=_float("KELLY_FRACTION_CAP", base.kelly_fraction_cap),
            min_edge=_float("MIN_EDGE", base.min_edge),
            min_kelly=_float("MIN_KELLY", base.min_kelly),
            kalshi_taker_fee_rate=_float("KALSHI_TAKER_FEE_RATE", base.kalshi_taker_fee_rate),
            # estimated_slippage removed
            max_concurrent_positions=_int("MAX_CONCURRENT_POSITIONS", base.max_concurrent_positions),
            max_daily_loss_pct=_float("MAX_DAILY_LOSS_PCT", base.max_daily_loss_pct),
            max_single_exposure_pct=_float("MAX_SINGLE_EXPOSURE_PCT", base.max_single_exposure_pct),
            min_spread_pct=_float("MIN_SPREAD_PCT", base.min_spread_pct),
            min_seconds_between_fills=_int("MIN_SECONDS_BETWEEN_FILLS", base.min_seconds_between_fills),
            max_positions_per_symbol=_int("MAX_POSITIONS_PER_SYMBOL", base.max_positions_per_symbol),
            max_positions_per_expiry=_int("MAX_POSITIONS_PER_EXPIRY", base.max_positions_per_expiry),
            max_signal_age_seconds=_float("MAX_SIGNAL_AGE_SECONDS", base.max_signal_age_seconds),
            min_no_fill_price=_float("MIN_NO_FILL_PRICE", base.min_no_fill_price),
            max_no_fill_price=_float("MAX_NO_FILL_PRICE", base.max_no_fill_price),
            max_yes_fill_price=_float("MAX_YES_FILL_PRICE", base.max_yes_fill_price),
            min_return_on_risk=_float("MIN_RETURN_ON_RISK", base.min_return_on_risk),
            ticker_reject_cooldown_seconds=_int("TICKER_REJECT_COOLDOWN_SECONDS", base.ticker_reject_cooldown_seconds),
            max_15m_contracts=_int("MAX_15M_CONTRACTS", base.max_15m_contracts),
            consecutive_loss_pause_fills=_int("CONSECUTIVE_LOSS_PAUSE_FILLS", base.consecutive_loss_pause_fills),
            scan_interval_seconds=_int("SCAN_INTERVAL_SECONDS", base.scan_interval_seconds),
            scan_startup_delay_seconds=_int("SCAN_STARTUP_DELAY_SECONDS", base.scan_startup_delay_seconds),
            scan_concurrency=_int("SCAN_CONCURRENCY", base.scan_concurrency),
            scan_limit=_int("SCAN_LIMIT", base.scan_limit),
            signal_scan_candidate_limit=_int(
                "SIGNAL_SCAN_CANDIDATE_LIMIT",
                _int("SIGNAL_CANDIDATE_LIMIT", base.signal_scan_candidate_limit),
            ),
            min_time_to_close_minutes=_int("MIN_TIME_TO_CLOSE_MINUTES", base.min_time_to_close_minutes),
            max_hours_to_close=_int("MAX_HOURS_TO_CLOSE", base.max_hours_to_close),
            signal_cooldown_seconds=_int("SIGNAL_COOLDOWN_SECONDS", base.signal_cooldown_seconds),
            min_crypto_vol=_float("MIN_CRYPTO_VOL", base.min_crypto_vol),
            max_bracket_yes_price=_float("MAX_BRACKET_YES_PRICE", base.max_bracket_yes_price),
            max_bracket_no_price=_float("MAX_BRACKET_NO_PRICE", base.max_bracket_no_price),
            max_bracket_near_spot_pct=_float("MAX_BRACKET_NEAR_SPOT_PCT", base.max_bracket_near_spot_pct),
            min_bracket_distance_pct=_float("MIN_BRACKET_DISTANCE_PCT", base.min_bracket_distance_pct),
            horizon_15m_hours=_float("HORIZON_15M_HOURS", base.horizon_15m_hours),
            min_live_liquidity_usd=_float("MIN_LIVE_LIQUIDITY_USD", base.min_live_liquidity_usd),
            min_disagreement=_float("MIN_DISAGREEMENT", base.min_disagreement),
            trading_start_hour_utc=_int("TRADING_START_HOUR_UTC", base.trading_start_hour_utc),
            trading_end_hour_utc=_int("TRADING_END_HOUR_UTC", base.trading_end_hour_utc),
            enable_brackets=(
                base.enable_brackets
                if os.environ.get("ENABLE_BRACKETS") is None
                else os.environ["ENABLE_BRACKETS"].lower() in ("1", "true", "yes")
            ),
            idle_scan_interval_seconds=_int("IDLE_SCAN_INTERVAL_SECONDS", base.idle_scan_interval_seconds),
            ewma_short_half_life_s=_float("EWMA_SHORT_HALF_LIFE_S", base.ewma_short_half_life_s),
            ewma_long_half_life_s=_float("EWMA_LONG_HALF_LIFE_S", base.ewma_long_half_life_s),
            max_drift_annualized=_float("MAX_DRIFT_ANNUALIZED", base.max_drift_annualized),
            bracket_calibration=_float("BRACKET_CALIBRATION", base.bracket_calibration),
            min_time_to_expiry_hours=_float("MIN_TIME_TO_EXPIRY_HOURS", base.min_time_to_expiry_hours),
            momentum_z_threshold=_float("MOMENTUM_Z_THRESHOLD", base.momentum_z_threshold),
            min_confidence=_float("MIN_CONFIDENCE", base.min_confidence),
            jump_return_threshold=_float("JUMP_RETURN_THRESHOLD", base.jump_return_threshold),
            assumed_fills_per_day=_int("ASSUMED_FILLS_PER_DAY", base.assumed_fills_per_day),
            min_fills_for_live=_int("MIN_FILLS_FOR_LIVE", base.min_fills_for_live),
            min_sharpe_for_live=_float("MIN_SHARPE_FOR_LIVE", base.min_sharpe_for_live),
            execution_fill_grace_seconds=_float(
                "EXECUTION_FILL_GRACE_SECONDS", base.execution_fill_grace_seconds,
            ),
            execution_fill_poll_interval_seconds=_float(
                "EXECUTION_FILL_POLL_INTERVAL_SECONDS",
                base.execution_fill_poll_interval_seconds,
            ),
            execution_cross_offset_max=_float(
                "EXECUTION_CROSS_OFFSET_MAX", base.execution_cross_offset_max,
            ),
            execution_cross_offset_min=_float(
                "EXECUTION_CROSS_OFFSET_MIN", base.execution_cross_offset_min,
            ),
            min_fill_register_usd=_float(
                "MIN_FILL_REGISTER_USD", base.min_fill_register_usd,
            ),
        )

    def validate(self) -> None:
        """Assert parameter invariants. Call at startup."""
        assert 0 < self.kelly_fraction_cap <= 1.0, "Kelly cap must be in (0, 1]"
        assert 0 < self.min_edge < 0.50, "Min edge must be in (0, 0.5)"
        assert 0 < self.max_daily_loss_pct <= 1.0, "Daily loss pct must be in (0, 1]"
        assert 0 < self.max_single_exposure_pct <= 1.0, "Exposure pct must be in (0, 1]"
        assert self.max_concurrent_positions >= 1
        assert self.min_no_fill_price < self.max_no_fill_price
        assert 0 < self.max_no_fill_price <= 0.85, "NO fill cap too high — suicidal risk/reward"
        assert 0 < self.max_yes_fill_price <= 0.50, "YES fill cap too high"
        assert self.min_return_on_risk > 0
        assert self.max_hours_to_close >= 1
        assert self.max_15m_contracts >= 1
        assert self.min_fills_for_live >= 1
        assert self.min_sharpe_for_live > 0


# Default singleton — agents use this unless a custom Config is injected.
DEFAULT_CONFIG = Config()
