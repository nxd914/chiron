"""
Tests for quant/core/features.py

Covers:
- RollingWindow: push, prune, Welford accuracy, return_since, realized_vol
- compute_features: minimum tick threshold, jump detection, momentum z-score
"""

import math
from datetime import datetime, timezone

import pytest

from latency.core.features import (
    JUMP_RETURN_THRESHOLD,
    MIN_TICKS_FOR_FEATURES,
    RollingWindow,
    compute_features,
)
from latency.core.models import Tick


# ---------------------------------------------------------------------------
# RollingWindow
# ---------------------------------------------------------------------------


def test_rolling_window_empty():
    w = RollingWindow(max_age_seconds=60.0)
    assert w.count == 0
    assert w.latest_price == 0.0
    assert w.variance == 0.0
    assert w.return_since(5.0) is None


def test_rolling_window_push_and_count():
    w = RollingWindow(max_age_seconds=60.0)
    for i in range(5):
        w.push(100.0 + i, float(i))
    assert w.count == 5
    assert w.latest_price == 104.0


def test_rolling_window_prunes_old_entries():
    w = RollingWindow(max_age_seconds=10.0)
    # Push ticks spread across 20 seconds
    for i in range(20):
        w.push(100.0, float(i))
    # Only the last ~10 seconds should remain
    assert w.count <= 11


def test_rolling_window_return_since():
    w = RollingWindow(max_age_seconds=60.0)
    # Price at t=0: 100, price at t=5: 105 (5% return)
    w.push(100.0, 0.0)
    w.push(101.0, 1.0)
    w.push(102.0, 2.0)
    w.push(103.0, 3.0)
    w.push(104.0, 4.0)
    w.push(105.0, 5.0)
    ret = w.return_since(5.0)
    assert ret is not None
    assert ret == pytest.approx(0.05, abs=0.001)


def test_rolling_window_realized_vol_positive():
    """Realized vol should be positive when prices vary."""
    w = RollingWindow(max_age_seconds=60.0)
    prices = [100, 101, 99, 102, 98, 103, 97, 104, 96, 105]
    for i, p in enumerate(prices):
        w.push(float(p), float(i))
    vol = w.realized_vol()
    assert vol > 0


def test_rolling_window_realized_vol_zero_for_flat():
    """Realized vol should be ~0 when prices are constant."""
    w = RollingWindow(max_age_seconds=60.0)
    for i in range(20):
        w.push(100.0, float(i))
    assert w.realized_vol() == pytest.approx(0.0)


def test_rolling_window_welford_accuracy():
    """Welford variance should match naive computation for same data."""
    w = RollingWindow(max_age_seconds=60.0)
    prices = [100.0, 100.5, 101.0, 100.2, 99.8, 100.3, 100.7, 99.5, 100.1, 100.6]
    for i, p in enumerate(prices):
        w.push(p, float(i))

    # Compute log returns manually
    log_rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    n = len(log_rets)
    mean = sum(log_rets) / n
    naive_var = sum((r - mean) ** 2 for r in log_rets) / (n - 1)

    assert w.variance == pytest.approx(naive_var, rel=0.01)


# ---------------------------------------------------------------------------
# compute_features
# ---------------------------------------------------------------------------


def _make_tick(symbol: str, price: float, ts_offset: float) -> Tick:
    return Tick(
        exchange="binance",
        symbol=symbol,
        price=price,
        timestamp=datetime.fromtimestamp(1700000000 + ts_offset, tz=timezone.utc),
        volume=1.0,
    )


def test_compute_features_returns_none_below_threshold():
    """Must have MIN_TICKS_FOR_FEATURES before emitting."""
    w = RollingWindow(max_age_seconds=60.0)
    for i in range(MIN_TICKS_FOR_FEATURES - 1):
        tick = _make_tick("BTC", 67000.0 + i, float(i))
        w.push(tick.price, tick.timestamp.timestamp())
        assert compute_features(w, tick) is None


def test_compute_features_emits_after_threshold():
    w = RollingWindow(max_age_seconds=60.0)
    tick = None
    for i in range(MIN_TICKS_FOR_FEATURES + 5):
        tick = _make_tick("BTC", 67000.0 + i * 10, float(i))
        w.push(tick.price, tick.timestamp.timestamp())
    fv = compute_features(w, tick)
    assert fv is not None
    assert fv.symbol == "BTC"
    assert fv.spot_price == tick.price


def test_compute_features_jump_detection():
    """A large price move should set jump_detected=True."""
    w = RollingWindow(max_age_seconds=60.0)
    base_price = 67000.0
    # Build up window with stable prices
    for i in range(MIN_TICKS_FOR_FEATURES):
        tick = _make_tick("BTC", base_price, float(i))
        w.push(tick.price, tick.timestamp.timestamp())

    # Spike the price by more than JUMP_RETURN_THRESHOLD
    jump_price = base_price * (1 + JUMP_RETURN_THRESHOLD * 3)
    jump_tick = _make_tick("BTC", jump_price, float(MIN_TICKS_FOR_FEATURES))
    w.push(jump_tick.price, jump_tick.timestamp.timestamp())
    fv = compute_features(w, jump_tick)
    assert fv is not None
    assert fv.jump_detected is True


def test_compute_features_no_jump_on_stable():
    """Stable prices should not trigger jump detection."""
    w = RollingWindow(max_age_seconds=60.0)
    for i in range(MIN_TICKS_FOR_FEATURES + 5):
        tick = _make_tick("BTC", 67000.0, float(i))
        w.push(tick.price, tick.timestamp.timestamp())
    fv = compute_features(w, tick)
    assert fv is not None
    assert fv.jump_detected is False


def test_compute_features_spot_price_matches_tick():
    """FeatureVector.spot_price should equal the latest tick price."""
    w = RollingWindow(max_age_seconds=60.0)
    for i in range(MIN_TICKS_FOR_FEATURES + 1):
        tick = _make_tick("ETH", 3500.0 + i, float(i))
        w.push(tick.price, tick.timestamp.timestamp())
    fv = compute_features(w, tick)
    assert fv is not None
    assert fv.spot_price == tick.price
