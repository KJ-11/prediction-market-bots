"""Kalshi execution engine.

Maps OrderRequest to Kalshi's POST /portfolio/orders API (v2).

Order fields we send: ticker, side (yes/no), action (buy/sell), count (int),
yes_price (1–99 cents), time_in_force="immediate_or_cancel". IOC so orders
fill instantly or cancel — no resting orders on the book.

Response fields we read (v2, dollar-denominated): fill_count_fp (float string
like "31.00"), taker_fill_cost_dollars, taker_fees_dollars.
Status values: resting, canceled, executed.
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


class KalshiExecutionEngine(AbstractExecutionEngine):
    _STATUS_MAP = {
        "resting": OrderStatus.OPEN,
        "canceled": OrderStatus.CANCELLED,
        "executed": OrderStatus.FILLED,
    }

    def __init__(
        self,
        client: KalshiClient,
        price_cushion_cents: int = 2,
    ) -> None:
        self._client = client
        self._price_cushion_cents = price_cushion_cents

    @staticmethod
    def _map_status(status: str) -> OrderStatus:
        return KalshiExecutionEngine._STATUS_MAP.get(status, OrderStatus.PENDING)

    @staticmethod
    def _get_fill_count(result: dict) -> int:
        """Extract fill count from Kalshi response.

        fill_count_fp is a float string like "31.00"; int(float(...)) handles that.
        """
        return int(float(result.get("fill_count_fp") or 0))

    @staticmethod
    def _compute_fill_price(result: dict) -> Decimal | None:
        """Compute average fill price from Kalshi response fields.

        taker_fill_cost_dollars is the actual cost paid, regardless of YES/NO
        side. Dividing by fill count gives the correct per-contract price
        directly — no YES/NO conversion needed.
        """
        fill_count = KalshiExecutionEngine._get_fill_count(result)
        cost = result.get("taker_fill_cost_dollars")
        if not fill_count or cost is None:
            return None
        cost_dec = Decimal(str(cost))
        if cost_dec == 0:
            return None
        return cost_dec / Decimal(str(fill_count))

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
            status = self._map_status(result.get("status", ""))
            fill_count = self._get_fill_count(result)
            fill_price = self._compute_fill_price(result)
            taker_fees = result.get("taker_fees_dollars") or 0

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
                status=self._map_status(o.get("status", "")),
                side=Side.BUY if o.get("action") == "buy" else Side.SELL,
                outcome=Outcome.YES if o.get("side") == "yes" else Outcome.NO,
                price=dec(o.get("yes_price")) or Decimal("0"),
                size=dec(o.get("remaining_count")) or Decimal("0"),
                filled_size=dec(o.get("fill_count_fp")) or Decimal("0"),
                raw=o,
            ))
        return result

    async def get_positions(self, market_id: str | None = None) -> list[Position]:
        # Kalshi v2 portfolio fields:
        #   position_fp: signed quantity (positive = YES, negative = NO),
        #     "0.00" when flat
        #   market_exposure_dollars: dollar cost of the open position
        #   ticker: market ticker
        # The legacy yes_count/no_count/avg_*_price fields no longer exist.
        # Use count_filter=position so the API returns only non-zero positions.
        positions = await self._client.get_positions(
            ticker=market_id, count_filter="position",
        )
        result = []
        for p in positions:
            ticker = p.get("ticker", "")
            qty = float(p.get("position_fp", 0) or 0)
            if qty == 0:
                continue
            size = Decimal(str(abs(int(qty))))
            outcome = Outcome.YES if qty > 0 else Outcome.NO
            exposure = dec(p.get("market_exposure_dollars")) or Decimal("0")
            avg_price = (exposure / size) if size > 0 else Decimal("0")
            result.append(Position(
                market_id=ticker,
                outcome=outcome,
                size=size,
                avg_entry_price=avg_price,
            ))
        return result

    async def get_balance(self) -> Decimal:
        data = await self._client.get_balance()
        cents = dec(data.get("balance")) or Decimal("0")
        return cents / Decimal("100")
