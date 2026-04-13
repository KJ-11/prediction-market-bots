# Position monitoring. Stop-loss + settlement detection.

"""Position monitor — tracks open positions with stop-loss and settlement.

Checks WS price updates against stop-loss threshold (15% below entry).
Detects market settlement via Kalshi market API polling (every 15s).
Falls back to REST for price checks if WS updates go stale (>60s).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal

from bots.kalshi_whale.sizing import kalshi_fee as _kalshi_fee
from bots.kalshi_whale.strategy import WhaleConfig
from bots.kalshi_whale.tracking import WhaleTracker
from shared.alerts.manager import AlertManager
from shared.clients.kalshi import KalshiClient
from shared.execution.base import AbstractExecutionEngine
from shared.execution.paper import PaperExecutionEngine
from shared.types import OrderRequest, Outcome, PriceUpdate, Side

logger = logging.getLogger(__name__)

# Don't sell below this price — slippage too extreme, hold to settlement.
HARD_FLOOR = Decimal("0.05")

# If no WS price update for a position in this many seconds, fetch via REST.
STALE_PRICE_SECS = 60.0


@dataclass
class TrackedPosition:
    """An open position being monitored."""

    market_ticker: str
    side: str  # "yes" or "no" — the consensus side we entered
    entry_price: Decimal
    size: int
    order_id: str = ""

    @property
    def outcome(self) -> Outcome:
        return Outcome.YES if self.side == "yes" else Outcome.NO


class PositionMonitor:
    """Monitors open positions with stop-loss and settlement detection.

    Checks WS price updates for stop-loss triggers. Polls Kalshi's market
    API to detect resolution. Falls back to REST if WS price data goes stale.
    """

    def __init__(
        self,
        config: WhaleConfig,
        engine: AbstractExecutionEngine,
        client: KalshiClient,
        alerts: AlertManager,
        tracker: WhaleTracker,
        price_queue: asyncio.Queue[PriceUpdate],
        on_stop_loss: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._engine = engine
        self._client = client
        self._alerts = alerts
        self._tracker = tracker
        self._price_queue = price_queue
        self._on_stop_loss = on_stop_loss

        # Active positions: ticker -> TrackedPosition
        self._positions: dict[str, TrackedPosition] = {}

        # Protects _positions against concurrent mutation by settlement
        # poller and stop-loss execution.
        self._lock = asyncio.Lock()

        # Last WS price update time per ticker (for REST fallback).
        self._last_price_time: dict[str, float] = {}

        # Settlement results to report back to main loop
        self._results: asyncio.Queue[tuple[str, Decimal]] = asyncio.Queue()

    @property
    def open_count(self) -> int:
        return len(self._positions)

    @property
    def results(self) -> asyncio.Queue[tuple[str, Decimal]]:
        """Queue of (ticker, pnl) for settled/stopped positions."""
        return self._results

    def _stop_threshold(self, pos: TrackedPosition) -> Decimal:
        """Price at which stop-loss triggers (bid must drop below this)."""
        return pos.entry_price * (
            Decimal("1") - Decimal(str(self._config.stop_loss_pct))
        )

    def add_position(self, pos: TrackedPosition) -> None:
        """Start monitoring a new position."""
        self._positions[pos.market_ticker] = pos
        self._last_price_time[pos.market_ticker] = time.monotonic()
        stop = self._stop_threshold(pos)
        logger.info(
            "Monitor: tracking %s %s entry=%.2f size=%d (stop at $%.2f)",
            pos.market_ticker, pos.side, pos.entry_price, pos.size, stop,
        )

    async def run_price_monitor(self) -> None:
        """Check WS price updates against stop-loss thresholds."""
        while True:
            try:
                update = await asyncio.wait_for(
                    self._price_queue.get(), timeout=5.0,
                )
            except asyncio.TimeoutError:
                await self._check_stale_prices()
                continue

            ticker = update.market_id
            if ticker not in self._positions:
                continue

            pos = self._positions[ticker]

            # Use the bid for our side — that's the price we'd get selling.
            if pos.side == "yes":
                bid = update.yes_bid
            else:
                bid = update.no_bid

            if bid is None:
                continue

            self._last_price_time[ticker] = time.monotonic()

            if bid < self._stop_threshold(pos):
                await self._execute_stop_loss(pos, trigger_bid=bid)

    async def _check_stale_prices(self) -> None:
        """REST fallback: fetch price for positions with stale WS data."""
        now = time.monotonic()
        for ticker, pos in list(self._positions.items()):
            last = self._last_price_time.get(ticker, 0.0)
            if now - last < STALE_PRICE_SECS:
                continue

            try:
                market = await self._client.fetch_market(ticker)
            except Exception as e:
                logger.warning("Stop-loss REST fallback %s error: %s", ticker, e)
                continue

            if market is None:
                continue

            # Update staleness timer even on REST fetch.
            self._last_price_time[ticker] = now

            # Extract bid for our side.
            if pos.side == "yes":
                bid_raw = market.get("yes_bid_dollars") or market.get("yes_bid")
            else:
                bid_raw = market.get("no_bid_dollars") or market.get("no_bid")

            if bid_raw is None:
                continue

            try:
                bid = Decimal(str(bid_raw))
            except Exception:
                continue

            if bid < self._stop_threshold(pos):
                logger.info(
                    "Stop-loss triggered via REST fallback: %s bid=%.2f",
                    ticker, bid,
                )
                await self._execute_stop_loss(pos, trigger_bid=bid)

    async def _execute_stop_loss(
        self, pos: TrackedPosition, trigger_bid: Decimal,
    ) -> None:
        """Sell position at market bid. Handles partial fills and races."""
        ticker = pos.market_ticker

        async with self._lock:
            # Re-check: may have been settled or stopped already.
            if ticker not in self._positions:
                return

            if trigger_bid <= HARD_FLOOR:
                logger.warning(
                    "Stop-loss skipped (hard floor): %s bid=$%.2f <= $%.2f",
                    ticker, trigger_bid, HARD_FLOOR,
                )
                return

            stop = self._stop_threshold(pos)
            logger.info(
                "STOP LOSS: %s %s entry=%.2f stop=%.4f trigger=%.2f size=%d",
                ticker, pos.side, pos.entry_price, stop, trigger_bid, pos.size,
            )

            # Sell IOC at trigger bid (engine applies cushion).
            order = OrderRequest(
                market_id=ticker,
                side=Side.SELL,
                outcome=pos.outcome,
                price=trigger_bid,
                size=Decimal(str(pos.size)),
            )

            try:
                resp = await self._engine.place_order(order)
            except Exception as e:
                logger.error("Stop-loss order failed: %s: %s", ticker, e)
                return

            filled_size = int(resp.filled_size)
            if filled_size <= 0:
                logger.warning(
                    "Stop-loss no fills: %s — will retry next tick", ticker,
                )
                return

            fill_price = resp.avg_fill_price or trigger_bid

            # P&L: proceeds - cost - fees
            entry_fee = _kalshi_fee(pos.entry_price, pos.size)
            exit_fee = _kalshi_fee(fill_price, filled_size)
            pnl = (
                (fill_price - pos.entry_price) * filled_size
                - entry_fee - exit_fee
            )

            fully_exited = filled_size >= pos.size
            if not fully_exited:
                # Partial fill — reduce size, keep monitoring remainder.
                pos.size -= filled_size
                logger.info(
                    "Stop-loss partial fill: %s sold %d, %d remaining",
                    ticker, filled_size, pos.size,
                )
            else:
                # Full exit.
                self._positions.pop(ticker)
                self._last_price_time.pop(ticker, None)

        # Outside lock: logging, alerts, results.
        # Settle paper engine positions so balance stays in sync.
        if fully_exited and isinstance(self._engine, PaperExecutionEngine):
            # Paper engine already handled the sell in place_order above.
            pass

        # Delay for Kalshi to process the sell.
        if not isinstance(self._engine, PaperExecutionEngine):
            await asyncio.sleep(2)

        balance = await self._engine.get_balance()
        live_positions = await self._engine.get_positions()
        live_open_cost = sum(
            (p.size * p.avg_entry_price for p in live_positions),
            Decimal("0"),
        )
        equity = balance + live_open_cost

        self._tracker.log_stop_loss(
            market_ticker=ticker,
            side=pos.side,
            outcome=pos.side,
            entry_price=pos.entry_price,
            stop_price=stop,
            trigger_price=trigger_bid,
            actual_price=fill_price,
            contracts=filled_size,
            order_id=resp.order_id,
            order_status=resp.status.value,
            pnl=pnl,
            balance_after=balance,
        )

        await self._alerts.whale_stop_loss(
            ticker=ticker,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=fill_price,
            stop_threshold=stop,
            trigger_bid=trigger_bid,
            size=filled_size,
            pnl=pnl,
            entry_fee=entry_fee,
            exit_fee=exit_fee,
            balance=balance,
            equity=equity,
        )

        logger.info(
            "STOPPED (%s): %s %s pnl=$%+.2f bal=$%.2f",
            "PARTIAL" if not fully_exited else "FULL",
            ticker, pos.side, pnl, balance,
        )

        await self._results.put((ticker, pnl))

        # Cleanup callback (only on full exit).
        if fully_exited and self._on_stop_loss:
            await self._on_stop_loss(ticker)

    async def run_settlement_poller(self) -> None:
        """Periodically check if positions have been settled by Kalshi.

        For each tracked position, fetches the market via REST API and checks
        the `result` field. Only settles when we have a confirmed result
        ("yes" or "no"), avoiding false settlements from API glitches.
        """
        while True:
            await asyncio.sleep(15.0)

            if not self._positions:
                continue

            # Check each tracked position's market result directly.
            # Don't rely on positions API (empty response = false settlement).
            settled: list[tuple[str, str]] = []  # (ticker, result)
            for ticker, pos in list(self._positions.items()):
                try:
                    market = await self._client.fetch_market(ticker)
                except Exception as e:
                    logger.warning("Monitor: fetch_market %s error: %s", ticker, e)
                    continue

                if market is None:
                    continue

                result = (market.get("result") or "").lower()
                if result in ("yes", "no"):
                    settled.append((ticker, result))

            for ticker, result in settled:
                async with self._lock:
                    if ticker not in self._positions:
                        continue  # Already stop-lossed.
                    pos = self._positions.pop(ticker)

                won = pos.side == result

                # Fee-accurate P&L
                entry_fee = _kalshi_fee(pos.entry_price, pos.size)
                if won:
                    payout = Decimal(str(pos.size))  # $1 per contract
                    profit_per = Decimal("1") - pos.entry_price
                    exit_fee = _kalshi_fee(profit_per, pos.size)
                    pnl = payout - (pos.entry_price * pos.size) - entry_fee - exit_fee
                else:
                    exit_fee = Decimal("0")
                    pnl = -(pos.entry_price * pos.size) - entry_fee

                logger.info(
                    "SETTLED (%s): %s %s pnl=$%+.2f",
                    "WIN" if won else "LOSS", ticker, pos.side, pnl,
                )

                # Settle paper engine positions so balance + positions stay in sync
                if isinstance(self._engine, PaperExecutionEngine):
                    winning = Outcome.YES if result == "yes" else Outcome.NO
                    await self._engine.settle_market(ticker, winning)

                # Delay for Kalshi to credit settlement payout
                if not isinstance(self._engine, PaperExecutionEngine):
                    await asyncio.sleep(5)
                balance = await self._engine.get_balance()
                live_positions = await self._engine.get_positions()
                live_open_cost = sum(
                    (p.size * p.avg_entry_price for p in live_positions),
                    Decimal("0"),
                )
                equity = balance + live_open_cost

                self._tracker.log_settlement(
                    market_ticker=ticker,
                    side=pos.side,
                    outcome=result,
                    entry_price=pos.entry_price,
                    contracts=pos.size,
                    pnl=pnl,
                    balance_after=balance,
                )

                await self._alerts.whale_settled(
                    ticker=ticker,
                    side=pos.side,
                    outcome=result,
                    won=won,
                    entry_price=pos.entry_price,
                    size=pos.size,
                    pnl=pnl,
                    entry_fee=entry_fee,
                    exit_fee=exit_fee,
                    balance=balance,
                    equity=equity,
                )

                self._last_price_time.pop(ticker, None)
                await self._results.put((ticker, pnl))

    def remove_position(self, ticker: str) -> TrackedPosition | None:
        """Remove a position from tracking (e.g. manual exit)."""
        self._last_price_time.pop(ticker, None)
        return self._positions.pop(ticker, None)
