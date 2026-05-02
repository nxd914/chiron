"""Tests for the Pipeline evaluation API."""

import asyncio

from tools.pipeline import Pipeline


def test_pipeline_no_trade_below_min_edge():
    pipeline = Pipeline(bankroll=500.0, enable_analysts=False)
    result = pipeline.evaluate_sync("Test market?", odds=0.50, model_prob=0.51)
    assert result.position_size_usdc == 0.0
    assert "NO TRADE" in result.recommendation


def test_pipeline_strong_signal():
    pipeline = Pipeline(bankroll=500.0, enable_analysts=False)
    result = pipeline.evaluate_sync("Test market?", odds=0.50, model_prob=0.70)
    assert result.position_size_usdc > 0
    assert "STRONG" in result.recommendation
    assert result.edge > 0.08


def test_pipeline_moderate_signal():
    pipeline = Pipeline(bankroll=500.0, enable_analysts=False)
    result = pipeline.evaluate_sync("Test market?", odds=0.50, model_prob=0.57)
    assert result.position_size_usdc > 0
    assert "MODERATE" in result.recommendation


def test_pipeline_respects_bankroll():
    small = Pipeline(bankroll=100.0, enable_analysts=False)
    large = Pipeline(bankroll=10000.0, enable_analysts=False)
    r_small = small.evaluate_sync("Test?", odds=0.50, model_prob=0.70)
    r_large = large.evaluate_sync("Test?", odds=0.50, model_prob=0.70)
    assert r_large.position_size_usdc > r_small.position_size_usdc


def test_pipeline_position_size_capped_by_kelly():
    pipeline = Pipeline(bankroll=1000.0, enable_analysts=False)
    result = pipeline.evaluate_sync("Test?", odds=0.10, model_prob=0.95)
    assert result.kelly_fraction <= 0.25
    assert result.position_size_usdc <= 250.0
