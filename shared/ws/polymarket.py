"""Polymarket WebSocket feeds — market data + RTDS crypto prices.

Market channel: real-time order book, trades, best bid/ask (no auth).
RTDS channel: Binance crypto prices (no auth).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from decimal import Decimal

import websockets

from shared.utils.decimals import dec

logger = logging.getLogger(__name__)

MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
RTDS_WS_URL = "wss://ws-live-data.polymarket.com"

MARKET_PING_INTERVAL = 10.0  # seconds
RTDS_PING_INTERVAL = 5.0  # seconds
MAX_CONSECUTIVE_TIMEOUTS = 3


@dataclass(frozen=True)
class PolymarketBookUpdate:
    """Order book snapshot or best bid/ask update from market channel."""

    asset_id: str  # token ID
    condition_id: str  # market/condition ID
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    spread: Decimal | None = None
    # Full book (only on initial snapshot)
    bids: tuple[tuple[str, str], ...] | None = None  # ((price, size), ...)
    asks: tuple[tuple[str, str], ...] | None = None
    timestamp: int = 0  # unix ms


@dataclass(frozen=True)
class PolymarketTradeUpdate:
    """Trade execution from market channel."""

    asset_id: str
    condition_id: str
    price: Decimal
    size: Decimal
    side: str  # "BUY" or "SELL"
    timestamp: int = 0


@dataclass(frozen=True)
class PolymarketMarketResolved:
    """Market resolution event from market channel (requires custom_feature_enabled)."""

    condition_id: str
    outcome: str  # resolved outcome
    timestamp: int = 0


@dataclass(frozen=True)
class PolymarketCryptoPrice:
    """Crypto price from RTDS (Binance or Chainlink)."""

    symbol: str  # e.g. "btcusdt" or "btc/usd"
    price: Decimal
    timestamp: int = 0
    source: str = "binance"


class PolymarketMarketWSFeed:
    """Connects to Polymarket market channel WS, pushes updates to queues.

    Subscribes by token IDs (asset_ids). Receives:
    - book: full order book snapshot
    - best_bid_ask: top-of-book updates
    - last_trade_price: trade executions
    - price_change: order book deltas
    - tick_size_change: tick size updates
    - new_market: new market created (custom_feature_enabled)
    - market_resolved: market resolution (custom_feature_enabled)

    Auto-reconnects on disconnect with exponential backoff.
    """

    def __init__(
        self,
        asset_ids: list[str],
        book_queue: asyncio.Queue[PolymarketBookUpdate],
        trade_queue: asyncio.Queue[PolymarketTradeUpdate] | None = None,
        resolution_queue: asyncio.Queue[PolymarketMarketResolved] | None = None,
    ) -> None:
        self._asset_ids = asset_ids
        self._book_queue = book_queue
        self._trade_queue = trade_queue
        self._resolution_queue = resolution_queue
        self._running = False
        self._task: asyncio.Task | None = None

    def set_asset_ids(self, asset_ids: list[str]) -> None:
        """Update token IDs to subscribe to (takes effect on next reconnect)."""
        self._asset_ids = asset_ids

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(
            self._connection_loop(), name="polymarket-market-ws"
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
                    "Polymarket Market WS: connecting (%d assets)",
                    len(self._asset_ids),
                )

                async with websockets.connect(
                    MARKET_WS_URL,
                    ping_interval=None,  # we handle PING ourselves
                    close_timeout=10,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    # Subscribe
                    await ws.send(json.dumps({
                        "assets_ids": self._asset_ids,
                        "type": "market",
                        "custom_feature_enabled": True,
                    }))

                    logger.info("Polymarket Market WS: connected and subscribed")
                    backoff = 1.0

                    # Start ping task
                    ping_task = asyncio.create_task(
                        self._ping_loop(ws, MARKET_PING_INTERVAL)
                    )

                    try:
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
                                        "Polymarket Market WS: %d timeouts, reconnecting",
                                        consecutive_timeouts,
                                    )
                                    break
                                continue

                            if raw == "PONG":
                                continue

                            try:
                                msg = json.loads(raw)
                            except json.JSONDecodeError:
                                continue

                            self._handle_message(msg)
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

            except websockets.ConnectionClosed as e:
                logger.warning("Polymarket Market WS: disconnected (%s)", e)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Polymarket Market WS: error: %s", e)

            if self._running:
                logger.info("Polymarket Market WS: reconnecting in %.0fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _ping_loop(
        self,
        ws: websockets.WebSocketClientProtocol,
        interval: float,
    ) -> None:
        """Send PING at regular intervals."""
        while True:
            await asyncio.sleep(interval)
            try:
                await ws.send("PING")
            except Exception:
                return

    def _handle_message(self, msg: dict) -> None:
        """Route message to appropriate handler."""
        # Messages can be a list of events
        events = msg if isinstance(msg, list) else [msg]
        for event in events:
            event_type = event.get("event_type", "")
            if event_type == "book":
                self._handle_book(event)
            elif event_type == "best_bid_ask":
                self._handle_best_bid_ask(event)
            elif event_type == "last_trade_price":
                self._handle_trade(event)
            elif event_type == "price_change":
                self._handle_price_change(event)
            elif event_type == "market_resolved":
                self._handle_resolution(event)

    def _handle_book(self, event: dict) -> None:
        """Full order book snapshot."""
        asset_id = event.get("asset_id", "")
        condition_id = event.get("market", "")
        timestamp = event.get("timestamp", 0)

        raw_bids = event.get("bids", [])
        raw_asks = event.get("asks", [])

        bids = tuple(
            (b.get("price", "0"), b.get("size", "0")) for b in raw_bids
        )
        asks = tuple(
            (a.get("price", "0"), a.get("size", "0")) for a in raw_asks
        )

        # Extract best bid/ask from top of book
        best_bid = dec(bids[0][0]) if bids else None
        best_ask = dec(asks[0][0]) if asks else None
        spread = None
        if best_bid is not None and best_ask is not None:
            spread = best_ask - best_bid

        update = PolymarketBookUpdate(
            asset_id=asset_id,
            condition_id=condition_id,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            bids=bids,
            asks=asks,
            timestamp=timestamp,
        )

        try:
            self._book_queue.put_nowait(update)
        except asyncio.QueueFull:
            logger.warning(
                "Polymarket Market WS: book queue full, dropping %s", asset_id
            )

    def _handle_best_bid_ask(self, event: dict) -> None:
        """Top-of-book update (requires custom_feature_enabled)."""
        condition_id = event.get("market", "")
        timestamp = event.get("timestamp", 0)

        update = PolymarketBookUpdate(
            asset_id="",  # not provided in best_bid_ask events
            condition_id=condition_id,
            best_bid=dec(event.get("best_bid")),
            best_ask=dec(event.get("best_ask")),
            spread=dec(event.get("spread")),
            timestamp=timestamp,
        )

        try:
            self._book_queue.put_nowait(update)
        except asyncio.QueueFull:
            pass

    def _handle_trade(self, event: dict) -> None:
        """Trade execution."""
        if self._trade_queue is None:
            return

        price = dec(event.get("price"))
        size = dec(event.get("size"))
        if price is None or size is None:
            return

        update = PolymarketTradeUpdate(
            asset_id=event.get("asset_id", ""),
            condition_id=event.get("market", ""),
            price=price,
            size=size,
            side=event.get("side", ""),
            timestamp=event.get("timestamp", 0),
        )

        try:
            self._trade_queue.put_nowait(update)
        except asyncio.QueueFull:
            pass

    def _handle_price_change(self, event: dict) -> None:
        """Price change (order placed/cancelled).

        We rely on best_bid_ask events for top-of-book tracking instead.
        """

    def _handle_resolution(self, event: dict) -> None:
        """Market resolved event (requires custom_feature_enabled)."""
        if self._resolution_queue is None:
            return

        update = PolymarketMarketResolved(
            condition_id=event.get("market", ""),
            outcome=event.get("outcome", ""),
            timestamp=event.get("timestamp", 0),
        )

        try:
            self._resolution_queue.put_nowait(update)
        except asyncio.QueueFull:
            pass


class PolymarketRTDSFeed:
    """Connects to Polymarket RTDS WebSocket for real-time crypto prices.

    Provides Binance and/or Chainlink price feeds for BTC, ETH, SOL, XRP.

    Subscription format:
        {"action": "subscribe", "subscriptions": [
            {"topic": "crypto_prices", "type": "update"}
        ]}
    """

    def __init__(
        self,
        price_queue: asyncio.Queue[PolymarketCryptoPrice],
        sources: list[str] | None = None,
    ) -> None:
        self._price_queue = price_queue
        self._sources = sources or ["crypto_prices"]
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(
            self._connection_loop(), name="polymarket-rtds"
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
                logger.info("Polymarket RTDS: connecting")

                async with websockets.connect(
                    RTDS_WS_URL,
                    ping_interval=None,  # we handle PING ourselves
                    close_timeout=10,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    # Subscribe — no filter = all symbols
                    subscriptions = []
                    for topic in self._sources:
                        sub: dict = {"topic": topic, "type": "update"}
                        if topic == "crypto_prices_chainlink":
                            sub["type"] = "*"
                        subscriptions.append(sub)

                    await ws.send(json.dumps({
                        "action": "subscribe",
                        "subscriptions": subscriptions,
                    }))

                    logger.info(
                        "Polymarket RTDS: connected, subscribed to %s",
                        self._sources,
                    )
                    backoff = 1.0

                    ping_task = asyncio.create_task(
                        self._ping_loop(ws)
                    )

                    try:
                        consecutive_timeouts = 0
                        while self._running:
                            try:
                                raw = await asyncio.wait_for(
                                    ws.recv(), timeout=15.0
                                )
                                consecutive_timeouts = 0
                            except asyncio.TimeoutError:
                                consecutive_timeouts += 1
                                if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                                    logger.warning(
                                        "Polymarket RTDS: %d timeouts, reconnecting",
                                        consecutive_timeouts,
                                    )
                                    break
                                continue

                            # Handle server ping
                            if raw == "ping":
                                await ws.send("pong")
                                continue
                            if raw == "PONG":
                                continue

                            try:
                                msg = json.loads(raw)
                            except json.JSONDecodeError:
                                continue

                            self._handle_message(msg)
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

            except websockets.ConnectionClosed as e:
                logger.warning("Polymarket RTDS: disconnected (%s)", e)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Polymarket RTDS: error: %s", e)

            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _ping_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        while True:
            await asyncio.sleep(RTDS_PING_INTERVAL)
            try:
                await ws.send("PING")
            except Exception:
                return

    def _handle_message(self, msg: dict) -> None:
        """Parse RTDS crypto price message.

        Format: {"topic": "crypto_prices", "type": "update",
                 "timestamp": 1753314064237,
                 "payload": {"symbol": "btcusdt", "timestamp": ..., "value": 71236.01}}
        """
        topic = msg.get("topic", "")
        if "crypto_prices" not in topic:
            return

        payload = msg.get("payload", {})
        symbol = payload.get("symbol", "")
        value = payload.get("value")
        timestamp = payload.get("timestamp", 0)

        if not symbol or value is None:
            return

        source = "chainlink" if "chainlink" in topic else "binance"
        self._enqueue_price(symbol, value, timestamp, source)

    def _enqueue_price(
        self, symbol: str, value: object, timestamp: int, source: str,
    ) -> None:
        try:
            price = Decimal(str(value))
        except (ValueError, TypeError):
            return

        update = PolymarketCryptoPrice(
            symbol=symbol,
            price=price,
            timestamp=timestamp,
            source=source,
        )

        try:
            self._price_queue.put_nowait(update)
        except asyncio.QueueFull:
            pass
