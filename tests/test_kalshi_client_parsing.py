
import pytest
from core.kalshi_client import _parse_market

def test_parse_market_handle_cents():
    raw = {
        "ticker": "TEST-1",
        "title": "Test 1",
        "yes_ask": 10,
        "yes_bid": 8,
        "volume_24h": 100,
        "liquidity": 50
    }
    m = _parse_market(raw)
    assert m is not None
    assert m.yes_ask == 0.10
    assert m.yes_bid == 0.08
    assert m.implied_prob == 0.09

def test_parse_market_handle_dollars():
    raw = {
        "ticker": "TEST-2",
        "title": "Test 2",
        "yes_ask_dollars": 0.15,
        "yes_bid_dollars": 0.13,
        "volume_24h": 100,
        "liquidity": 50
    }
    m = _parse_market(raw)
    assert m is not None
    assert m.yes_ask == 0.15
    assert m.yes_bid == 0.13
    assert m.implied_prob == 0.14

def test_parse_market_skip_kxmve():
    raw = {
        "ticker": "KXMVE-COMBO",
        "title": "Combo",
        "yes_ask": 10,
        "yes_bid": 8
    }
    assert _parse_market(raw) is None

def test_parse_market_handle_mixed_none():
    raw = {
        "ticker": "TEST-3",
        "title": "Test 3",
        "yes_ask": None,
        "yes_ask_dollars": 0.20,
        "yes_bid": 18,
        "volume_24h": 100
    }
    m = _parse_market(raw)
    assert m is not None
    assert m.yes_ask == 0.20
    assert m.yes_bid == 0.18
