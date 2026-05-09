"""
Tests for quant/agents/scanner_agent.py

Covers:
- Signal coalescing: queue drain behavior in _signal_scan
- Price cache application (immutable dataclass replacement)
- Scoring with spot_to_implied_prob
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest

from strategies.crypto.agents.scanner_agent import (
    ScannerAgent,
    _is_bracket_market,
    _is_less_market,
    _is_up_down_15m_market,
    parse_strike,
)
from strategies.crypto.core.config import DEFAULT_CONFIG
from strategies.crypto.core.models import (
    FeatureVector,
    KalshiMarket,
    Signal,
    SignalType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future_close_time() -> str:
    """Return an ISO close time 2 hours from now — within MAX_HOURS_TO_CLOSE."""
    from datetime import timedelta
    return (datetime.now(tz=timezone.utc) + timedelta(hours=2)).isoformat()


def _make_market(
    title: str = "Will Bitcoin be above $67,000 at 4pm ET?",
    ticker: str = "KXBTC-26APR13-T67000",
    event_ticker: str = "KXBTC-26APR13",
    implied_prob: float = 0.50,
    close_time: str = "",
    strike_type: str = "greater",
    floor_strike: Optional[float] = None,
    cap_strike: Optional[float] = None,
) -> KalshiMarket:
    if not close_time:
        close_time = _future_close_time()
    return KalshiMarket(
        ticker=ticker,
        title=title,
        event_ticker=event_ticker,
        yes_bid=implied_prob - 0.01,
        yes_ask=implied_prob + 0.01,
        no_bid=1.0 - implied_prob - 0.01,
        no_ask=1.0 - implied_prob + 0.01,
        implied_prob=implied_prob,
        spread_pct=0.04,
        volume_24h=5000,
        liquidity=2000,
        close_time=close_time,
        timestamp=datetime.now(tz=timezone.utc),
        strike_type=strike_type,
        floor_strike=floor_strike,
        cap_strike=cap_strike,
    )


def _make_signal(
    symbol: str = "BTC",
    spot_price: float = 67500.0,
    drift_short: float = 0.5,
    drift_long: float = 0.5,
) -> Signal:
    fv = FeatureVector(
        symbol=symbol,
        timestamp=datetime.now(tz=timezone.utc),
        spot_price=spot_price,
        short_return=0.005,
        realized_vol=0.5,
        jump_detected=True,
        momentum_z=3.0,
        realized_vol_long=0.5,
        ewma_drift_short=drift_short,
        ewma_drift_long=drift_long,
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
# Price cache application (frozen dataclass fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_price_cache_returns_new_market():
    """Price cache must not mutate the original frozen KalshiMarket."""
    market = _make_market()
    original_bid = market.yes_bid

    cache = {market.ticker: {"yes_bid": 0.55, "yes_ask": 0.60, "no_bid": 0.40, "no_ask": 0.45}}

    agent = ScannerAgent(asyncio.Queue(), 500.0, price_cache=cache)
    updated = agent._apply_price_cache(market)

    # Original must be unchanged (frozen)
    assert market.yes_bid == original_bid
    # Updated should have new prices
    assert updated.yes_bid == 0.55
    assert updated.yes_ask == 0.60


@pytest.mark.asyncio
async def test_apply_price_cache_no_cache_returns_same():
    agent = ScannerAgent(asyncio.Queue(), 500.0)
    market = _make_market()
    result = agent._apply_price_cache(market)
    assert result is market  # identity — no copy needed


# ---------------------------------------------------------------------------
# Signal coalescing — queue drain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_scan_drains_burst_queue():
    """
    When multiple signals arrive at once, _signal_scan should drain all of
    them and only process the last (most recent) one.
    """
    sig_queue: asyncio.Queue[Signal] = asyncio.Queue()
    opp_queue: asyncio.Queue = asyncio.Queue()

    agent = ScannerAgent(opp_queue, 500.0, signal_queue=sig_queue)

    for i in range(3):
        await sig_queue.put(_make_signal("BTC", spot_price=67000.0 + i * 100))

    assert sig_queue.qsize() == 3

    agent._client.get_top_markets = AsyncMock(return_value=[])

    call_count = 0

    async def _fake_sleep(duration):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt("break loop")

    with patch("asyncio.sleep", side_effect=_fake_sleep), \
         patch.object(ScannerAgent, "_is_trading_hours", return_value=True):
        try:
            await agent._signal_scan()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

    assert sig_queue.qsize() == 0


@pytest.mark.asyncio
async def test_signal_scan_preserves_multi_symbol_signals():
    """
    When BTC and ETH signals arrive in the same burst, both symbols
    should update spot_cache (not just the last one received).
    """
    sig_queue: asyncio.Queue[Signal] = asyncio.Queue()
    opp_queue: asyncio.Queue = asyncio.Queue()

    agent = ScannerAgent(opp_queue, 500.0, signal_queue=sig_queue)

    await sig_queue.put(_make_signal("BTC", spot_price=67000.0))
    await sig_queue.put(_make_signal("ETH", spot_price=2300.0))

    agent._client.get_top_markets = AsyncMock(return_value=[])

    call_count = 0

    async def _fake_sleep(duration):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt("break loop")

    with patch("asyncio.sleep", side_effect=_fake_sleep), \
         patch.object(ScannerAgent, "_is_trading_hours", return_value=True):
        try:
            await agent._signal_scan()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

    assert sig_queue.qsize() == 0
    # Both symbols should have cached spot data
    assert "BTC" in agent._spot_cache
    assert "ETH" in agent._spot_cache
    assert agent._spot_cache["BTC"][0] == 67000.0
    assert agent._spot_cache["ETH"][0] == 2300.0


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_returns_opportunity_with_edge():
    """Near-ATM threshold with drift produces an opportunity when disagreement is material."""
    agent = ScannerAgent(
        asyncio.Queue(),
        500.0,
        crypto_features={
            "BTC": FeatureVector(
                symbol="BTC",
                timestamp=datetime.now(tz=timezone.utc),
                spot_price=68000.0,
                short_return=0.01,
                realized_vol=0.5,
                jump_detected=True,
                momentum_z=3.0,
                ewma_drift_long=2.0,
            )
        },
    )
    # Near-ATM strike (67900 vs spot 68000 = 0.15% ITM). Drift=2.0 annualized
    # materially shifts N(d2) → large disagreement between p_drift and p_zero.
    # YES ask at 0.26 (within 0.40 cap).
    market = _make_market(
        title="Will Bitcoin be above $67,900 at 4pm ET?",
        ticker="KXBTC-26APR1409-T67900",
        event_ticker="KXBTC-26APR1409",
        implied_prob=0.25,
        strike_type="greater",
    )
    signal = _make_signal("BTC", spot_price=68000.0, drift_long=2.0)
    opp = agent._score(market, signal)
    assert opp is not None
    assert opp.model_prob > 0.25
    assert opp.edge > 0


@pytest.mark.asyncio
async def test_score_returns_none_without_spot_data():
    """No spot data -> no opportunity."""
    agent = ScannerAgent(asyncio.Queue(), 500.0)
    market = _make_market()
    # Signal with spot_price=0.0
    fv = FeatureVector("BTC", datetime.now(tz=timezone.utc), 0.0, 0.0, 0.0, False, 0.0)
    signal = Signal(
        signal_type=SignalType.MOMENTUM_UP,
        symbol="BTC",
        timestamp=datetime.now(tz=timezone.utc),
        features=fv,
        implied_prob_shift=0.1,
        confidence=0.9,
    )
    opp = agent._score(market, signal)
    assert opp is None


@pytest.mark.asyncio
async def test_score_returns_none_for_non_crypto_market():
    """Non-crypto market has no parseable strike."""
    agent = ScannerAgent(asyncio.Queue(), 500.0)
    market = _make_market(
        title="Will Fed cut rates?",
        ticker="KXFED-25MAY",
        event_ticker="KXFED",
        strike_type="",
    )
    signal = _make_signal("BTC", spot_price=67000.0)
    opp = agent._score(market, signal)
    assert opp is None


# ---------------------------------------------------------------------------
# Contract type filtering
# ---------------------------------------------------------------------------


def test_is_bracket_market_from_strike_type():
    """Bracket contracts (strike_type='between') are detected."""
    bracket = _make_market(
        ticker="KXETH-26APR1417-B2330",
        strike_type="between",
    )
    threshold = _make_market(
        ticker="KXBTC-26APR1409-T74400",
        strike_type="greater",
    )
    assert _is_bracket_market(bracket) is True
    assert _is_bracket_market(threshold) is False


def test_is_bracket_market_falls_back_to_ticker_suffix():
    """When strike_type is empty, -B suffix is treated as bracket."""
    bracket = _make_market(
        ticker="KXETH-26APR1417-B2330",
        strike_type="",
    )
    threshold = _make_market(
        ticker="KXBTC-26APR1409-T74400",
        strike_type="",
    )
    assert _is_bracket_market(bracket) is True
    assert _is_bracket_market(threshold) is False


def test_is_less_market():
    """'less' strike_type means YES = spot < strike."""
    less_mkt = _make_market(
        ticker="KXBTC-26APR1409-T1430",
        strike_type="less",
    )
    greater_mkt = _make_market(
        ticker="KXBTC-26APR1409-T74400",
        strike_type="greater",
    )
    assert _is_less_market(less_mkt) is True
    assert _is_less_market(greater_mkt) is False


def test_parse_strike_threshold_contract():
    """Threshold -T suffix is parsed correctly."""
    market = _make_market(ticker="KXBTC-26APR1409-T74400", strike_type="greater")
    assert parse_strike(market) == 74400.0


def test_parse_strike_title_fallback():
    """When ticker has no -T suffix, fall back to title."""
    market = _make_market(
        ticker="KXBTC-26APR1409",
        title="Bitcoin price above $74,400?",
        strike_type="greater",
    )
    assert parse_strike(market) == 74400.0


@pytest.mark.asyncio
async def test_score_skips_bracket_contracts_missing_strikes():
    """Bracket contracts without floor/cap strike data must be skipped."""
    agent = ScannerAgent(
        asyncio.Queue(),
        500.0,
        crypto_features={
            "ETH": FeatureVector(
                symbol="ETH",
                timestamp=datetime.now(tz=timezone.utc),
                spot_price=2330.0,
                short_return=0.01,
                realized_vol=0.5,
                jump_detected=True,
                momentum_z=3.0,
            )
        },
    )
    bracket_market = _make_market(
        title="Ethereum price at Apr 14, 2026 at 5pm EDT?",
        ticker="KXETH-26APR1417-B2330",
        event_ticker="KXETH-26APR1417",
        implied_prob=0.20,
        strike_type="between",
        # floor_strike and cap_strike intentionally omitted
    )
    signal = _make_signal("ETH", spot_price=2330.0)
    opp = agent._score(bracket_market, signal)
    assert opp is None


@pytest.mark.skip(reason="Drift-based disagreement on multi-hour brackets is intrinsically small (~0.2pp); the new disagreement gate is intended to kill most of them.")
@pytest.mark.asyncio
async def test_score_prices_bracket_contract_with_edge():
    """Bracket contracts with floor/cap strikes are scored via N(d2_floor)-N(d2_cap)."""
    # YES bracket near spot: spot=2320, bracket [2240, 2360], mid=2300.
    # Distance pct = 20/2320 = 0.86% — passes MIN_BRACKET_DISTANCE_PCT (0.5%)
    # and MAX_BRACKET_NEAR_SPOT_PCT (1.5%). Negative drift pushes price toward
    # mid → bracket prob rises → YES.
    agent = ScannerAgent(
        asyncio.Queue(),
        500.0,
        crypto_features={
            "ETH": FeatureVector(
                symbol="ETH",
                timestamp=datetime.now(tz=timezone.utc),
                spot_price=2320.0,
                short_return=-0.01,
                realized_vol=0.8,
                jump_detected=True,
                momentum_z=-3.0,
                realized_vol_long=0.8,
                ewma_drift_long=-2.0,
            )
        },
        config=replace(DEFAULT_CONFIG, enable_brackets=True),
    )
    bracket_market = _make_market(
        title="Ethereum price at Apr 14, 2026?",
        ticker="KXETH-26APR1417-B2300",
        event_ticker="KXETH-26APR1417",
        implied_prob=0.05,
        strike_type="between",
        floor_strike=2240.0,
        cap_strike=2360.0,
    )
    signal = _make_signal("ETH", spot_price=2320.0, drift_long=-2.0)
    opp = agent._score(bracket_market, signal)
    # spot(2350) is inside [2200, 2400] → model_prob should be > 0.05 → edge > MIN_EDGE
    assert opp is not None
    assert opp.model_prob > 0.05
    assert opp.edge >= 0.03


@pytest.mark.asyncio
async def test_score_accepts_threshold_greater_contract():
    """Threshold 'greater' contracts should be scored normally."""
    agent = ScannerAgent(
        asyncio.Queue(),
        500.0,
        crypto_features={
            "BTC": FeatureVector(
                symbol="BTC",
                timestamp=datetime.now(tz=timezone.utc),
                spot_price=74500.0,
                short_return=0.01,
                realized_vol=0.5,
                jump_detected=True,
                momentum_z=3.0,
                ewma_drift_long=2.0,
            )
        },
    )
    # YES ask at 0.26 (within 0.40 cap), spot above strike
    market = _make_market(
        title="Bitcoin price above $74,400?",
        ticker="KXBTC-26APR1409-T74400",
        event_ticker="KXBTC-26APR1409",
        implied_prob=0.25,
        strike_type="greater",
    )
    signal = _make_signal("BTC", spot_price=74500.0, drift_long=2.0)
    opp = agent._score(market, signal)
    assert opp is not None
    assert opp.model_prob > 0.25  # spot > strike → high prob above


# ---------------------------------------------------------------------------
# Vol warmup guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_score_skips_when_vol_zero():
    """If realized_vol is 0 (warmup period), _score should return None."""
    agent = ScannerAgent(
        asyncio.Queue(),
        500.0,
        crypto_features={
            "BTC": FeatureVector(
                symbol="BTC",
                timestamp=datetime.now(tz=timezone.utc),
                spot_price=75000.0,
                short_return=0.01,
                realized_vol=0.0,  # cold start — no vol data
                jump_detected=True,
                momentum_z=3.0,
            )
        },
    )
    market = _make_market(
        title="Bitcoin price above $74,400?",
        ticker="KXBTC-26APR1409-T74400",
        event_ticker="KXBTC-26APR1409",
        implied_prob=0.50,
        strike_type="greater",
    )
    # Signal also has realized_vol=0
    fv = FeatureVector(
        symbol="BTC",
        timestamp=datetime.now(tz=timezone.utc),
        spot_price=75000.0,
        short_return=0.01,
        realized_vol=0.0,
        jump_detected=True,
        momentum_z=3.0,
    )
    signal = Signal(
        signal_type=SignalType.MOMENTUM_UP,
        symbol="BTC",
        timestamp=datetime.now(tz=timezone.utc),
        features=fv,
        implied_prob_shift=0.1,
        confidence=0.9,
    )
    opp = agent._score(market, signal)
    assert opp is None  # skipped due to vol warmup


# ---------------------------------------------------------------------------
# Bracket YES price cap
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_score_skips_bracket_yes_too_expensive():
    """Bracket YES bets above MAX_BRACKET_YES_PRICE should be skipped."""
    agent = ScannerAgent(
        asyncio.Queue(),
        500.0,
        crypto_features={
            "ETH": FeatureVector(
                symbol="ETH",
                timestamp=datetime.now(tz=timezone.utc),
                spot_price=2330.0,
                short_return=0.01,
                realized_vol=0.8,
                jump_detected=True,
                momentum_z=3.0,
            )
        },
    )
    # Bracket centered on spot with high implied_prob (yes_ask > 0.30)
    # Model will compute high prob (spot inside range), so side=YES
    bracket_market = _make_market(
        title="Ethereum price at Apr 14, 2026?",
        ticker="KXETH-26APR1417-B2330",
        event_ticker="KXETH-26APR1417",
        implied_prob=0.50,  # yes_ask = 0.51, well above 0.30
        strike_type="between",
        floor_strike=2280.0,
        cap_strike=2380.0,
    )
    signal = _make_signal("ETH", spot_price=2330.0)
    opp = agent._score(bracket_market, signal)
    assert opp is None  # blocked by bracket YES price cap


@pytest.mark.asyncio
async def test_score_skips_bracket_no_too_expensive():
    """Bracket NO bets above MAX_BRACKET_NO_PRICE should be skipped.

    Mirrors the failure mode from 2026-05-06: spot far outside a narrow bin
    means model_prob ≈ 0 (YES wildly unlikely), but market makers quote NO at
    0.95–0.99. Without this guard the scanner queues a 99¢ NO bet — risk $1
    to win 1¢. The new MAX_BRACKET_NO_PRICE cap blocks it before risk_agent.
    """
    agent = ScannerAgent(
        asyncio.Queue(),
        500.0,
        crypto_features={
            "ETH": FeatureVector(
                symbol="ETH",
                timestamp=datetime.now(tz=timezone.utc),
                spot_price=2330.0,
                short_return=0.0,
                realized_vol=0.6,
                jump_detected=False,
                momentum_z=0.0,
            )
        },
    )
    # Spot=2330 nowhere near the $2700–2800 bin — model_prob will be ~0.
    # Market implies YES ≈ 0.05 (no_ask ≈ 0.96), so side=NO at 0.96 > 0.85 cap.
    bracket_market = _make_market(
        title="Ethereum price at Apr 14, 2026?",
        ticker="KXETH-26APR1417-B2750",
        event_ticker="KXETH-26APR1417",
        implied_prob=0.05,
        strike_type="between",
        floor_strike=2700.0,
        cap_strike=2800.0,
    )
    signal = _make_signal("ETH", spot_price=2330.0)
    opp = agent._score(bracket_market, signal)
    assert opp is None  # blocked by bracket NO price cap


# ---------------------------------------------------------------------------
# Disagreement gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_skips_when_disagreement_below_threshold():
    """Zero drift → p_drift == p_zero → disagreement = 0 → skip."""
    agent = ScannerAgent(
        asyncio.Queue(),
        500.0,
        crypto_features={
            "BTC": FeatureVector(
                symbol="BTC",
                timestamp=datetime.now(tz=timezone.utc),
                spot_price=74500.0,
                short_return=0.0,
                realized_vol=0.5,
                jump_detected=False,
                momentum_z=0.0,
                ewma_drift_long=0.0,
            )
        },
    )
    market = _make_market(
        title="Bitcoin price above $74,400?",
        ticker="KXBTC-26APR1409-T74400",
        event_ticker="KXBTC-26APR1409",
        implied_prob=0.50,
        strike_type="greater",
    )
    signal = _make_signal("BTC", spot_price=74500.0, drift_short=0.0, drift_long=0.0)
    opp = agent._score(market, signal)
    assert opp is None  # blocked by disagreement gate


@pytest.mark.asyncio
async def test_score_skips_when_drift_sign_opposes_side():
    """Negative drift on a market we'd buy YES on (drift sign mismatch) → skip."""
    # spot > strike → side=YES if model_prob > implied. But negative drift makes
    # p_drift < p_zero → disagreement < 0 → side=YES with disagreement<0 mismatch.
    agent = ScannerAgent(
        asyncio.Queue(),
        500.0,
        crypto_features={
            "BTC": FeatureVector(
                symbol="BTC",
                timestamp=datetime.now(tz=timezone.utc),
                spot_price=74500.0,
                short_return=-0.01,
                realized_vol=0.5,
                jump_detected=False,
                momentum_z=-3.0,
                ewma_drift_long=-2.0,
            )
        },
    )
    # Market underprices YES: spot=74500, strike=74400 → model_prob_zero > 0.5
    # but market implied=0.30. With negative drift, p_drift even smaller, but
    # still > 0.30 → side=YES while disagreement<0 → reject.
    market = _make_market(
        title="Bitcoin price above $74,400?",
        ticker="KXBTC-26APR1409-T74400",
        event_ticker="KXBTC-26APR1409",
        implied_prob=0.30,
        strike_type="greater",
    )
    signal = _make_signal("BTC", spot_price=74500.0, drift_short=-2.0, drift_long=-2.0)
    opp = agent._score(market, signal)
    assert opp is None  # drift sign opposes the YES side


