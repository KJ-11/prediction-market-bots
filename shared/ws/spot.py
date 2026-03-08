"""Spot price WebSocket — real-time crypto prices via asyncio.Queue.

Uses Coinbase WS (US-friendly, high frequency ~1-5 updates/sec).
Optionally connects to Kraken WS as a second source for CF Benchmarks
cross-validation (Kraken is a CF Benchmarks constituent exchange).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from decimal import Decimal

import websockets

logger = logging.getLogger(__name__)

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"
KRAKEN_WS_URL = "wss://ws.kraken.com/v2"

# Mapping from coin symbol to Coinbase product ID
COIN_TO_COINBASE: dict[str, str] = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
}

# Mapping from coin symbol to Kraken pair
COIN_TO_KRAKEN: dict[str, str] = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
    "SOL": "SOL/USD",
    "XRP": "XRP/USD",
}

MAX_CONSECUTIVE_TIMEOUTS = 3  # Force reconnect after this many


@dataclass(frozen=True)
class SpotPriceUpdate:
    """A single spot price update."""

    symbol: str  # e.g. BTC-USD
    price: Decimal
    timestamp_ms: int
    source: str = "coinbase"  # "coinbase" or "kraken"


class SpotWSFeed:
    """Connects to Coinbase ticker WS, pushes SpotPriceUpdate onto a queue.

    Auto-reconnects on disconnect with exponential backoff.
    """

    def __init__(
        self,
        coins: list[str],
        price_queue: asyncio.Queue[SpotPriceUpdate],
    ) -> None:
        self._coins = coins
        self._product_ids = [
            COIN_TO_COINBASE[c.upper()]
            for c in coins
            if c.upper() in COIN_TO_COINBASE
        ]
        self._price_queue = price_queue
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the WS feed as a background task."""
        self._running = True
        self._task = asyncio.create_task(
            self._connection_loop(), name="spot-ws"
        )

    async def stop(self) -> None:
        """Stop the feed gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _connection_loop(self) -> None:
        """WS connection with reconnect logic."""
        backoff = 1.0
        while self._running:
            try:
                logger.info(
                    "Spot WS: connecting to Coinbase (%s)",
                    ", ".join(self._product_ids),
                )

                async with websockets.connect(
                    COINBASE_WS_URL,
                    ping_interval=20,
                    close_timeout=10,
                    max_size=1 * 1024 * 1024,
                ) as ws:
                    # Subscribe to ticker channel
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "product_ids": self._product_ids,
                        "channels": ["ticker"],
                    }))

                    logger.info("Spot WS: connected and subscribed")
                    backoff = 1.0

                    consecutive_timeouts = 0
                    while self._running:
                        try:
                            raw = await asyncio.wait_for(
                                ws.recv(), timeout=30.0
                            )
                            consecutive_timeouts = 0
                        except asyncio.TimeoutError:
                            consecutive_timeouts += 1
                            if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                                logger.warning(
                                    "Spot WS: %d consecutive timeouts, forcing reconnect",
                                    consecutive_timeouts,
                                )
                                break
                            continue

                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        self._handle_message(msg)

            except websockets.ConnectionClosed as e:
                logger.warning("Spot WS: disconnected (%s)", e)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Spot WS: error: %s", e)

            if self._running:
                logger.info("Spot WS: reconnecting in %.0fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    def _handle_message(self, msg: dict) -> None:
        """Parse Coinbase ticker message and enqueue SpotPriceUpdate."""
        if msg.get("type") != "ticker":
            return

        price_str = msg.get("price")
        if not price_str:
            return

        try:
            # Coinbase time is ISO 8601, convert to ms
            update = SpotPriceUpdate(
                symbol=msg.get("product_id", ""),
                price=Decimal(price_str),
                timestamp_ms=int(msg.get("sequence", 0)),
            )
        except (ValueError, TypeError):
            return

        try:
            self._price_queue.put_nowait(update)
        except asyncio.QueueFull:
            pass


class KrakenWSFeed:
    """Connects to Kraken WS v2 ticker, pushes SpotPriceUpdate onto a queue.

    Kraken is a CF Benchmarks constituent exchange — comparing its prices
    to Coinbase helps validate our spot proxy against the actual resolution
    source.
    """

    def __init__(
        self,
        coins: list[str],
        price_queue: asyncio.Queue[SpotPriceUpdate],
    ) -> None:
        self._coins = coins
        self._pairs = [
            COIN_TO_KRAKEN[c.upper()]
            for c in coins
            if c.upper() in COIN_TO_KRAKEN
        ]
        self._price_queue = price_queue
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(
            self._connection_loop(), name="kraken-ws"
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _connection_loop(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                logger.info(
                    "Kraken WS: connecting (%s)", ", ".join(self._pairs),
                )

                async with websockets.connect(
                    KRAKEN_WS_URL,
                    ping_interval=20,
                    close_timeout=10,
                    max_size=1 * 1024 * 1024,
                ) as ws:
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "params": {
                            "channel": "ticker",
                            "symbol": self._pairs,
                        },
                    }))

                    logger.info("Kraken WS: connected and subscribed")
                    backoff = 1.0

                    consecutive_timeouts = 0
                    while self._running:
                        try:
                            raw = await asyncio.wait_for(
                                ws.recv(), timeout=30.0
                            )
                            consecutive_timeouts = 0
                        except asyncio.TimeoutError:
                            consecutive_timeouts += 1
                            if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                                logger.warning(
                                    "Kraken WS: %d consecutive timeouts, forcing reconnect",
                                    consecutive_timeouts,
                                )
                                break
                            continue

                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        self._handle_message(msg)

            except websockets.ConnectionClosed as e:
                logger.warning("Kraken WS: disconnected (%s)", e)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Kraken WS: error: %s", e)

            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    def _handle_message(self, msg: dict) -> None:
        """Parse Kraken v2 ticker message and enqueue SpotPriceUpdate."""
        if msg.get("channel") != "ticker":
            return

        for item in msg.get("data", []):
            symbol = item.get("symbol", "")
            last_price = item.get("last")
            if not last_price:
                continue

            try:
                update = SpotPriceUpdate(
                    symbol=symbol,
                    price=Decimal(str(last_price)),
                    timestamp_ms=0,
                    source="kraken",
                )
            except (ValueError, TypeError):
                continue

            try:
                self._price_queue.put_nowait(update)
            except asyncio.QueueFull:
                pass
