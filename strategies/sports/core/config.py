"""
Configuration for the sports latency arb strategy.

All thresholds live here. Override via environment variables at runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SportsConfig:
    min_edge: float = 0.08
    poll_seconds: int = 30           # polling interval during active games
    idle_seconds: int = 3600         # polling interval when no games in progress
    leagues: tuple[str, ...] = ("NFL", "NBA", "MLB")
    bankroll_usdc: float = 10000.0
    max_concurrent_positions: int = 5
    kalshi_taker_fee_rate: float = 0.07

    @classmethod
    def from_env(cls) -> SportsConfig:
        def _float(key: str, default: float) -> float:
            v = os.environ.get(key)
            return float(v) if v is not None else default

        def _int(key: str, default: int) -> int:
            v = os.environ.get(key)
            return int(v) if v is not None else default

        leagues_raw = os.environ.get("SPORTS_LEAGUES", "NFL,NBA,MLB")
        leagues = tuple(lg.strip().upper() for lg in leagues_raw.split(",") if lg.strip())

        base = cls()
        return cls(
            min_edge=_float("SPORTS_MIN_EDGE", base.min_edge),
            poll_seconds=_int("SPORTS_POLL_SECONDS", base.poll_seconds),
            idle_seconds=_int("SPORTS_IDLE_SECONDS", base.idle_seconds),
            leagues=leagues,
            bankroll_usdc=_float("BANKROLL_USDC", base.bankroll_usdc),
            max_concurrent_positions=_int("SPORTS_MAX_POSITIONS", base.max_concurrent_positions),
        )
