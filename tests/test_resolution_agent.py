"""
Tests for quant/agents/resolution_agent.py

Covers:
- _check_resolution_from_raw: YES / NO / still-open detection via raw API dict
- _compute_pnl: win/lose for YES and NO sides (fee-adjusted)
- ResolutionAgent._resolve_cycle: position slot freeing via record_fill
"""

import math
import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from latency.agents.resolution_agent import (
    ResolutionAgent,
    _OpenRow,
    _check_resolution_from_raw,
    _compute_pnl,
    KALSHI_TAKER_FEE_RATE,
)
from latency.agents.risk_agent import RiskAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_market(
    *,
    ticker: str = "KXTEST",
    yes_bid: int = 50,
    yes_ask: int = 51,
    implied_prob: float = 0.505,
    close_time: str = "",
    status: str = "open",
    result: str = "",
) -> dict:
    """Build a raw Kalshi API market dict (prices in integer cents)."""
    return {
        "ticker": ticker,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "close_time": close_time,
        "status": status,
        "result": result,
    }


def _open_row(ticker: str = "KXTEST", side: str = "YES", entry: float = 0.80, size: float = 20.0) -> _OpenRow:
    return _OpenRow(order_id="o1", ticker=ticker, side=side, entry_price=entry, size_usdc=size)


def _expected_fee(entry_price: float, size_usdc: float) -> float:
    """Compute the expected Kalshi taker fee for a position."""
    num_contracts = size_usdc / entry_price
    return math.ceil(
        KALSHI_TAKER_FEE_RATE * num_contracts * entry_price * (1.0 - entry_price) * 100
    ) / 100


# ---------------------------------------------------------------------------
# _check_resolution_from_raw — API status path
# ---------------------------------------------------------------------------

def test_resolution_settled_yes():
    """status=settled + result=yes → YES."""
    raw = _raw_market(status="settled", result="yes")
    assert _check_resolution_from_raw(raw) == "YES"


def test_resolution_settled_no():
    """status=settled + result=no → NO."""
    raw = _raw_market(status="settled", result="no")
    assert _check_resolution_from_raw(raw) == "NO"


def test_resolution_settled_no_result_falls_back():
    """status=settled but no result field → falls back to price heuristic."""
    raw = _raw_market(status="settled", result="", yes_bid=99, yes_ask=100)
    assert _check_resolution_from_raw(raw) == "YES"


# ---------------------------------------------------------------------------
# _check_resolution_from_raw — price heuristic fallback
# ---------------------------------------------------------------------------

def test_resolution_yes_bid_at_ceiling():
    """yes_bid >= 0.99 → market settled YES (heuristic)."""
    raw = _raw_market(yes_bid=99, yes_ask=100)
    assert _check_resolution_from_raw(raw) == "YES"


def test_resolution_yes_ask_at_floor():
    """yes_ask <= 0.01 → market settled NO (heuristic)."""
    raw = _raw_market(yes_bid=0, yes_ask=1)
    assert _check_resolution_from_raw(raw) == "NO"