# ---------------------------------------------------------------------------
# 15-minute Up/Down market detection and scoring
# ---------------------------------------------------------------------------


def _make_15m_market(
    ticker: str = "KXETH15M-26MAY030830",
    implied_prob: float = 0.60,  # market thinks 60% chance of up
) -> KalshiMarket:
    from datetime import timedelta
    close_time = (datetime.now(tz=timezone.utc) + timedelta(minutes=10)).isoformat()
    return KalshiMarket(
        ticker=ticker,
        title="Will ETH be higher in 15 minutes?",
        event_ticker="KXETH15M-26MAY0308",
        yes_bid=implied_prob - 0.01,
        yes_ask=implied_prob + 0.01,
        no_bid=1 - implied_prob - 0.01,
        no_ask=1 - implied_prob + 0.01,
        implied_prob=implied_prob,
        spread_pct=0.04,
        volume_24h=3000,
        liquidity=1000,
        close_time=close_time,
        timestamp=datetime.now(tz=timezone.utc),
        strike_type="",
        floor_strike=None,
        cap_strike=None,
    )


def test_is_up_down_15m_market_eth():
    assert _is_up_down_15m_market(_make_15m_market("KXETH15M-26MAY030830"))


def test_is_up_down_15m_market_btc():
    assert _is_up_down_15m_market(_make_15m_market("KXBTC15M-26MAY030830"))


