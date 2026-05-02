"""
Rolling window feature computation for real-time crypto tick streams.

Uses Welford's online algorithm for O(1) amortized variance updates.
Bounded deque with time-based expiry keeps memory constant regardless
of tick rate.

This module is a pure computation engine — no I/O, no async.
Designed to be swappable with a Rust/PyO3 extension if profiling
shows CPU pressure at high tick rates.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from .models import FeatureVector, Tick

# Feature computation parameters
SHORT_RETURN_WINDOW_SECONDS = 5.0    # lookback for short return
VOL_WINDOW_SECONDS = 60.0            # lookback for realized vol (signal detection)
VOL_WINDOW_LONG_SECONDS = 900.0      # 15-minute lookback for pricing vol (more stable for 1-4h contracts)
MIN_TICKS_FOR_FEATURES = 10          # minimum observations before emitting
JUMP_RETURN_THRESHOLD = 0.002        # 0.2% return in window -> jump
ANNUALIZATION_FACTOR = math.sqrt(365 * 24 * 3600)  # per-second vol -> annual


class RollingWindow:
    """
    Time-bounded rolling window with O(1) amortized online statistics.

    Maintains a deque of (timestamp_seconds, price) tuples. Entries older
    than max_age_seconds are pruned on each push. Running mean and variance
    of log-returns are tracked via Welford's algorithm with an adjustment
    for pruned entries.

    Because Welford's algorithm doesn't support efficient removal of old
    entries, we use a hybrid approach: Welford for the fast path (push),
    and periodic full recomputation when the prune ratio exceeds a threshold.
    For typical crypto tick rates (<500/sec) this is effectively O(1) amortized.
    """

    def __init__(self, max_age_seconds: float = VOL_WINDOW_SECONDS) -> None:
        self._max_age = max_age_seconds
        self._ticks: deque[tuple[float, float]] = deque()  # (timestamp_s, price)
        # Welford running state for log returns
        self._n: int = 0
        self._mean: float = 0.0
        self._m2: float = 0.0
        self._dirty: bool = False  # set when prune invalidates Welford state

    def push(self, price: float, timestamp: float) -> None:
        """Ingest a new price observation. Prunes stale entries."""
        old_len = len(self._ticks)

        # Compute log return before appending (need previous price)
        if self._ticks and price > 0 and self._ticks[-1][1] > 0:
            log_ret = math.log(price / self._ticks[-1][1])
            self._welford_push(log_ret)

        self._ticks.append((timestamp, price))
        self._prune(timestamp)

        if len(self._ticks) < old_len:
            self._dirty = True

    @property
    def count(self) -> int:
        return len(self._ticks)

    @property
    def latest_price(self) -> float:
        return self._ticks[-1][1] if self._ticks else 0.0

    @property
    def variance(self) -> float:
        """Running variance of log returns. Recomputes if dirty."""
        if self._dirty:
            self._recompute_welford()
        if self._n < 2:
            return 0.0
        return self._m2 / (self._n - 1)

    @property
    def std(self) -> float:
        v = self.variance
        return math.sqrt(v) if v > 0 else 0.0

    @property
    def mean_return(self) -> float:
        if self._dirty:
            self._recompute_welford()
        return self._mean if self._n > 0 else 0.0

    def return_since(self, seconds_ago: float) -> Optional[float]:
        """Compute return from ~seconds_ago to now. None if insufficient data."""
        if len(self._ticks) < 2:
            return None
        now_ts = self._ticks[-1][0]
        current_price = self._ticks[-1][1]
        target_ts = now_ts - seconds_ago

        # Walk backward to find the closest tick at or before target_ts
        for ts, price in self._ticks:
            if ts <= target_ts and price > 0:
                return (current_price - price) / price

        # If all ticks are newer than target, use the oldest
        oldest_price = self._ticks[0][1]
        if oldest_price > 0:
            return (current_price - oldest_price) / oldest_price
        return None

    def realized_vol(self) -> float:
        """Annualized realized volatility from log-return variance."""
        return self.std * ANNUALIZATION_FACTOR

    # ---------------------------------------------------------------
    # Welford internals
    # ---------------------------------------------------------------

    def _welford_push(self, value: float) -> None:
        self._n += 1
        delta = value - self._mean
        self._mean += delta / self._n
        delta2 = value - self._mean
        self._m2 += delta * delta2

    def _recompute_welford(self) -> None:
        """Full recomputation from the deque. Called when prune invalidates state."""
        self._n = 0
        self._mean = 0.0
        self._m2 = 0.0
        prices = [p for _, p in self._ticks]
        for i in range(1, len(prices)):
            if prices[i] > 0 and prices[i - 1] > 0:
                log_ret = math.log(prices[i] / prices[i - 1])
                self._welford_push(log_ret)
        self._dirty = False

    def _prune(self, now_ts: float) -> None:
        cutoff = now_ts - self._max_age
        while self._ticks and self._ticks[0][0] < cutoff:
            self._ticks.popleft()


def compute_features(
    window: RollingWindow,
    tick: Tick,
    short_window_seconds: float = SHORT_RETURN_WINDOW_SECONDS,
    long_window: Optional[RollingWindow] = None,
) -> Optional[FeatureVector]:
    """
    Compute a FeatureVector from rolling window state after ingesting a tick.

    Returns None if the window has fewer than MIN_TICKS_FOR_FEATURES observations.
    This is a pure function of the window state — no side effects.

    Args:
        window: 60-second rolling window (for signal detection)
        tick: latest price tick
        short_window_seconds: lookback for short return
        long_window: optional 15-minute rolling window (for pricing vol)
    """
    if window.count < MIN_TICKS_FOR_FEATURES:
        return None

    short_return = window.return_since(short_window_seconds)
    if short_return is None:
        return None

    realized_vol = window.realized_vol()
    jump_detected = abs(short_return) >= JUMP_RETURN_THRESHOLD

    # Momentum z-score: how many stds is this return from the rolling mean
    std = window.std
    if std > 0:
        momentum_z = (short_return - window.mean_return) / std
    else:
        momentum_z = 0.0

    # Long vol: use 15-minute window if available and warmed up, else fall back to short
    realized_vol_long = 0.0
    if long_window is not None and long_window.count >= MIN_TICKS_FOR_FEATURES:
        realized_vol_long = long_window.realized_vol()
    if realized_vol_long <= 0.0:
        realized_vol_long = realized_vol

    return FeatureVector(
        symbol=tick.symbol,
        timestamp=tick.timestamp,
        spot_price=tick.price,
        short_return=short_return,
        realized_vol=realized_vol,
        realized_vol_long=realized_vol_long,
        jump_detected=jump_detected,
        momentum_z=momentum_z,
    )