def test_resolution_still_open():
    """Mid-spread, future close_time → no resolution yet."""
    future = (datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat()
    raw = _raw_market(yes_bid=40, yes_ask=60, close_time=future)
    assert _check_resolution_from_raw(raw) is None


def test_resolution_expired_high_prob_yes():
    """Past close_time + implied >= 0.95 → YES retroactively."""
    past = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
    raw = _raw_market(yes_bid=95, yes_ask=96, close_time=past)
    assert _check_resolution_from_raw(raw) == "YES"


def test_resolution_expired_low_prob_no():
    """Past close_time + implied <= 0.05 → NO retroactively."""
    past = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
    raw = _raw_market(yes_bid=4, yes_ask=5, close_time=past)
    assert _check_resolution_from_raw(raw) == "NO"


def test_resolution_settled_market_with_extreme_prices():
    """Settled market with implied_prob=1.0 (which _parse_market would filter out)."""
    raw = _raw_market(status="settled", result="yes", yes_bid=100, yes_ask=100)
    assert _check_resolution_from_raw(raw) == "YES"


# ---------------------------------------------------------------------------
# _compute_pnl (fee-adjusted)
# ---------------------------------------------------------------------------

def test_pnl_winning_yes():
    """YES wins: payout = size / entry, profit = payout - size - fee."""
    row = _open_row(side="YES", entry=0.50, size=10.0)
    fee = _expected_fee(0.50, 10.0)
    # 10/0.50 - 10 = 10, minus fee
    assert _compute_pnl(row, "YES") == pytest.approx(10.0 - fee)


def test_pnl_losing_yes():
    """YES loses: P&L = -(size + fee)."""
    row = _open_row(side="YES", entry=0.80, size=20.0)
    fee = _expected_fee(0.80, 20.0)
    assert _compute_pnl(row, "NO") == pytest.approx(-(20.0 + fee))


def test_pnl_winning_no():
    """NO wins: payout = size / entry, profit = payout - size - fee."""
    row = _open_row(side="NO", entry=0.25, size=5.0)
    fee = _expected_fee(0.25, 5.0)
    # 5/0.25 - 5 = 15, minus fee
    assert _compute_pnl(row, "NO") == pytest.approx(15.0 - fee)


def test_pnl_losing_no():
    """NO loses: P&L = -(size + fee)."""
    row = _open_row(side="NO", entry=0.25, size=5.0)
    fee = _expected_fee(0.25, 5.0)
    assert _compute_pnl(row, "YES") == pytest.approx(-(5.0 + fee))


def test_pnl_fee_at_p50_matches_schedule():
    """At P=0.50, fee should match Kalshi schedule: $0.02/contract."""
    row = _open_row(side="YES", entry=0.50, size=25.0)
    # 50 contracts at P=0.50: fee = ceil(0.07 * 50 * 0.50 * 0.50 * 100)/100 = ceil(87.5)/100 = $0.88
    expected_fee = 0.88
    gross = (25.0 / 0.50) - 25.0  # = 25.0
    assert _compute_pnl(row, "YES") == pytest.approx(gross - expected_fee)


# ---------------------------------------------------------------------------
# ResolutionAgent._resolve_cycle — slot freeing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_cycle_calls_record_fill():
    """
    Ensure _resolve_cycle calls risk_agent.record_fill when a market resolves.
    Verifies position slot is freed with fee-adjusted P&L.
    """
    risk_mock = MagicMock()
    agent = ResolutionAgent(risk_agent=risk_mock, poll_interval=999)

    # Raw dict for settled market
    raw = _raw_market(ticker="KXWIN", status="settled", result="yes", yes_bid=99, yes_ask=100)
    row = _open_row(ticker="KXWIN", side="YES", entry=0.80, size=20.0)

    agent._load_open_rows = MagicMock(return_value=[row])
    agent._write_resolution = MagicMock()
    agent._client = MagicMock()
    agent._client.get_market_for_resolution = AsyncMock(return_value=raw)

    await agent._resolve_cycle()

    # Expected P&L: (20/0.80) - 20 = 5.0, minus fee
    fee = _expected_fee(0.80, 20.0)
    expected_pnl = 5.0 - fee
    risk_mock.record_fill.assert_called_once_with("KXWIN", pytest.approx(expected_pnl))
    agent._write_resolution.assert_called_once_with("o1", "YES", pytest.approx(expected_pnl))


@pytest.mark.asyncio
async def test_resolve_cycle_skips_open_market():
    """No record_fill if the market has not resolved yet."""
    risk_mock = MagicMock()
    agent = ResolutionAgent(risk_agent=risk_mock, poll_interval=999)

    raw = _raw_market(ticker="KXOPEN", yes_bid=48, yes_ask=52, status="open")
    row = _open_row(ticker="KXOPEN")

    agent._load_open_rows = MagicMock(return_value=[row])
    agent._write_resolution = MagicMock()
    agent._client = MagicMock()
    agent._client.get_market_for_resolution = AsyncMock(return_value=raw)

    await agent._resolve_cycle()

    risk_mock.record_fill.assert_not_called()
    agent._write_resolution.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_cycle_handles_none_from_api():
    """If API returns None, skip gracefully."""
    risk_mock = MagicMock()
    agent = ResolutionAgent(risk_agent=risk_mock, poll_interval=999)

    row = _open_row(ticker="KXGONE")
    agent._load_open_rows = MagicMock(return_value=[row])
    agent._write_resolution = MagicMock()
    agent._client = MagicMock()
    agent._client.get_market_for_resolution = AsyncMock(return_value=None)

    await agent._resolve_cycle()

    risk_mock.record_fill.assert_not_called()


# ---------------------------------------------------------------------------
# _sync_risk_positions — symbol tracking across restarts
# ---------------------------------------------------------------------------

def test_sync_rebuilds_positions_by_symbol():
    """_sync_risk_positions must rebuild _positions_by_symbol, not just _open_positions."""
    risk_agent = RiskAgent(asyncio.Queue(), asyncio.Queue(), bankroll_usdc=100_000.0)
    agent = ResolutionAgent(risk_agent=risk_agent, poll_interval=999)

    rows = [
        _open_row(ticker="KXETH-26APR1416-B1"),
        _open_row(ticker="KXETH-26APR1416-B2"),
        _open_row(ticker="KXBTC-26APR1416-T1"),
    ]
    agent._load_open_rows = MagicMock(return_value=rows)

    agent._sync_risk_positions()

    # _open_positions should have all 3 (as dict: ticker -> size_usdc)
    assert set(risk_agent._open_positions.keys()) == {"KXETH-26APR1416-B1", "KXETH-26APR1416-B2", "KXBTC-26APR1416-T1"}
    assert all(size > 0 for size in risk_agent._open_positions.values())

    # _positions_by_symbol should track ETH=2, BTC=1
    assert "ETH" in risk_agent._positions_by_symbol
    assert len(risk_agent._positions_by_symbol["ETH"]) == 2
    assert "BTC" in risk_agent._positions_by_symbol
    assert len(risk_agent._positions_by_symbol["BTC"]) == 1
