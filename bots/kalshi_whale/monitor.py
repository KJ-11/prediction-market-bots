# Position monitoring. Stop loss via WS price updates, resolution detection.

"""Position monitor — tracks open positions for stop loss and resolution.

Watches price updates from the WhaleDetector's price_queue. Triggers stop
loss exits when price drops below threshold. Detects market settlement
via Kalshi positions API polling.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from bots.kalshi_whale.sizing import kalshi_fee
from bots.kalshi_whale.strategy import WhaleConfig
from bots.kalshi_whale.tracking import WhaleTracker
from shared.alerts.manager import AlertManager
from shared.clients.kalshi import KalshiClient
from shared.execution.base import AbstractExecutionEngine
from shared.execution.paper import PaperExecutionEngine
from shared.types import OrderRequest, OrderStatus, Outcome, PriceUpdate, Side

logger = logging.getLogger(__name__)


@dataclass
class TrackedPosition:
    """An open position being monitored."""

    market_ticker: str
    side: str  # "yes" or "no" — the consensus side we entered
    entry_price: Decimal
    size: int
    stop_price: Decimal  # Exit if price drops below this
    order_id: str = ""
    stop_triggered: bool = False

    @property
    def outcome(self) -> Outcome:
        return Outcome.YES if self.side == "yes" else Outcome.NO


class PositionMonitor:
    """Monitors open positions for stop loss and resolution.

    Two responsibilities:
    1. Watch price_queue for stop loss triggers on open positions
    2. Periodically poll Kalshi positions API to detect settlement
    """

    def __init__(
        self,
        config: WhaleConfig,
        engine: AbstractExecutionEngine,
        client: KalshiClient,
        alerts: AlertManager,
        tracker: WhaleTracker,
        price_queue: asyncio.Queue[PriceUpdate],
    ) -> None:
        self._config = config
        self._engine = engine
        self._client = client
        self._alerts = alerts
        self._tracker = tracker
        self._price_queue = price_queue

        # Active positions: ticker -> TrackedPosition
        self._positions: dict[str, TrackedPosition] = {}

        # Settlement results to report back to main loop
        self._results: asyncio.Queue[tuple[str, Decimal]] = asyncio.Queue()

    @property
    def open_count(self) -> int:
        return len(self._positions)

    @property
    def open_cost(self) -> Decimal:
        """Total entry cost locked in open positions (for equity calculation)."""
        return sum(
            p.entry_price * p.size
            for p in self._positions.values()
            if not p.stop_triggered
        )

    @property
    def results(self) -> asyncio.Queue[tuple[str, Decimal]]:
        """Queue of (ticker, pnl) for settled/stopped positions."""
        return self._results

    def add_position(self, pos: TrackedPosition) -> None:
        """Start monitoring a new position."""
        self._positions[pos.market_ticker] = pos
        logger.info(
            "Monitor: tracking %s %s entry=%.2f size=%d stop=%.2f",
            pos.market_ticker, pos.side, pos.entry_price, pos.size, pos.stop_price,
        )

    async def run_price_monitor(self) -> None:
        """Consume price updates and check stop losses."""
        while True:
            try:
                update = await asyncio.wait_for(
                    self._price_queue.get(), timeout=5.0,
                )
            except asyncio.TimeoutError:
                continue

            ticker = update.market_id
            pos = self._positions.get(ticker)
            if pos is None or pos.stop_triggered:
                continue

            # Get current price for the side we're holding
            if pos.side == "yes":
                current_price = update.yes_price or update.yes_bid
            else:
                current_price = update.no_price or update.no_bid

            if current_price is None:
                continue

            # Check stop loss
            if current_price <= pos.stop_price:
                logger.warning(
                    "STOP LOSS: %s price=%.2f <= stop=%.2f (entry=%.2f)",
                    ticker, current_price, pos.stop_price, pos.entry_price,
                )
                pos.stop_triggered = True
                await self._execute_stop_loss(pos, current_price)

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
                if pos.stop_triggered:
                    continue
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
                pos = self._positions.pop(ticker)
                won = pos.side == result

                if won:
                    payout = Decimal(str(pos.size))  # $1 per contract
                    cost = pos.entry_price * pos.size
                    pnl = payout - cost
                else:
                    # Lost: cost was already deducted, no payout
                    pnl = -(pos.entry_price * pos.size)

                logger.info(
                    "SETTLED (%s): %s %s pnl=$%+.2f",
                    "WIN" if won else "LOSS", ticker, pos.side, pnl,
                )

                # Settle paper engine positions so balance + positions stay in sync
                if isinstance(self._engine, PaperExecutionEngine):
                    winning = Outcome.YES if result == "yes" else Outcome.NO
                    await self._engine.settle_market(ticker, winning)

                balance = await self._engine.get_balance()

                self._tracker.log_settlement(
                    market_ticker=ticker,
                    side=pos.side,
                    outcome=result,
                    entry_price=pos.entry_price,
                    contracts=pos.size,
                    pnl=pnl,
                    balance_after=balance,
                )

                entry_cost = pos.entry_price * pos.size
                icon = "\u2705 WIN" if won else "\u274c LOSS"
                await self._alerts._send(
                    "SETTLED",
                    "\u2696\ufe0f",
                    (
                        f"<b>{ticker}</b>: {icon}\n"
                        f"Entry: ${pos.entry_price} x{pos.size} "
                        f"(cost ${entry_cost:.2f})\n"
                        f"P&L: <b>${pnl:+.2f}</b> | "
                        f"Balance: <b>${balance:.2f}</b>"
                    ),
                )

                await self._results.put((ticker, pnl))

    async def _execute_stop_loss(
        self, pos: TrackedPosition, current_price: Decimal,
    ) -> None:
        """Sell position at market to exit."""
        order = OrderRequest(
            market_id=pos.market_ticker,
            side=Side.SELL,
            outcome=pos.outcome,
            price=current_price,
            size=Decimal(str(pos.size)),
        )

        try:
            resp = await self._engine.place_order(order)
        except Exception as e:
            logger.error("Stop loss order failed: %s: %s", pos.market_ticker, e)
            # Remove from tracking even on failure — don't retry stop losses
            self._positions.pop(pos.market_ticker, None)
            return

        filled = resp.status == OrderStatus.FILLED
        fill_price = resp.avg_fill_price or current_price
        exit_fee = kalshi_fee(fill_price, pos.size)
        pnl = (fill_price - pos.entry_price) * pos.size - exit_fee

        balance = await self._engine.get_balance()

        self._tracker.log_stop_loss(
            market_ticker=pos.market_ticker,
            side=pos.side,
            outcome=pos.side,
            entry_price=pos.entry_price,
            stop_price=pos.stop_price,
            trigger_price=current_price,
            actual_price=fill_price,
            contracts=pos.size,
            order_id=resp.order_id,
            order_status=resp.status.value,
            pnl=pnl,
            balance_after=balance,
        )

        logger.info(
            "Stop loss %s: %s fill=%.2f pnl=$%.2f bal=$%.2f",
            "FILLED" if filled else "FAILED",
            pos.market_ticker, fill_price, pnl, balance,
        )

        entry_cost = pos.entry_price * pos.size
        await self._alerts._send(
            "STOP LOSS",
            "\U0001f6d1",
            (
                f"<b>{pos.market_ticker}</b>\n"
                f"Entry: ${pos.entry_price} x{pos.size} | "
                f"Exit: ${fill_price} x{pos.size}\n"
                f"P&L: <b>${pnl:+.2f}</b> | "
                f"Balance: <b>${balance:.2f}</b>"
            ),
        )

        self._positions.pop(pos.market_ticker, None)
        await self._results.put((pos.market_ticker, pnl))

    def remove_position(self, ticker: str) -> TrackedPosition | None:
        """Remove a position from tracking (e.g. manual exit)."""
        return self._positions.pop(ticker, None)