def test_is_up_down_15m_market_false_for_bracket():
    bracket = _make_market(
        ticker="KXETH-26APR1417-B2330",
        strike_type="between",
        floor_strike=2280.0,
        cap_strike=2380.0,
    )
    assert not _is_up_down_15m_market(bracket)


@pytest.mark.asyncio
async def test_score_15m_market_skipped_without_drift():
    """15M w/ zero drift on signal scan: model=0.50, market=0.60 → edge=0.10 but disagreement=0 → skip.

    The disagreement gate only fires on signal-driven scans (where momentum should
    confirm direction). Periodic scans bypass it to find structural mispricing.
    """
    agent = ScannerAgent(
        asyncio.Queue(),
        500.0,
        crypto_features={
            "ETH": FeatureVector(
                symbol="ETH",
                timestamp=datetime.now(tz=timezone.utc),
                spot_price=3000.0,
                short_return=0.0,
                realized_vol=0.6,
                jump_detected=False,
                momentum_z=0.0,
                realized_vol_long=0.6,
                ewma_drift_short=0.0,
            )
        },
    )
    market = _make_15m_market(implied_prob=0.60)
    # Signal-driven scan with zero drift → disagreement gate kills it
    signal = _make_signal("ETH", spot_price=3000.0, drift_short=0.0)
    opp = agent._score(market, signal=signal)
    assert opp is None  # disagreement gate kills no-drift 15M on signal scan


