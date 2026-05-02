"""
Property-based tests for core/kelly.py.

Describes the mathematical constraints Kelly sizing must satisfy.
Does not change any implementation.
"""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from core.kelly import (
    MAX_KELLY_FRACTION,
    MIN_EDGE,
    capped_kelly,
    compute_kelly,
    kalshi_taker_fee_per_contract,
)

PROB_STRAT = st.floats(min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False)
PRICE_STRAT = st.floats(min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False)


class TestKalshiFeeProperties:

    @given(price=PRICE_STRAT)
    @settings(max_examples=500)
    def test_fee_is_non_negative(self, price):
        """Taker fee is always non-negative."""
        assert kalshi_taker_fee_per_contract(price) >= 0.0

    @given(price=PRICE_STRAT)
    @settings(max_examples=500)
    def test_fee_is_symmetric(self, price):
        """Fee at price P equals fee at 1-P (parabolic structure)."""
        fee_p = kalshi_taker_fee_per_contract(price)
        fee_complement = kalshi_taker_fee_per_contract(1.0 - price)
        assert abs(fee_p - fee_complement) < 1e-12

    def test_fee_is_zero_at_boundaries(self):
        """Fee is 0 at price=0 and price=1 (binary resolved)."""
        assert kalshi_taker_fee_per_contract(0.0) == 0.0
        assert kalshi_taker_fee_per_contract(1.0) == 0.0

    def test_fee_is_maximized_at_midpoint(self):
        """Fee is maximized at P=0.5 for parabolic fee structure."""
        fee_mid = kalshi_taker_fee_per_contract(0.5)
        for p in [0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9]:
            assert kalshi_taker_fee_per_contract(p) <= fee_mid + 1e-12


class TestComputeKellyProperties:

    @given(model_prob=PROB_STRAT, market_price=PRICE_STRAT)
    @settings(max_examples=500)
    def test_kelly_is_finite(self, model_prob, market_price):
        """Kelly fraction is always a finite number."""
        f = compute_kelly(model_prob, market_price)
        assert not (f != f), "Kelly returned NaN"  # NaN check
        assert abs(f) < 1e6, f"Kelly {f} is unreasonably large"

    @given(market_price=PRICE_STRAT)
    @settings(max_examples=200)
    def test_kelly_negative_when_model_equals_market(self, market_price):
        """When model probability equals market price, Kelly should be ≤ 0 (no edge after fees)."""
        f = compute_kelly(market_price, market_price)
        assert f <= 0.0, f"Expected Kelly ≤ 0 when model_prob == market_price, got {f:.4f}"

    @given(
        model_prob=st.floats(min_value=0.60, max_value=0.99, allow_nan=False),
        market_price=st.floats(min_value=0.01, max_value=0.40, allow_nan=False),
    )
    @settings(max_examples=300)
    def test_kelly_positive_when_strong_edge(self, model_prob, market_price):
        """Kelly is positive when model_prob >> market_price (clear edge)."""
        assume(model_prob - market_price > 0.15)
        f = compute_kelly(model_prob, market_price)
        assert f > 0.0, f"Expected positive Kelly for edge={model_prob - market_price:.2f}, got {f:.4f}"


class TestCappedKellyProperties:

    @given(model_prob=PROB_STRAT, market_price=PRICE_STRAT)
    @settings(max_examples=500)
    def test_capped_kelly_is_in_valid_range(self, model_prob, market_price):
        """Capped Kelly is always in [0, MAX_KELLY_FRACTION]."""
        f = capped_kelly(model_prob, market_price)
        assert 0.0 <= f <= MAX_KELLY_FRACTION + 1e-12, (
            f"capped_kelly({model_prob:.4f}, {market_price:.4f}) = {f:.6f} "
            f"out of range [0, {MAX_KELLY_FRACTION}]"
        )

    @given(market_price=PRICE_STRAT)
    @settings(max_examples=200)
    def test_no_edge_means_zero_kelly(self, market_price):
        """When model probability exactly matches market price, no position is taken."""
        f = capped_kelly(market_price, market_price)
        assert f == 0.0, f"Expected zero Kelly when model == market, got {f:.6f}"

    @given(
        model_prob=st.floats(min_value=0.80, max_value=0.99, allow_nan=False),
        market_price=st.floats(min_value=0.01, max_value=0.30, allow_nan=False),
    )
    @settings(max_examples=200)
    def test_strong_edge_is_capped_not_zero(self, model_prob, market_price):
        """With strong edge (high model prob, low market price), output should be MAX_KELLY_FRACTION."""
        assume(model_prob - market_price > 0.40)
        f = capped_kelly(model_prob, market_price)
        assert f == MAX_KELLY_FRACTION, (
            f"Strong edge case should be capped at {MAX_KELLY_FRACTION}, got {f:.6f}"
        )

    @given(model_prob=PROB_STRAT, market_price=PRICE_STRAT)
    @settings(max_examples=300)
    def test_capped_is_less_than_or_equal_to_unconstrained(self, model_prob, market_price):
        """Capped Kelly is always ≤ unconstrained Kelly (when positive)."""
        f_uncapped = compute_kelly(model_prob, market_price)
        f_capped = capped_kelly(model_prob, market_price)
        if f_uncapped > 0:
            assert f_capped <= f_uncapped + 1e-12
        else:
            assert f_capped >= 0.0
