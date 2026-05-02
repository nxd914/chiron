"""
Configuration for the economic data strategy.

All thresholds live here. Override via environment variables at runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EconConfig:
    min_edge: float = 0.08
    # 8% minimum edge — higher bar than crypto because signals are infrequent
    # (monthly) and we want high confidence before acting.

    kelly_fraction_cap: float = 0.25

    max_concurrent_positions: int = 3
    # At most 3 open econ positions simultaneously (CPI + NFP + FOMC).

    kalshi_taker_fee_rate: float = 0.07

    bankroll_usdc: float = 10000.0

    pre_release_window_minutes: float = 10.0
    # Start watching Kalshi market prices 10 min before scheduled release.

    post_release_timeout_minutes: float = 5.0
    # Give up polling BLS API if actual data doesn't appear within 5 min.

    poll_interval_seconds: float = 3.0
    # Poll BLS API every 3 seconds after scheduled release time.

    @classmethod
    def from_env(cls) -> EconConfig:
        def _float(key: str, default: float) -> float:
            v = os.environ.get(key)
            return float(v) if v is not None else default

        def _int(key: str, default: int) -> int:
            v = os.environ.get(key)
            return int(v) if v is not None else default

        base = cls()
        return cls(
            min_edge=_float("ECON_MIN_EDGE", base.min_edge),
            kelly_fraction_cap=_float("ECON_KELLY_FRACTION_CAP", base.kelly_fraction_cap),
            max_concurrent_positions=_int("ECON_MAX_CONCURRENT_POSITIONS", base.max_concurrent_positions),
            bankroll_usdc=_float("BANKROLL_USDC", base.bankroll_usdc),
            post_release_timeout_minutes=_float("ECON_TIMEOUT_MINUTES", base.post_release_timeout_minutes),
            poll_interval_seconds=_float("ECON_POLL_SECONDS", base.poll_interval_seconds),
        )
