"""Paper trading execution engine — simulates fills at market price."""

from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal
from pathlib import Path

from shared.execution.base import AbstractExecutionEngine
from shared.types import (
    OrderRequest,
    OrderResponse,
    OrderStatus,
    Outcome,
    Position,
    Side,
)

logger = logging.getLogger(__name__)

DEFAULT_BALANCE_FILE = Path("data/paper_balance.json")


class PaperExecutionEngine(AbstractExecutionEngine):
    """Simulates order execution for paper trading.

    Fills immediately at the order price with configurable slippage.
    Tracks positions and balance in memory. Persists balance to disk
    so it survives restarts.
    """

    def __init__(
        self,
        initial_balance: Decimal = Decimal("50"),
        slippage_bps: int = 10,
        balance_file: Path | None = DEFAULT_BALANCE_FILE,
    ) -> None:
        self._balance_file = balance_file
        self._slippage_bps = slippage_bps
        self._orders: dict[str, OrderResponse] = {}
        self._positions: dict[str, Position] = {}  # key: f"{market_id}:{outcome}"

        loaded = self._load_balance()
        if loaded is not None:
            self._balance = loaded
            logger.info("Paper: loaded persisted balance $%.2f", self._balance)
        else:
            self._balance = initial_balance
            logger.info("Paper: starting with initial balance $%.2f", self._balance)

    def _load_balance(self) -> Decimal | None:
        if self._balance_file is None:
            return None
        try:
            data = json.loads(self._balance_file.read_text())
            return Decimal(data["balance"])
        except (FileNotFoundError, KeyError, json.JSONDecodeError, Exception):
            return None

    def _save_balance(self) -> None:
        if self._balance_file is None:
            return
        try:
            self._balance_file.parent.mkdir(parents=True, exist_ok=True)
            self._balance_file.write_text(
                json.dumps({"balance": str(self._balance)}) + "\n"
            )
        except Exception:
            logger.warning("Paper: failed to persist balance", exc_info=True)

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        order_id = f"paper-{uuid.uuid4().hex[:12]}"

        slippage = order.price * Decimal(self._slippage_bps) / Decimal("10000")
        if order.side == Side.BUY:
            fill_price = order.price + slippage
        else:
            fill_price = order.price - slippage

        cost = fill_price * order.size

        if order.side == Side.BUY and cost > self._balance:
            logger.warning(
                "Paper: insufficient balance (need %.2f, have %.2f)",
                cost, self._balance,
            )
            return OrderResponse(
                order_id=order_id,
                market_id=order.market_id,
                status=OrderStatus.FAILED,
                side=order.side,
                outcome=order.outcome,
                price=order.price,
                size=order.size,
                raw={"error": "insufficient_balance"},
            )

        if order.side == Side.BUY:
            self._balance -= cost
        else:
            self._balance += cost

        pos_key = f"{order.market_id}:{order.outcome.value}"
        existing = self._positions.get(pos_key)
        if order.side == Side.BUY:
            if existing:
                total_size = existing.size + order.size
                avg_price = (
                    (existing.avg_entry_price * existing.size + fill_price * order.size)
                    / total_size
                )
                self._positions[pos_key] = Position(
                    market_id=order.market_id,
                    outcome=order.outcome,
                    size=total_size,
                    avg_entry_price=avg_price,
                )
            else:
                self._positions[pos_key] = Position(
                    market_id=order.market_id,
                    outcome=order.outcome,
                    size=order.size,
                    avg_entry_price=fill_price,
                )
        else:
            if existing and existing.size >= order.size:
                new_size = existing.size - order.size
                if new_size > 0:
                    self._positions[pos_key] = Position(
                        market_id=order.market_id,
                        outcome=order.outcome,
                        size=new_size,
                        avg_entry_price=existing.avg_entry_price,
                        realized_pnl=existing.realized_pnl
                        + (fill_price - existing.avg_entry_price) * order.size,
                    )
                else:
                    del self._positions[pos_key]

        response = OrderResponse(
            order_id=order_id,
            market_id=order.market_id,
            status=OrderStatus.FILLED,
            side=order.side,
            outcome=order.outcome,
            price=order.price,
            size=order.size,
            filled_size=order.size,
            avg_fill_price=fill_price,
        )

        self._orders[order_id] = response

        self._save_balance()

        logger.info(
            "Paper: %s %s @ %.4f x%.1f (fill=%.4f, bal=%.2f)",
            order.side.value, order.outcome.value,
            order.price, order.size, fill_price, self._balance,
        )

        return response

    async def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            del self._orders[order_id]
            return True
        return False

    async def cancel_all(self, market_id: str | None = None) -> int:
        if market_id:
            to_remove = [
                oid for oid, o in self._orders.items() if o.market_id == market_id
            ]
        else:
            to_remove = list(self._orders.keys())
        for oid in to_remove:
            del self._orders[oid]
        return len(to_remove)

    async def get_open_orders(self, market_id: str | None = None) -> list[OrderResponse]:
        orders = list(self._orders.values())
        if market_id:
            orders = [o for o in orders if o.market_id == market_id]
        return [o for o in orders if o.status == OrderStatus.OPEN]

    async def get_positions(self, market_id: str | None = None) -> list[Position]:
        positions = list(self._positions.values())
        if market_id:
            positions = [p for p in positions if p.market_id == market_id]
        return positions

    async def get_balance(self) -> Decimal:
        return self._balance

    async def settle_market(
        self,
        market_id: str,
        winning_outcome: Outcome,
    ) -> Decimal:
        """Settle all positions in a market. Winners pay $1, losers $0.

        Returns net P&L from settlement.
        """
        pnl = Decimal("0")
        keys_to_remove = []

        for pos_key, pos in self._positions.items():
            if pos.market_id != market_id:
                continue

            if pos.outcome == winning_outcome:
                # Winner: receive $1 per contract
                payout = pos.size * Decimal("1")
                profit = payout - (pos.size * pos.avg_entry_price)
                self._balance += payout
                pnl += profit
            else:
                # Loser: position expires worthless, cost already deducted
                loss = pos.size * pos.avg_entry_price
                pnl -= loss

            keys_to_remove.append(pos_key)

        for key in keys_to_remove:
            del self._positions[key]

        if keys_to_remove:
            self._save_balance()
            logger.info(
                "Paper: settled %s outcome=%s pnl=%+.2f bal=%.2f",
                market_id, winning_outcome.value, pnl, self._balance,
            )

        return pnl