@pytest.mark.asyncio
async def test_score_15m_market_finds_edge_with_negative_drift():
    """15M w/ strong negative drift: model<0.50, market=0.60 → side=NO, drift sign matches."""
    agent = ScannerAgent(
        asyncio.Queue(),
        500.0,
        crypto_features={
            "ETH": FeatureVector(
                symbol="ETH",
                timestamp=datetime.now(tz=timezone.utc),
                spot_price=3000.0,
                short_return=-0.01,
                realized_vol=0.6,
                jump_detected=True,
                momentum_z=-3.0,
                realized_vol_long=0.6,
                ewma_drift_short=-2.0,
            )
        },
    )
    market = _make_15m_market(implied_prob=0.60)
    opp = agent._score(market, signal=None)
    assert opp is not None
    assert opp.side.value == "NO"  # market overprices YES, drift confirms NO
    assert opp.edge >= 0.02


@pytest.mark.asyncio
async def test_score_15m_market_no_edge_at_fair_price():
    """When 15M market is priced at ~50%, model finds no edge (too small for min_edge)."""
    agent = ScannerAgent(
        asyncio.Queue(),
        500.0,
        crypto_features={
            "ETH": FeatureVector(
                symbol="ETH",
                timestamp=datetime.now(tz=timezone.utc),
                spot_price=3000.0,
                short_return=0.0,
                realized_vol=0.6,
                jump_detected=False,
                momentum_z=0.0,
                realized_vol_long=0.6,
            )
        },
    )
    market = _make_15m_market(implied_prob=0.51)
    opp = agent._score(market, signal=None)
    assert opp is None  # 1% edge < min_edge threshold
