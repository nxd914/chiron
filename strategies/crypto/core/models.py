"""
Crypto strategy data models.
KalshiMarket is shared — imported from core.models.
All types are immutable dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from core.models import KalshiMarket  # noqa: F401  (re-exported for local imports)


class Side(str, Enum):
    YES = "YES"
    NO = "NO"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class SignalType(str, Enum):
    MOMENTUM_UP = "MOMENTUM_UP"
    MOMENTUM_DOWN = "MOMENTUM_DOWN"


@dataclass(frozen=True)
class Tick:
    """Normalized price tick from a CEX (Binance or Coinbase)."""
    exchange: str          # "binance" | "coinbase"
    symbol: str            # e.g. "BTCUSDT"
    price: float
    timestamp: datetime
    volume: float = 0.0


@dataclass(frozen=True)
class FeatureVector:
    """
    Spot-derived features computed by the feature agent.
    Separating feature computation from decision logic keeps the decision
    rule mathematically defensible and avoids overfitting.
    """
    symbol: str
    timestamp: datetime
    spot_price: float         # current CEX spot price at feature computation time
    short_return: float       # return over last N seconds
    realized_vol: float       # rolling annualized vol (60s window, for signal detection)
    jump_detected: bool       # True if return exceeds jump threshold
    momentum_z: float         # z-score of short return vs rolling mean
    realized_vol_long: float = 0.0  # rolling annualized vol (15min window, for pricing). Falls back to realized_vol if 0.


@dataclass(frozen=True)
class Signal:
    """
    Deterministic output of the decision rule: features → implied prob shift.
    No learned model — pure math so the edge is defensible.
    """
    signal_type: SignalType
    symbol: str
    timestamp: datetime
    features: FeatureVector
    implied_prob_shift: float   # estimated direction of market prob move
    confidence: float           # in [0, 1], derived from feature strength
    # Optional: both clubs + actor (e.g. scorer) for Kalshi title matching
    match_teams: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TradeOpportunity:
    """
    A scored market opportunity where model probability diverges from
    Kalshi's implied probability enough to justify a position.
    """
    signal: Signal
    market: KalshiMarket
    side: Side
    model_prob: float        # our estimated probability
    market_prob: float       # Kalshi's current implied probability (market mid)
    edge: float              # |model_prob - market_prob|
    kelly_fraction: float    # unconstrained Kelly bet fraction
    capped_fraction: float   # Kelly capped at MAX_KELLY_FRACTION


@dataclass(frozen=True)
class Order:
    """A paper or live order placed on Kalshi."""
    opportunity: TradeOpportunity
    size_usdc: float
    status: OrderStatus
    fill_price: Optional[float]
    placed_at: datetime
    filled_at: Optional[datetime] = None
    order_id: Optional[str] = None
    error: Optional[str] = None


