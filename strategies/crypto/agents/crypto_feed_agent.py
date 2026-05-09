import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from ..core.models import Tick

logger = logging.getLogger(__name__)

KRAKEN_WS_URL = "wss://ws.kraken.com"

# Internal symbol -> Kraken pair
_SYMBOL_TO_KRAKEN = {
    "BTC": "XBT/USD",
    "ETH": "ETH/USD",
    "SOL": "SOL/USD",
    "XRP": "XRP/USD",
}

_KRAKEN_TO_SYMBOL = {v: k for k, v in _SYMBOL_TO_KRAKEN.items()}

MAX_RECONNECT_DELAY = 60.0
INITIAL_RECONNECT_DELAY = 1.0


class CryptoFeedAgent:
    """
    Kraken WebSocket ingestion.

    Subscribes to ticker streams on Kraken. Normalizes all messages into Tick
    objects and pushes them to tick_queue. (Binance and Coinbase block GCP).
    """

    def __init__(
        self,
        tick_queue: asyncio.Queue[Tick],
        symbols: Optional[list[str]] = None,
    ) -> None:
        self._tick_queue = tick_queue
        self._symbols = symbols or ["BTC", "ETH"]

    async def run(self) -> None:
        """Start the Kraken feed."""
        logger.info("CryptoFeedAgent: starting feeds for %s", self._symbols)
        await self._kraken_feed()

    async def _kraken_feed(self) -> None:
        """Connect to Kraken ticker channel with auto-reconnect."""
        import websockets

        pairs = [
            _SYMBOL_TO_KRAKEN[s]
            for s in self._symbols
            if s in _SYMBOL_TO_KRAKEN
        ]
        if not pairs:
            logger.warning("CryptoFeedAgent: no Kraken streams configured")
            return

        subscribe_msg = json.dumps({
            "event": "subscribe",
            "pair": pairs,
            "subscription": {"name": "ticker"}
        })

        retry_delay = INITIAL_RECONNECT_DELAY

        while True:
            try:
                async with websockets.connect(KRAKEN_WS_URL) as ws:
                    await ws.send(subscribe_msg)
                    logger.info(
                        "CryptoFeedAgent: connected to Kraken (%s)", pairs,
                    )
                    retry_delay = INITIAL_RECONNECT_DELAY

                    async for raw in ws:
                        tick = self._parse_kraken(raw)
                        if tick is not None:
                            await self._tick_queue.put(tick)

            except Exception as exc:
                logger.warning(
                    "CryptoFeedAgent Kraken error: %s. Reconnecting in %.1fs",
                    exc, retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(MAX_RECONNECT_DELAY, retry_delay * 2)

    def _parse_kraken(self, raw: str) -> Optional[Tick]:
        """Parse Kraken ticker message into a Tick.
        
        Example payload:
        [119930888,{"a":["79300.10000",2,"2.79261056"],"b":["79300.00000",0,"0.32492162"],"c":["79300.10000","0.00150667"]...},"ticker","XBT/USD"]
        """
        try:
            msg = json.loads(raw)
            if not isinstance(msg, list) or len(msg) < 4:
                return None
            
            # Message format: [channelID, payload, channelName, pair]
            channel_name = msg[2]
            pair = msg[3]
            
            if channel_name != "ticker":
                return None

            symbol = _KRAKEN_TO_SYMBOL.get(pair)
            if symbol is None:
                return None

            payload = msg[1]
            if "c" not in payload:
                return None
                
            # c[0] is the last trade price
            price = float(payload["c"][0])
            
            # Extract top-of-book volumes to calculate Order Book Imbalance (OBI)
            ask_vol = float(payload["a"][2]) if "a" in payload else 0.0
            bid_vol = float(payload["b"][2]) if "b" in payload else 0.0
            obi = 0.0
            if ask_vol + bid_vol > 0:
                obi = (bid_vol - ask_vol) / (bid_vol + ask_vol)
            
            return Tick(
                exchange="kraken",
                symbol=symbol,
                price=price,
                timestamp=datetime.now(tz=timezone.utc),  # Kraken WS ticker doesn't send ts, use local
                volume=0.0,  # We don't strictly need volume for pricing features
                obi=obi,
            )
        except Exception:
            return None
