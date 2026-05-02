"""
Property-based tests for core/pricing.py.

These tests describe the mathematical properties the model must satisfy —
they do not change the implementation. Hypothesis generates edge cases
in realistic crypto trading ranges automatically.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from strategies.crypto.core.pricing import bracket_prob, spot_to_implied_prob

# Realistic ranges for crypto trading
PRICE_STRAT = st.floats(min_value=100.0, max_value=200_000.0, allow_nan=False, allow_infinity=False)
VOL_STRAT = st.floats(min_value=0.10, max_value=5.0, allow_nan=False, allow_infinity=False)
TIME_STRAT = st.floats(min_value=0.01, max_value=24.0, allow_nan=False, allow_infinity=False)


class TestSpotToImpliedProbProperties:

    @given(price=PRICE_STRAT, strike=PRICE_STRAT, vol=VOL_STRAT, t=TIME_STRAT)
    @settings(max_examples=500)
    def test_output_is_valid_probability(self, price, strike, vol, t):
        """N(d2) is always a valid probability in [0, 1]."""
        p = spot_to_implied_prob(price, strike, t, vol)
        assert 0.0 <= p <= 1.0, f"Invalid probability {p} for price={price}, strike={strike}, vol={vol}, t={t}"

    @given(
        price=st.floats(min_value=1000.0, max_value=200_000.0, allow_nan=False, allow_infinity=False),
        vol=VOL_STRAT,
        t=TIME_STRAT,
    )
    @settings(max_examples=300)
    def test_higher_price_means_higher_probability_vs_fixed_strike(self, price, vol, t):
        """With a fixed strike and vol, higher spot price → higher win probability."""
        strike = price * 0.95
        price_higher = price * 1.05
        p_base = spot_to_implied_prob(price, strike, t, vol)
        p_higher = spot_to_implied_prob(price_higher, strike, t, vol)
        assert p_higher >= p_base - 1e-10, (
            f"Monotonicity violated: price={price:.2f}→{price_higher:.2f} gave prob {p_base:.4f}→{p_higher:.4f}"
        )

    @given(price=PRICE_STRAT, vol=VOL_STRAT)
    @settings(max_examples=200)
    def test_deep_itm_approaches_one(self, price, vol):
        """Deep in-the-money (spot >> strike) with very little time → prob ≈ 1."""
        strike = price * 0.50
        t = 0.001
        p = spot_to_implied_prob(price, strike, t, vol)
        assert p > 0.90, f"Expected prob > 0.90 for deep ITM (price/strike=2.0), got {p:.4f}"

    @given(price=PRICE_STRAT, vol=VOL_STRAT)
    @settings(max_examples=200)
    def test_deep_otm_approaches_zero(self, price, vol):
        """Deep out-of-the-money (spot << strike) with very little time → prob ≈ 0."""
        strike = price * 2.0
        t = 0.001
        p = spot_to_implied_prob(price, strike, t, vol)
        assert p < 0.10, f"Expected prob < 0.10 for deep OTM (price/strike=0.5), got {p:.4f}"

    @given(price=PRICE_STRAT, vol=VOL_STRAT)
    @settings(max_examples=200)
    def test_atm_probability_near_half(self, price, vol):
        """At the money (spot == strike), probability should be near 0.5 (slightly below due to -0.5σ²t drift term)."""
        t = 0.5
        p = spot_to_implied_prob(price, price, t, vol)
        assert 0.30 <= p <= 0.65, f"ATM probability {p:.4f} is too far from 0.5 (vol={vol:.2f})"

    def test_zero_time_resolves_deterministically(self):
        """With zero time, the outcome is determined by current spot vs. strike."""
        assert spot_to_implied_prob(100.0, 90.0, 0.0, 0.5) == 1.0
        assert spot_to_implied_prob(80.0, 90.0, 0.0, 0.5) == 0.0

    def test_zero_vol_resolves_deterministically(self):
        """With zero vol, the outcome is determined by current spot vs. strike."""
        assert spot_to_implied_prob(100.0, 90.0, 1.0, 0.0) == 1.0
        assert spot_to_implied_prob(80.0, 90.0, 1.0, 0.0) == 0.0


class TestBracketProbProperties:

    @given(
        price=PRICE_STRAT,
        vol=VOL_STRAT,
        t=TIME_STRAT,
        pct_width=st.floats(min_value=0.01, max_value=0.30, allow_nan=False),
    )
    @settings(max_examples=400)
    def test_bracket_is_valid_probability(self, price, vol, t, pct_width):
        """bracket_prob always returns a valid probability in [0, 1]."""
        floor = price * (1.0 - pct_width)
        cap = price * (1.0 + pct_width)
        assume(cap > floor)
        p = bracket_prob(price, floor, cap, t, vol)
        assert 0.0 <= p <= 1.0, f"Invalid probability {p} for bracket [{floor:.2f}, {cap:.2f}]"

    @given(
        price=PRICE_STRAT,
        vol=VOL_STRAT,
        t=TIME_STRAT,
    )
    @settings(max_examples=300)
    def test_wider_bracket_has_higher_probability(self, price, vol, t):
        """A wider bracket should have at least as high a probability as a narrower one centered on the same price."""
        narrow_floor = price * 0.97
        narrow_cap = price * 1.03
        wide_floor = price * 0.90
        wide_cap = price * 1.10

        p_narrow = bracket_prob(price, narrow_floor, narrow_cap, t, vol)
        p_wide = bracket_prob(price, wide_floor, wide_cap, t, vol)

        assert p_wide >= p_narrow - 1e-10, (
            f"Wide bracket [{wide_floor:.0f}, {wide_cap:.0f}] prob {p_wide:.4f} "
            f"< narrow [{narrow_floor:.0f}, {narrow_cap:.0f}] prob {p_narrow:.4f}"
        )

    @given(price=PRICE_STRAT, vol=VOL_STRAT, t=TIME_STRAT)
    @settings(max_examples=200)
    def test_zero_width_bracket_has_zero_probability(self, price, vol, t):
        """A bracket with zero width (floor == cap) has zero probability."""
        p = bracket_prob(price, price, price, t, vol)
        assert p == pytest.approx(0.0, abs=1e-10)
