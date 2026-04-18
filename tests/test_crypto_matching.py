"""
Tests for crypto market matching and strike parsing in scanner_agent.py

Covers:
- market_matches_crypto_signal: BTC/ETH matching via ticker, title, event_ticker
- parse_strike: extraction from ticker pattern and title pattern
- _is_crypto_market: identification of crypto vs non-crypto markets
"""

from datetime import datetime, timezone

import pytest

from latency.agents.scanner_agent import (
    market_matches_crypto_signal,
    parse_strike,
    _is_crypto_market,
    _market_symbol,
)
from latency.core.models import (
    FeatureVector,
    KalshiMarket,
    Signal,
    SignalType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_market(
    title: str = "Test",
    ticker: str = "TICKER",
    event_ticker: str = "",
) -> KalshiMarket:
    return KalshiMarket(
        ticker=ticker,
        title=title,
        event_ticker=event_ticker,
        yes_bid=0.50,
        yes_ask=0.51,
        no_bid=0.49,
        no_ask=0.50,
        implied_prob=0.505,
        spread_pct=0.02,
        volume_24h=1000,
        liquidity=500,
        close_time="",
        timestamp=datetime.now(tz=timezone.utc),
    )


def _make_signal(symbol: str) -> Signal:
    fv = FeatureVector(
        symbol=symbol,
        timestamp=datetime.now(tz=timezone.utc),
        spot_price=67000.0,
        short_return=0.005,
        realized_vol=0.5,
        jump_detected=True,
        momentum_z=3.0,
    )
    return Signal(
        signal_type=SignalType.MOMENTUM_UP,
        symbol=symbol,
        timestamp=datetime.now(tz=timezone.utc),
        features=fv,
        implied_prob_shift=0.1,
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# market_matches_crypto_signal
# ---------------------------------------------------------------------------


def test_btc_signal_matches_bitcoin_title():
    market = _make_market(title="Will Bitcoin be above $67,000 at 4pm ET?")
    signal = _make_signal("BTC")
    assert market_matches_crypto_signal(market, signal) is True


def test_btc_signal_matches_btc_ticker():
    market = _make_market(ticker="KXBTC-26APR13-T67000")
    signal = _make_signal("BTC")
    assert market_matches_crypto_signal(market, signal) is True


def test_eth_signal_matches_ethereum_title():
    market = _make_market(title="Will Ethereum be above $3,500 at 4pm ET?")
    signal = _make_signal("ETH")
    assert market_matches_crypto_signal(market, signal) is True


def test_eth_signal_matches_eth_event_ticker():
    market = _make_market(event_ticker="KXETH-HOURLY-26APR13")
    signal = _make_signal("ETH")
    assert market_matches_crypto_signal(market, signal) is True


def test_btc_signal_no_match_on_unrelated():
    market = _make_market(title="Will Fed cut rates in May?")
    signal = _make_signal("BTC")
    assert market_matches_crypto_signal(market, signal) is False


def test_matching_is_case_insensitive():
    market = _make_market(title="WILL BITCOIN RISE?")
    signal = _make_signal("BTC")
    assert market_matches_crypto_signal(market, signal) is True


# ---------------------------------------------------------------------------
# parse_strike
# ---------------------------------------------------------------------------


def test_parse_strike_from_ticker():
    market = _make_market(ticker="KXBTC-26APR13-T67000")
    assert parse_strike(market) == 67000.0


def test_parse_strike_from_ticker_decimal():
    market = _make_market(ticker="KXETH-26APR13-T3500.5")
    assert parse_strike(market) == 3500.5


def test_parse_strike_from_title_with_commas():
    market = _make_market(
        title="Will Bitcoin be above $67,000 at 4pm ET?",
        ticker="SOME-TICKER",
    )
    assert parse_strike(market) == 67000.0


def test_parse_strike_from_title_no_commas():
    market = _make_market(
        title="Will Ethereum be above $3500 at 4pm ET?",
        ticker="SOME-TICKER",
    )
    assert parse_strike(market) == 3500.0


def test_parse_strike_ticker_takes_precedence():
    """If both ticker and title have a strike, ticker wins."""
    market = _make_market(
        title="Will Bitcoin be above $65,000?",
        ticker="KXBTC-26APR13-T67000",
    )
    assert parse_strike(market) == 67000.0


def test_parse_strike_returns_none_if_missing():
    market = _make_market(title="Some market without a price", ticker="NOMATCH")
    assert parse_strike(market) is None


# ---------------------------------------------------------------------------
# _is_crypto_market
# ---------------------------------------------------------------------------


def test_is_crypto_market_btc():
    market = _make_market(title="Will Bitcoin be above $67,000?")
    assert _is_crypto_market(market) is True


def test_is_crypto_market_eth_ticker():
    market = _make_market(ticker="KXETH-26APR13-T3500")
    assert _is_crypto_market(market) is True


def test_is_crypto_not_crypto():
    market = _make_market(title="Will Fed cut rates?", ticker="KXFED-25MAY")
    assert _is_crypto_market(market) is False


# ---------------------------------------------------------------------------
# _market_symbol
# ---------------------------------------------------------------------------


def test_market_symbol_btc():
    market = _make_market(title="Will Bitcoin be above $67,000?")
    assert _market_symbol(market) == "BTC"


def test_market_symbol_eth():
    market = _make_market(ticker="KXETH-26APR13-T3500")
    assert _market_symbol(market) == "ETH"


def test_market_symbol_none():
    market = _make_market(title="Will Fed cut rates?", ticker="KXFED")
    assert _market_symbol(market) is None
