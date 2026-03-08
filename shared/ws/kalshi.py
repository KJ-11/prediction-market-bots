"""Kalshi WebSocket — real-time prices and trades via asyncio.Queue."""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal

import websockets

from shared.clients.kalshi import KalshiClient
from shared.types import PriceUpdate
from shared.utils.decimals import dec

logger = logging.getLogger(__name__)

KALSHI_WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"


class KalshiWSManager:
    """Manages Kalshi WebSocket connection for real-time streaming.

    Pushes PriceUpdate objects onto the provided asyncio.Queue.
    """

    def __init__(
        self,
        kalshi_client: KalshiClient,
        price_queue: asyncio.Queue[PriceUpdate],
        tickers: list[str] | None = None,
    ) -> None:
        self._kalshi_client = kalshi_client
        self._price_queue = price_queue
        self._tickers = tickers or []
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._ws: websockets.WebSocketClientProtocol | None = None

    def set_tickers(self, tickers: list[str]) -> None:
        """Update the list of tickers to subscribe to."""
        self._tickers = tickers

    async def start(self) -> None:
        """Start WS manager."""
        self._running = True
        conn_task = asyncio.create_task(self._connection_loop(), name="kalshi-ws")
        self._tasks = [conn_task]
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
        for task in self._tasks:
            task.cancel()

    async def _connection_loop(self) -> None:
        """WS connection with reconnection logic."""
        backoff = 1.0
        while self._running:
            try:
                auth_headers = self._kalshi_client.sign_ws()

                async with websockets.connect(
                    KALSHI_WS_URL,
                    additional_headers=auth_headers,
                    ping_interval=20,
                    close_timeout=10,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    logger.info("Kalshi WS: connected")
                    backoff = 1.0

                    if not self._tickers:
                        logger.warning("Kalshi WS: no tickers to subscribe, waiting 60s")
                        await asyncio.sleep(60)
                        continue

                    logger.info("Kalshi WS: subscribing to %d tickers", len(self._tickers))

                    batch_size = 500
                    sub_id = 1
                    for i in range(0, len(self._tickers), batch_size):
                        batch = self._tickers[i : i + batch_size]
                        await ws.send(json.dumps({
                            "id": sub_id,
                            "cmd": "subscribe",
                            "params": {
                                "channels": ["ticker", "trade"],
                                "market_tickers": batch,
                            },
                        }))
                        sub_id += 1
                        await asyncio.sleep(0.05)

                    logger.info("Kalshi WS: subscriptions sent")

                    while self._running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            await ws.ping()
                            continue

                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        await self._handle_message(msg)

            except websockets.ConnectionClosed as e:
                logger.warning("Kalshi WS: disconnected (%s)", e)
            except Exception as e:
                logger.error("Kalshi WS: error: %s", e)

            if self._running:
                logger.info("Kalshi WS: reconnecting in %.0fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _handle_message(self, msg: dict) -> None:
        """Process a single WS message and emit PriceUpdate."""
        msg_type = msg.get("type", "")
        payload = msg.get("msg", {})

        if msg_type == "ticker":
            await self._handle_ticker(payload)
        elif msg_type == "trade":
            await self._handle_trade(payload)

    async def _handle_ticker(self, payload: dict) -> None:
        """Convert ticker update to PriceUpdate and enqueue."""
        ticker = payload.get("market_ticker", "")
        if not ticker:
            return

        yes_price = dec(payload.get("price_dollars"))
        no_price = None
        if yes_price is not None:
            no_price = Decimal("1") - yes_price

        yes_bid = dec(payload.get("yes_bid_dollars"))
        yes_ask = dec(payload.get("yes_ask_dollars"))
        no_bid = dec(payload.get("no_bid_dollars"))
        no_ask = dec(payload.get("no_ask_dollars"))

        # Compute no_bid/no_ask from complementary if not provided
        if no_bid is None and yes_ask is not None:
            no_bid = Decimal("1") - yes_ask
        if no_ask is None and yes_bid is not None:
            no_ask = Decimal("1") - yes_bid

        update = PriceUpdate(
            market_id=ticker,
            yes_price=yes_price,
            no_price=no_price,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            yes_bid_size=dec(payload.get("yes_bid_size_fp")),
            yes_ask_size=dec(payload.get("yes_ask_size_fp")),
            volume=dec(payload.get("volume_fp")),
            last_trade_price=yes_price,
        )

        try:
            self._price_queue.put_nowait(update)
        except asyncio.QueueFull:
            logger.warning("Kalshi WS: price queue full, dropping update for %s", ticker)

    async def _handle_trade(self, payload: dict) -> None:
        """Convert trade to PriceUpdate (price-only)."""
        ticker = payload.get("market_ticker", "")
        if not ticker:
            return

        taker_side = (payload.get("taker_side") or "yes").lower()
        if taker_side == "no":
            price = dec(payload.get("no_price_dollars"))
        else:
            price = dec(payload.get("yes_price_dollars"))

        if price is None:
            return

        update = PriceUpdate(
            market_id=ticker,
            last_trade_price=price,
        )

        try:
            self._price_queue.put_nowait(update)
        except asyncio.QueueFull:
            pass
