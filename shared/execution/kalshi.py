"""Kalshi execution engine.

Maps OrderRequest to Kalshi's POST /portfolio/orders API.
Kalshi order fields: ticker, side (yes/no), action (buy/sell), count (int),
yes_price (1-99 cents). No "type" field — market orders removed Feb 2026.
Uses time_in_force="immediate_or_cancel" (IOC) so orders fill instantly
or cancel — no resting orders.

Kalshi response fields (cents): yes_price, no_price, fill_count,
taker_fill_cost, taker_fees. Status: resting, canceled, executed.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import httpx

from shared.clients.kalshi import KalshiClient
from shared.execution.base import AbstractExecutionEngine
from shared.types import (
    OrderRequest,
    OrderResponse,
    OrderStatus,
    Outcome,
    Position,
    Side,
)
from shared.utils.decimals import dec

logger = logging.getLogger(__name__)


def _map_kalshi_status(status: str) -> OrderStatus:
    mapping = {
        "resting": OrderStatus.OPEN,
        "canceled": OrderStatus.CANCELLED,
        "executed": OrderStatus.FILLED,
    }
    return mapping.get(status, OrderStatus.PENDING)


def _get_fill_count(result: dict) -> int:
    """Extract fill count from Kalshi response.

    Kalshi API returns fill_count_fp (current) or fill_count (legacy).
    """
    count = result.get("fill_count_fp") or result.get("fill_count") or 0
    return int(count)


def _compute_fill_price(result: dict, outcome: str) -> Decimal | None:
    """Compute average fill price from Kalshi response fields.

    Kalshi API returns dollar-denominated fields (current) or cent fields
    (legacy). Handles both:
        taker_fill_cost_dollars / fill_count_fp  (dollars, no conversion)
        taker_fill_cost / fill_count / 100       (cents, convert to dollars)
    """
    fill_count = _get_fill_count(result)
    if not fill_count:
        return None

    # Prefer dollar-denominated fields (current API)
    cost_dollars = result.get("taker_fill_cost_dollars")
    if cost_dollars is not None:
        cost_dec = Decimal(str(cost_dollars))
        if cost_dec == 0:
            return None
        yes_fill = cost_dec / Decimal(str(fill_count))
    else:
        # Legacy cent-denominated fields
        taker_fill_cost = result.get("taker_fill_cost", 0)
        if not taker_fill_cost:
            return None
        yes_fill = (
            Decimal(str(taker_fill_cost))
            / Decimal(str(fill_count))
            / Decimal("100")
        )

    if outcome == "no":
        return Decimal("1") - yes_fill
    return yes_fill


class KalshiExecutionEngine(AbstractExecutionEngine):
    def __init__(
        self,
        client: KalshiClient,
        price_cushion_cents: int = 2,
    ) -> None:
        self._client = client
        self._price_cushion_cents = price_cushion_cents

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place a Kalshi IOC order with price cushion.

        Adds price_cushion_cents above ask (for buys) to absorb price
        movement during network round trip. IOC fills at best available
        price, so the cushion only costs extra if the book is thin.
        """
        kalshi_order = {
            "ticker": order.market_id,
            "side": order.outcome.value,  # "yes" or "no"
            "action": order.side.value,  # "buy" or "sell"
            "count": int(order.size),
        }

        price_cents = int(order.price * 100)
        # Add cushion for buys, subtract for sells
        if order.side == Side.BUY:
            price_cents = min(price_cents + self._price_cushion_cents, 99)
        else:
            price_cents = max(price_cents - self._price_cushion_cents, 1)
        if price_cents < 1 or price_cents > 99:
            return OrderResponse(
                order_id="",
                market_id=order.market_id,
                status=OrderStatus.FAILED,
                side=order.side,
                outcome=order.outcome,
                price=order.price,
                size=order.size,
                raw={"error": f"Price {price_cents}c out of range [1, 99]"},
            )
        # Kalshi always wants yes_price — convert NO prices
        if order.outcome == Outcome.NO:
            yes_price_cents = 100 - price_cents
        else:
            yes_price_cents = price_cents
        kalshi_order["yes_price"] = yes_price_cents
        kalshi_order["time_in_force"] = "immediate_or_cancel"
        logger.info(
            "Kalshi: placing IOC order %s %s @ %dc (yes_price=%dc) x%d on %s",
            order.side.value, order.outcome.value,
            price_cents, yes_price_cents, int(order.size), order.market_id,
        )

        if order.client_order_id:
            kalshi_order["client_order_id"] = order.client_order_id

        try:
            result = await self._client.place_order(kalshi_order)
            status = _map_kalshi_status(result.get("status", ""))
            fill_count = _get_fill_count(result)
            fill_price = _compute_fill_price(result, order.outcome.value)
            taker_fees = (
                result.get("taker_fees_dollars")
                or result.get("taker_fees")
                or 0
            )

            logger.info(
                "Kalshi: order response status=%s fills=%d "
                "price=%s fees=%s",
                result.get("status"), fill_count,
                fill_price, taker_fees,
            )

            return OrderResponse(
                order_id=result.get("order_id", ""),
                market_id=order.market_id,
                status=status,
                side=order.side,
                outcome=order.outcome,
                price=order.price,
                size=order.size,
                filled_size=Decimal(str(fill_count)),
                avg_fill_price=fill_price,
                raw=result,
            )
        except httpx.HTTPStatusError as e:
            # Extract Kalshi's error response body
            try:
                error_body = e.response.json()
                error_msg = error_body.get("message", e.response.text)
            except Exception:
                error_msg = e.response.text
            logger.error(
                "Kalshi: order rejected [%d]: %s",
                e.response.status_code, error_msg,
            )
            return OrderResponse(
                order_id="",
                market_id=order.market_id,
                status=OrderStatus.FAILED,
                side=order.side,
                outcome=order.outcome,
                price=order.price,
                size=order.size,
                raw={
                    "error": error_msg,
                    "status_code": e.response.status_code,
                },
            )
        except Exception as e:
            logger.error("Kalshi: order failed: %s", e)
            return OrderResponse(
                order_id="",
                market_id=order.market_id,
                status=OrderStatus.FAILED,
                side=order.side,
                outcome=order.outcome,
                price=order.price,
                size=order.size,
                raw={"error": str(e)},
            )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await self._client.cancel_order(order_id)
            logger.info("Kalshi: cancelled order %s", order_id)
            return True
        except Exception as e:
            logger.error("Kalshi: cancel failed for %s: %s", order_id, e)
            return False

    async def cancel_all(self, market_id: str | None = None) -> int:
        orders = await self._client.get_open_orders(ticker=market_id)
        cancelled = 0
        for o in orders:
            oid = o.get("order_id", "")
            if oid and await self.cancel_order(oid):
                cancelled += 1
        return cancelled

    async def get_open_orders(self, market_id: str | None = None) -> list[OrderResponse]:
        orders = await self._client.get_open_orders(ticker=market_id)
        result = []
        for o in orders:
            result.append(OrderResponse(
                order_id=o.get("order_id", ""),
                market_id=o.get("ticker", ""),
                status=_map_kalshi_status(o.get("status", "")),
                side=Side.BUY if o.get("action") == "buy" else Side.SELL,
                outcome=Outcome.YES if o.get("side") == "yes" else Outcome.NO,
                price=dec(o.get("yes_price")) or Decimal("0"),
                size=dec(o.get("remaining_count")) or Decimal("0"),
                filled_size=dec(o.get("fill_count")) or Decimal("0"),
                raw=o,
            ))
        return result

    async def get_positions(self, market_id: str | None = None) -> list[Position]:
        positions = await self._client.get_positions(ticker=market_id)
        result = []
        for p in positions:
            ticker = p.get("ticker", "")
            for side_key, outcome in [("yes", Outcome.YES), ("no", Outcome.NO)]:
                size = int(p.get(f"{side_key}_count", 0))
                if size > 0:
                    result.append(Position(
                        market_id=ticker,
                        outcome=outcome,
                        size=Decimal(str(size)),
                        avg_entry_price=dec(p.get(f"avg_{side_key}_price")) or Decimal("0"),
                    ))
        return result

    async def get_balance(self) -> Decimal:
        data = await self._client.get_balance()
        cents = dec(data.get("balance")) or Decimal("0")
        return cents / Decimal("100")
