# Position monitoring. Settlement detection via REST polling.

"""Position monitor — tracks open positions until settlement.

Drains the price_queue to prevent backpressure. Detects market settlement
via Kalshi market API polling (every 15s).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal

from bots.kalshi_whale.sizing import kalshi_fee as _kalshi_fee
from bots.kalshi_whale.strategy import WhaleConfig
from bots.kalshi_whale.tracking import WhaleTracker
from shared.alerts.manager import AlertManager
from shared.clients.kalshi import KalshiClient
from shared.execution.base import AbstractExecutionEngine
from shared.execution.paper import PaperExecutionEngine
from shared.types import Outcome, PriceUpdate

logger = logging.getLogger(__name__)


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
    """Monitors open positions until settlement.

    Drains the price_queue (prevents backpressure) and polls Kalshi's
    market API to detect resolution. No stop loss — hold to settlement.
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
    def results(self) -> asyncio.Queue[tuple[str, Decimal]]:
        """Queue of (ticker, pnl) for settled positions."""
        return self._results

    def add_position(self, pos: TrackedPosition) -> None:
        """Start monitoring a new position."""
        self._positions[pos.market_ticker] = pos
        logger.info(
            "Monitor: tracking %s %s entry=%.2f size=%d (hold to settlement)",
            pos.market_ticker, pos.side, pos.entry_price, pos.size,
        )

    async def run_price_monitor(self) -> None:
        """Consume price updates (drains queue to prevent backpressure)."""
        while True:
            try:
                await asyncio.wait_for(
                    self._price_queue.get(), timeout=5.0,
                )
            except asyncio.TimeoutError:
                continue

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
                # Source equity from real Kalshi positions, not the in-memory
                # monitor cache — see _fetch_balance_and_equity in main.py for
                # the rationale (positions opened pre-restart aren't tracked).
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

                await self._results.put((ticker, pnl))

    def remove_position(self, ticker: str) -> TrackedPosition | None:
        """Remove a position from tracking (e.g. manual exit)."""
        return self._positions.pop(ticker, None)
