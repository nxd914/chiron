"""Tests for Kelly criterion implementation."""

from core.kelly import capped_kelly, compute_kelly, position_size


def test_kelly_positive_edge():
    result = compute_kelly(model_prob=0.70, market_price=0.50)
    assert result > 0


def test_kelly_no_edge():
    # With fee-adjusted Kelly, fair value (model=market) produces slightly
    # negative Kelly since the fee makes it a losing proposition.
    result = compute_kelly(model_prob=0.50, market_price=0.50)
    assert result < 0


def test_kelly_negative_edge():
    result = compute_kelly(model_prob=0.30, market_price=0.50)
    assert result < 0


def test_capped_kelly_respects_cap():
    result = capped_kelly(model_prob=0.95, market_price=0.10)
    assert result <= 0.25


def test_capped_kelly_returns_zero_for_no_edge():
    # Fee-adjusted: fair value → negative Kelly → capped to 0.0
    result = capped_kelly(model_prob=0.50, market_price=0.50)
    # capped_kelly checks both YES and NO sides; both negative at fair value
    assert result == 0.0


def test_position_size_below_min_edge():
    result = position_size(model_prob=0.51, market_price=0.50, bankroll_usdc=1000.0)
    assert result == 0.0


def test_position_size_sufficient_edge():
    result = position_size(model_prob=0.70, market_price=0.50, bankroll_usdc=1000.0)
    assert result > 0
    assert result <= 250.0  # 0.25 * 1000


def test_kelly_boundary_market_price_zero():
    result = compute_kelly(model_prob=0.70, market_price=0.0)
    assert result == 0.0


def test_kelly_boundary_market_price_one():
    result = compute_kelly(model_prob=0.70, market_price=1.0)
    assert result == 0.0
