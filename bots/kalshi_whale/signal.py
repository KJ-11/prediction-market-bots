# Whale trade detection and signal aggregation via Kalshi WebSocket.

"""Whale signal detector — processes Kalshi WS trade messages.

Subscribes to the trade channel for watchlist markets. Filters trades by
notional >= whale_threshold ($1,000). Aggregates whale trades per market
and emits WhaleSignal when all criteria are met.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

import websockets

from bots.kalshi_whale.discovery import Watchlist
from bots.kalshi_whale.strategy import (
    MarketWhaleState,
    WhaleConfig,
    WhaleSignal,
    WhaleTrade,
)
from bots.kalshi_whale.tracking import WhaleTracker
from shared.alerts.manager import AlertManager
from shared.clients.kalshi import KalshiClient
from shared.types import PriceUpdate
from shared.utils.decimals import dec

logger = logging.getLogger(__name__)

KALSHI_WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"


class WhaleDetector:
    """Detects whale trades and scores markets for entry signals.

    Runs a WebSocket connection to Kalshi, subscribes to trade + ticker
    channels for all watchlist markets. When a market hits signal criteria,
    puts a WhaleSignal onto the signal_queue.
    """

    def __init__(
        self,
        client: KalshiClient,
        config: WhaleConfig,
        watchlist: Watchlist,
        signal_queue: asyncio.Queue[WhaleSignal],
        price_queue: asyncio.Queue[PriceUpdate],
        tracker: WhaleTracker,
        alerts: AlertManager,
    ) -> None:
        self._client = client
        self._config = config
        self._watchlist = watchlist
        self._signal_queue = signal_queue
        self._price_queue = price_queue
        self._tracker = tracker
        self._alerts = alerts

        # Per-market whale state
        self._states: dict[str, MarketWhaleState] = {}

        # Latest ask prices from ticker channel
        self._asks: dict[str, Decimal] = {}  # ticker -> yes_ask

        self._running = False
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._current_tickers: set[str] = set()

    async def run(self) -> None:
        """Main WS loop with reconnection. Runs until stopped."""
        self._running = True
        backoff = 1.0

        while self._running:
            try:
                auth_headers = self._client.sign_ws()

                async with websockets.connect(
                    KALSHI_WS_URL,
                    additional_headers=auth_headers,
                    ping_interval=20,
                    close_timeout=10,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    logger.info("Whale WS: connected")
                    backoff = 1.0

                    # Subscribe to current watchlist
                    await self._subscribe(ws, set(self._watchlist.tickers))

                    while self._running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            await ws.ping()
                            # Check for new watchlist tickers to subscribe
                            await self._check_new_subscriptions(ws)
                            continue

                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        await self._handle_message(msg)

            except websockets.ConnectionClosed as e:
                logger.warning("Whale WS: disconnected (%s)", e)
            except Exception as e:
                logger.error("Whale WS: error: %s", e)

            if self._running:
                logger.info("Whale WS: reconnecting in %.0fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _subscribe(
        self,
        ws: websockets.WebSocketClientProtocol,
        tickers: set[str],
    ) -> None:
        """Subscribe to trade + ticker channels for given tickers."""
        new_tickers = tickers - self._current_tickers
        if not new_tickers:
            return

        ticker_list = list(new_tickers)
        batch_size = 500
        sub_id = 1
        for i in range(0, len(ticker_list), batch_size):
            batch = ticker_list[i : i + batch_size]
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

        self._current_tickers |= new_tickers
        logger.info("Whale WS: subscribed to %d new tickers (%d total)",
                     len(new_tickers), len(self._current_tickers))

    async def _check_new_subscriptions(
        self,
        ws: websockets.WebSocketClientProtocol,
    ) -> None:
        """Subscribe to any watchlist tickers we're not yet subscribed to."""
        current_watchlist = set(self._watchlist.tickers)
        await self._subscribe(ws, current_watchlist)

    async def _handle_message(self, msg: dict) -> None:
        msg_type = msg.get("type", "")
        payload = msg.get("msg", {})

        if msg_type == "trade":
            await self._handle_trade(payload)
        elif msg_type == "ticker":
            self._handle_ticker(payload)

    async def _handle_trade(self, payload: dict) -> None:
        """Process a trade message — filter for whales, aggregate, score."""
        ticker = payload.get("market_ticker", "")
        trade_id = payload.get("trade_id", "")
        if not ticker or not trade_id:
            return

        # Only process trades for watchlist markets
        if ticker not in self._watchlist.markets:
            return

        taker_side = (payload.get("taker_side") or "yes").lower()

        # Price: use the taker's side price
        if taker_side == "no":
            price_raw = payload.get("no_price_dollars") or payload.get("no_price")
        else:
            price_raw = payload.get("yes_price_dollars") or payload.get("yes_price")

        size_raw = payload.get("count_fp") or payload.get("count")

        if price_raw is None or size_raw is None:
            return

        try:
            price = Decimal(str(price_raw))
            size = Decimal(str(size_raw))
        except Exception:
            return

        notional = price * size

        # Filter: only whale trades
        if notional < self._config.whale_threshold:
            return

        # Parse timestamp
        ts = payload.get("ts")
        if ts:
            traded_at = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        else:
            traded_at = datetime.now(timezone.utc)

        trade = WhaleTrade(
            trade_id=trade_id,
            market_ticker=ticker,
            price=price,
            size=size,
            notional=notional,
            taker_side=taker_side,
            timestamp=traded_at,
        )

        # Aggregate
        if ticker not in self._states:
            self._states[ticker] = MarketWhaleState(market_ticker=ticker)
        state = self._states[ticker]
        state.add_trade(trade)

        logger.info(
            "Whale trade: %s %s $%.0f (%s x%.0f @ %.2f) — "
            "count=%d consensus=%.0f%% %s",
            ticker, taker_side, notional, taker_side, size, price,
            state.whale_count, state.consensus_pct * 100,
            state.consensus_side,
        )

        # Track every whale trade
        self._tracker.log_whale_trade(
            market_ticker=ticker,
            trade_id=trade_id,
            taker_side=taker_side,
            price=price,
            size=size,
            notional=notional,
            whale_count=state.whale_count,
            consensus_pct=state.consensus_pct,
            consensus_side=state.consensus_side,
        )

        # Score and maybe emit signal
        await self._maybe_emit_signal(state)

    def _handle_ticker(self, payload: dict) -> None:
        """Update ask prices from ticker channel."""
        ticker = payload.get("market_ticker", "")
        if not ticker:
            return

        yes_ask = dec(payload.get("yes_ask_dollars"))
        if yes_ask is not None:
            self._asks[ticker] = yes_ask

        # Also push to price_queue for the monitor
        yes_price = dec(payload.get("price_dollars"))
        no_price = None
        if yes_price is not None:
            no_price = Decimal("1") - yes_price

        update = PriceUpdate(
            market_id=ticker,
            yes_price=yes_price,
            no_price=no_price,
            yes_bid=dec(payload.get("yes_bid_dollars")),
            yes_ask=yes_ask,
            no_bid=dec(payload.get("no_bid_dollars")),
            no_ask=dec(payload.get("no_ask_dollars")),
        )

        try:
            self._price_queue.put_nowait(update)
        except asyncio.QueueFull:
            pass

    async def _maybe_emit_signal(self, state: MarketWhaleState) -> None:
        """Check if market meets all criteria, emit WhaleSignal if so."""
        if state.signal_emitted:
            return

        cfg = self._config

        # Only count recent whale trades (within window)
        recent = state.recent_trades(cfg.whale_window_min)
        if len(recent) < cfg.min_whale_count:
            return

        # Compute consensus on recent trades only
        yes_vol = sum(t.notional for t in recent if t.taker_side == "yes")
        no_vol = sum(t.notional for t in recent if t.taker_side == "no")
        total_vol = yes_vol + no_vol
        if total_vol == 0:
            return

        if yes_vol >= no_vol:
            side: str | None = "yes"
            consensus = float(yes_vol / total_vol)
        else:
            side = "no"
            consensus = float(no_vol / total_vol)

        if consensus < cfg.consensus_pct:
            # Not a signal — just whale activity without consensus.
            # Log to CSV only, no Telegram, no lock (consensus could shift).
            self._tracker.log_signal_skip(
                market_ticker=state.market_ticker,
                reason=f"consensus {consensus:.0%} < {cfg.consensus_pct:.0%}",
                whale_count=len(recent),
                consensus_pct=consensus,
                consensus_side=side or "",
            )
            return

        # Must have a current ask price.
        # Don't lock — ask will likely arrive on next ticker update.
        ask = self._asks.get(state.market_ticker)
        if ask is None:
            return

        # For YES consensus, check yes_ask. For NO, check no_ask (= 1 - yes_ask).
        if side == "yes":
            entry_price = ask
        else:
            entry_price = Decimal("1") - ask

        # Price must be in range.
        # Don't lock — price could move into range on next tick.
        # Log to CSV only, no Telegram.
        if entry_price < cfg.price_min or entry_price > cfg.price_max:
            self._tracker.log_signal_skip(
                market_ticker=state.market_ticker,
                reason=f"price {entry_price} outside [{cfg.price_min}-{cfg.price_max}]",
                whale_count=len(recent),
                consensus_pct=consensus,
                consensus_side=side or "",
            )
            return

        # Market must still be on watchlist
        market = self._watchlist.get(state.market_ticker)
        if market is None:
            return

        state.signal_emitted = True

        signal = WhaleSignal(
            market_ticker=state.market_ticker,
            side=side,
            whale_count=len(recent),
            consensus_pct=consensus,
            total_volume=total_vol,
            best_ask=entry_price,
            confidence=consensus,
        )

        self._tracker.log_signal_pass(
            market_ticker=signal.market_ticker,
            side=signal.side,
            whale_count=signal.whale_count,
            consensus_pct=signal.consensus_pct,
            total_volume=signal.total_volume,
            best_ask=signal.best_ask,
        )

        logger.info(
            "WHALE SIGNAL: %s %s — %d whales, %.0f%% consensus, "
            "ask=%.2f, vol=$%.0f",
            signal.market_ticker, signal.side, signal.whale_count,
            signal.consensus_pct * 100, signal.best_ask, signal.total_volume,
        )

        await self._signal_queue.put(signal)

    def clear_market(self, ticker: str) -> None:
        """Clear state for a market (after it closes/resolves)."""
        self._states.pop(ticker, None)
        self._asks.pop(ticker, None)
        self._current_tickers.discard(ticker)
