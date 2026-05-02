"""
Crypto Feed Agent

Persistent dual-exchange WebSocket ingestion engine.
Maintains connections to Binance and Coinbase, emitting normalized
Tick objects to a shared queue consumed by the FeatureAgent.

Both feeds run as concurrent async tasks with independent reconnect
logic. If one exchange drops, the other continues feeding.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from ..core.models import Tick

logger = logging.getLogger(__name__)

BINANCE_WS_BASE = "wss://stream.binance.us:9443/stream"
COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"

# Map exchange-native symbols to internal normalized format
_BINANCE_SYMBOL_MAP = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
    "XRPUSDT": "XRP",
}
_COINBASE_SYMBOL_MAP = {
    "BTC-USD": "BTC",
    "ETH-USD": "ETH",
    "SOL-USD": "SOL",
    "XRP-USD": "XRP",
}

# Internal symbol -> exchange subscription identifiers
_SYMBOL_TO_BINANCE = {v: k.lower() for k, v in _BINANCE_SYMBOL_MAP.items()}
_SYMBOL_TO_COINBASE = {v: k for k, v in _COINBASE_SYMBOL_MAP.items()}

MAX_RECONNECT_DELAY = 60.0
INITIAL_RECONNECT_DELAY = 1.0


class CryptoFeedAgent:
    """
    Dual-exchange WebSocket ingestion.

    Subscribes to aggregated trade streams on Binance and ticker streams
    on Coinbase.  Normalizes all messages into Tick objects and pushes
    them to tick_queue.
    """

    def __init__(
        self,
        tick_queue: asyncio.Queue[Tick],
        symbols: Optional[list[str]] = None,
    ) -> None:
        self._tick_queue = tick_queue
        self._symbols = symbols or ["BTC", "ETH"]

    async def run(self) -> None:
        """Start both exchange feeds concurrently."""
        logger.info("CryptoFeedAgent: starting feeds for %s", self._symbols)
        await asyncio.gather(
            self._binance_feed(),
            self._coinbase_feed(),
        )

    # ------------------------------------------------------------------
    # Binance
    # ------------------------------------------------------------------

    async def _binance_feed(self) -> None:
        """Connect to Binance aggTrade streams with auto-reconnect."""
        import websockets

        streams = [
            f"{_SYMBOL_TO_BINANCE[s]}@aggTrade"
            for s in self._symbols
            if s in _SYMBOL_TO_BINANCE
        ]
        if not streams:
            logger.warning("CryptoFeedAgent: no Binance streams configured")
            return

        url = f"{BINANCE_WS_BASE}?streams={'/'.join(streams)}"
        retry_delay = INITIAL_RECONNECT_DELAY

        while True:
            try:
                async with websockets.connect(url) as ws:
                    logger.info("CryptoFeedAgent: connected to Binance (%s)", streams)
                    retry_delay = INITIAL_RECONNECT_DELAY

                    async for raw in ws:
                        tick = self._parse_binance(raw)
                        if tick is not None:
                            await self._tick_queue.put(tick)

            except Exception as exc:
                logger.warning(
                    "CryptoFeedAgent Binance error: %s. Reconnecting in %.1fs",
                    exc, retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(MAX_RECONNECT_DELAY, retry_delay * 2)

    def _parse_binance(self, raw: str) -> Optional[Tick]:
        """Parse Binance aggTrade message into a Tick."""
        try:
            msg = json.loads(raw)
            # Combined stream wraps in {"stream": "...", "data": {...}}
            data = msg.get("data", msg)
            if data.get("e") != "aggTrade":
                return None

            raw_symbol = data["s"]  # e.g. "BTCUSDT"
            symbol = _BINANCE_SYMBOL_MAP.get(raw_symbol)
            if symbol is None:
                return None

            return Tick(
                exchange="binance",
                symbol=symbol,
                price=float(data["p"]),
                timestamp=datetime.fromtimestamp(
                    data["T"] / 1000.0, tz=timezone.utc,
                ),
                volume=float(data["q"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("Binance parse error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Coinbase
    # ------------------------------------------------------------------

    async def _coinbase_feed(self) -> None:
        """Connect to Coinbase ticker channel with auto-reconnect."""
        import websockets

        product_ids = [
            _SYMBOL_TO_COINBASE[s]
            for s in self._symbols
            if s in _SYMBOL_TO_COINBASE
        ]
        if not product_ids:
            logger.warning("CryptoFeedAgent: no Coinbase products configured")
            return

        subscribe_msg = json.dumps({
            "type": "subscribe",
            "channels": ["ticker"],
            "product_ids": product_ids,
        })

        retry_delay = INITIAL_RECONNECT_DELAY

        while True:
            try:
                async with websockets.connect(COINBASE_WS_URL) as ws:
                    await ws.send(subscribe_msg)
                    logger.info(
                        "CryptoFeedAgent: connected to Coinbase (%s)", product_ids,
                    )
                    retry_delay = INITIAL_RECONNECT_DELAY

                    async for raw in ws:
                        tick = self._parse_coinbase(raw)
                        if tick is not None:
                            await self._tick_queue.put(tick)

            except Exception as exc:
                logger.warning(
                    "CryptoFeedAgent Coinbase error: %s. Reconnecting in %.1fs",
                    exc, retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(MAX_RECONNECT_DELAY, retry_delay * 2)

    def _parse_coinbase(self, raw: str) -> Optional[Tick]:
        """Parse Coinbase ticker message into a Tick."""
        try:
            msg = json.loads(raw)
            if msg.get("type") != "ticker":
                return None

            product_id = msg["product_id"]  # e.g. "BTC-USD"
            symbol = _COINBASE_SYMBOL_MAP.get(product_id)
            if symbol is None:
                return None

            raw_time = msg.get("time")
            if raw_time:
                ts = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            else:
                ts = datetime.now(tz=timezone.utc)
            return Tick(
                exchange="coinbase",
                symbol=symbol,
                price=float(msg["price"]),
                timestamp=ts,
                volume=float(msg.get("last_size", 0)),
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("Coinbase parse error: %s", exc)
            return None
