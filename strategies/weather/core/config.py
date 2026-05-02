"""
Configuration for the weather latency arb strategy.

All thresholds live here. Override via environment variables at runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WeatherConfig:
    min_edge: float = 0.08
    poll_minutes: int = 15            # NWS observation poll interval
    max_observation_age_minutes: int = 60  # ignore stale observations
    bankroll_usdc: float = 10000.0
    max_concurrent_positions: int = 5
    kalshi_taker_fee_rate: float = 0.07

    @classmethod
    def from_env(cls) -> WeatherConfig:
        def _float(key: str, default: float) -> float:
            v = os.environ.get(key)
            return float(v) if v is not None else default

        def _int(key: str, default: int) -> int:
            v = os.environ.get(key)
            return int(v) if v is not None else default

        base = cls()
        return cls(
            min_edge=_float("WEATHER_MIN_EDGE", base.min_edge),
            poll_minutes=_int("WEATHER_POLL_MINUTES", base.poll_minutes),
            max_observation_age_minutes=_int("WEATHER_MAX_OBS_AGE_MINUTES", base.max_observation_age_minutes),
            bankroll_usdc=_float("BANKROLL_USDC", base.bankroll_usdc),
            max_concurrent_positions=_int("WEATHER_MAX_POSITIONS", base.max_concurrent_positions),
        )
