"""
Shared Kalshi API data models used across all strategies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class KalshiMarket:
    """A Kalshi prediction market that may be tradeable."""
    ticker: str              # unique market identifier, e.g. "KXFED-25MAY-T5.25"
    title: str               # market title / question (up to 200 chars)
    event_ticker: str        # parent event ticker
    yes_bid: float           # best bid for YES side (0–1 USD)
    yes_ask: float           # best ask for YES side (0–1 USD)
    no_bid: float            # best bid for NO side (0–1 USD)
    no_ask: float            # best ask for NO side (0–1 USD)
    implied_prob: float      # mid of yes_bid / yes_ask
    spread_pct: float        # (yes_ask - yes_bid) / implied_prob
    yes_ask_size: float = 0.0 # available size at YES best ask (USD)
    yes_bid_size: float = 0.0 # available size at YES best bid (USD)
    no_ask_size: float = 0.0  # available size at NO best ask (USD)
    no_bid_size: float = 0.0  # available size at NO best bid (USD)
    volume_24h: float = 0.0   # 24-hour traded volume in USD
    liquidity: float = 0.0    # available liquidity depth in USD
    close_time: str = ""      # ISO datetime when the market closes
    timestamp: datetime = field(default_factory=datetime.utcnow) # snapshot time
    strike_type: str = ""    # "greater" | "less" | "between" | "" (from Kalshi API)
    floor_strike: Optional[float] = None  # lower bound (threshold floor or bracket floor)
    cap_strike: Optional[float] = None    # upper bound (bracket cap only)
    status: str = ""         # "unopened" | "open" | "closed" | "settled"
    result: str = ""         # "yes" | "no" | "" (populated when settled)
