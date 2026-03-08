"""Kalshi execution engine.

Maps OrderRequest to Kalshi's POST /portfolio/orders API.
Kalshi order fields: ticker, side (yes/no), action (buy/sell), count (int),
yes_price (1-99 cents), type (limit/market).

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


def _compute_fill_price(result: dict, outcome: str) -> Decimal | None:
    """Compute average fill price from Kalshi response fields.

    Kalshi returns taker_fill_cost (cents) and fill_count.
    avg_fill_price = taker_fill_cost / fill_count / 100 (convert cents to dollars).

    For NO orders, Kalshi reports cost from the YES perspective,
    so we use: no_fill_price = 1 - (taker_fill_cost / fill_count / 100).
    """
    fill_count = result.get("fill_count", 0)
    taker_fill_cost = result.get("taker_fill_cost", 0)
    if not fill_count or not taker_fill_cost:
        return None
    yes_fill = Decimal(str(taker_fill_cost)) / Decimal(str(fill_count)) / Decimal("100")
    if outcome == "no":
        return Decimal("1") - yes_fill
    return yes_fill


class KalshiExecutionEngine(AbstractExecutionEngine):
    def __init__(self, client: KalshiClient) -> None:
        self._client = client

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place a Kalshi order."""
        kalshi_order = {
            "ticker": order.market_id,
            "side": order.outcome.value,  # "yes" or "no"
            "action": order.side.value,  # "buy" or "sell"
            "count": int(order.size),
        }

        if order.market_order:
            kalshi_order["type"] = "market"
            logger.info(
                "Kalshi: placing MARKET order %s %s x%d on %s",
                order.side.value, order.outcome.value,
                int(order.size), order.market_id,
            )
        else:
            price_cents = int(order.price * 100)
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
            kalshi_order["type"] = "limit"
            kalshi_order["yes_price"] = yes_price_cents
            logger.info(
                "Kalshi: placing LIMIT order %s %s @ %dc (yes_price=%dc) x%d on %s",
                order.side.value, order.outcome.value,
                price_cents, yes_price_cents, int(order.size), order.market_id,
            )

        if order.client_order_id:
            kalshi_order["client_order_id"] = order.client_order_id

        try:
            result = await self._client.place_order(kalshi_order)
            status = _map_kalshi_status(result.get("status", ""))
            fill_count = result.get("fill_count", 0)
            fill_price = _compute_fill_price(result, order.outcome.value)
            taker_fees = result.get("taker_fees", 0)

            logger.info(
                "Kalshi: order response status=%s fill_count=%s "
                "fill_price=%s fees=%sc raw_keys=%s",
                result.get("status"), fill_count,
                fill_price, taker_fees, list(result.keys()),
            )

            return OrderResponse(
                order_id=result.get("order_id", ""),
                market_id=order.market_id,
                status=status,
                side=order.side,
                outcome=order.outcome,
                price=order.price,
                size=order.size,
                filled_size=Decimal(str(fill_count)) if fill_count else Decimal("0"),
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
