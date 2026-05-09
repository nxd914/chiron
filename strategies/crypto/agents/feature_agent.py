"""
Feature Agent

Consumes raw Tick objects from CryptoFeedAgent and computes real-time
features using the Welford rolling window engine in core/features.py.

Emits Signals (via features_to_signal) to the signal queue consumed
by the ScannerAgent. Also exposes a latest_features dict that the
scanner queries during periodic scans (same pattern as
WebsocketAgent.price_cache).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..core.config import Config, DEFAULT_CONFIG
from ..core.features import (
    EWMA_DRIFT_LONG_HALF_LIFE_S,
    EWMA_DRIFT_SHORT_HALF_LIFE_S,
    EwmaDrift,
    EwmaObi,
    RollingWindow,
    VOL_WINDOW_1H_SECONDS,
    VOL_WINDOW_LONG_SECONDS,
    compute_features,
)
from ..core.models import FeatureVector, Signal, Tick
from ..core.pricing import features_to_signal

logger = logging.getLogger(__name__)


class FeatureAgent:
    """
    Stateful per-symbol feature computation.

    Maintains two rolling windows per symbol:
      - 60-second window for signal detection (jump/momentum)
      - 15-minute window for pricing vol (more stable for 1-4h contracts)

    Public state:
        latest_features: dict mapping symbol -> most recent FeatureVector.
        Other agents (ScannerAgent) read this for periodic pricing.
    """

    def __init__(
        self,
        tick_queue: asyncio.Queue[Tick],
        signal_queue: asyncio.Queue[Signal],
        config: Optional[Config] = None,
    ) -> None:
        self._ticks = tick_queue
        self._signals = signal_queue
        self._cfg = config or DEFAULT_CONFIG
        self._windows: dict[str, RollingWindow] = {}
        self._windows_long: dict[str, RollingWindow] = {}
        self._windows_1h: dict[str, RollingWindow] = {}
        self._drift_short: dict[str, EwmaDrift] = {}
        self._drift_long: dict[str, EwmaDrift] = {}
        self._obi_trackers: dict[str, EwmaObi] = {}
        self.latest_features: dict[str, FeatureVector] = {}

    async def run(self) -> None:
        """Consume ticks indefinitely, compute features, emit signals."""
        logger.info("FeatureAgent: started")
        while True:
            tick = await self._ticks.get()
            signal = self._process_tick(tick)
            if signal is not None:
                await self._signals.put(signal)

    def _process_tick(self, tick: Tick) -> Optional[Signal]:
        """Ingest tick, update both windows, compute features, maybe fire signal."""
        symbol = tick.symbol
        ts = tick.timestamp.timestamp()

        # Lazy-init windows for new symbols
        if symbol not in self._windows:
            self._windows[symbol] = RollingWindow()
        if symbol not in self._windows_long:
            self._windows_long[symbol] = RollingWindow(max_age_seconds=VOL_WINDOW_LONG_SECONDS)
        if symbol not in self._windows_1h:
            self._windows_1h[symbol] = RollingWindow(max_age_seconds=VOL_WINDOW_1H_SECONDS)
        if symbol not in self._drift_short:
            self._drift_short[symbol] = EwmaDrift(EWMA_DRIFT_SHORT_HALF_LIFE_S)
        if symbol not in self._drift_long:
            self._drift_long[symbol] = EwmaDrift(EWMA_DRIFT_LONG_HALF_LIFE_S)
        if symbol not in self._obi_trackers:
            self._obi_trackers[symbol] = EwmaObi(half_life_seconds=5.0)

        window = self._windows[symbol]
        window_long = self._windows_long[symbol]
        window_1h = self._windows_1h[symbol]
        drift_short = self._drift_short[symbol]
        drift_long = self._drift_long[symbol]
        obi_tracker = self._obi_trackers[symbol]
        window.push(tick.price, ts)
        window_long.push(tick.price, ts)
        window_1h.push(tick.price, ts)
        drift_short.push(tick.price, ts)
        drift_long.push(tick.price, ts)
        obi_tracker.push(tick.obi, ts)

        features = compute_features(
            window,
            tick,
            short_window_seconds=self._cfg.short_return_window_seconds,
            jump_return_threshold=self._cfg.jump_return_threshold,
            long_window=window_long,
            window_1h=window_1h,
            drift_short=drift_short,
            drift_long=drift_long,
            obi_tracker=obi_tracker,
        )
        if features is None:
            return None

        self.latest_features[symbol] = features

        return features_to_signal(features, config=self._cfg)
