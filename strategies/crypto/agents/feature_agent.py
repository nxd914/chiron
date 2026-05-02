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

from ..core.features import RollingWindow, VOL_WINDOW_LONG_SECONDS, compute_features
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
    ) -> None:
        self._ticks = tick_queue
        self._signals = signal_queue
        self._windows: dict[str, RollingWindow] = {}
        self._windows_long: dict[str, RollingWindow] = {}
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

        window = self._windows[symbol]
        window_long = self._windows_long[symbol]
        window.push(tick.price, ts)
        window_long.push(tick.price, ts)

        features = compute_features(window, tick, long_window=window_long)
        if features is None:
            return None

        self.latest_features[symbol] = features

        return features_to_signal(features)
