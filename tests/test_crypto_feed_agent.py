"""
Tests for quant/agents/crypto_feed_agent.py

Covers:
- Binance aggTrade message parsing
- Coinbase ticker message parsing
- Symbol normalization (BTCUSDT -> BTC, BTC-USD -> BTC)
- Malformed message handling
"""

import json
from datetime import datetime, timezone

import pytest

from latency.agents.crypto_feed_agent import CryptoFeedAgent


# ---------------------------------------------------------------------------
# Binance parsing
# ---------------------------------------------------------------------------


def _binance_agg_trade(symbol: str = "BTCUSDT", price: str = "67000.50", qty: str = "0.123") -> str:
    return json.dumps({
        "e": "aggTrade",
        "E": 1713000000000,
        "s": symbol,
        "a": 123456,
        "p": price,
        "q": qty,
        "f": 100,
        "l": 200,
        "T": 1713000000000,
        "m": False,
    })


def test_binance_parse_btc():
    agent = CryptoFeedAgent(tick_queue=None)  # type: ignore
    tick = agent._parse_binance(_binance_agg_trade("BTCUSDT", "67000.50", "0.5"))
    assert tick is not None
    assert tick.exchange == "binance"
    assert tick.symbol == "BTC"
    assert tick.price == 67000.50
    assert tick.volume == 0.5


def test_binance_parse_eth():
    agent = CryptoFeedAgent(tick_queue=None)  # type: ignore
    tick = agent._parse_binance(_binance_agg_trade("ETHUSDT", "3500.25", "1.0"))
    assert tick is not None
    assert tick.symbol == "ETH"
    assert tick.price == 3500.25


def test_binance_parse_unknown_symbol():
    agent = CryptoFeedAgent(tick_queue=None)  # type: ignore
    tick = agent._parse_binance(_binance_agg_trade("DOGEUSDT", "0.15", "100"))
    assert tick is None


def test_binance_parse_combined_stream():
    """Combined streams wrap in {"stream": "...", "data": {...}}."""
    agent = CryptoFeedAgent(tick_queue=None)  # type: ignore
    inner = json.loads(_binance_agg_trade("BTCUSDT", "67000.00", "0.1"))
    wrapped = json.dumps({"stream": "btcusdt@aggTrade", "data": inner})
    tick = agent._parse_binance(wrapped)
    assert tick is not None
    assert tick.symbol == "BTC"


def test_binance_parse_non_aggtrade_ignored():
    agent = CryptoFeedAgent(tick_queue=None)  # type: ignore
    tick = agent._parse_binance(json.dumps({"e": "kline", "s": "BTCUSDT"}))
    assert tick is None


def test_binance_parse_malformed():
    agent = CryptoFeedAgent(tick_queue=None)  # type: ignore
    assert agent._parse_binance("not json") is None
    assert agent._parse_binance("{}") is None


# ---------------------------------------------------------------------------
# Coinbase parsing
# ---------------------------------------------------------------------------


def _coinbase_ticker(product_id: str = "BTC-USD", price: str = "67000.50") -> str:
    return json.dumps({
        "type": "ticker",
        "sequence": 123,
        "product_id": product_id,
        "price": price,
        "open_24h": "66000.00",
        "volume_24h": "12345.67",
        "low_24h": "65000.00",
        "high_24h": "68000.00",
        "best_bid": "66999.00",
        "best_ask": "67001.00",
        "last_size": "0.25",
    })


def test_coinbase_parse_btc():
    agent = CryptoFeedAgent(tick_queue=None)  # type: ignore
    tick = agent._parse_coinbase(_coinbase_ticker("BTC-USD", "67000.50"))
    assert tick is not None
    assert tick.exchange == "coinbase"
    assert tick.symbol == "BTC"
    assert tick.price == 67000.50
    assert tick.volume == 0.25


def test_coinbase_parse_eth():
    agent = CryptoFeedAgent(tick_queue=None)  # type: ignore
    tick = agent._parse_coinbase(_coinbase_ticker("ETH-USD", "3500.00"))
    assert tick is not None
    assert tick.symbol == "ETH"


def test_coinbase_parse_unknown_product():
    agent = CryptoFeedAgent(tick_queue=None)  # type: ignore
    tick = agent._parse_coinbase(_coinbase_ticker("DOGE-USD", "0.15"))
    assert tick is None


def test_coinbase_parse_non_ticker_ignored():
    agent = CryptoFeedAgent(tick_queue=None)  # type: ignore
    tick = agent._parse_coinbase(json.dumps({"type": "subscriptions"}))
    assert tick is None


def test_coinbase_parse_malformed():
    agent = CryptoFeedAgent(tick_queue=None)  # type: ignore
    assert agent._parse_coinbase("not json") is None
