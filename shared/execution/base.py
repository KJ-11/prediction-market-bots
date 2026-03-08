"""Abstract base class for execution engines."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from shared.types import OrderRequest, OrderResponse, Position


class AbstractExecutionEngine(ABC):
    """Interface for platform-specific order execution."""

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """Place a single order. Returns OrderResponse with status."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID. Returns True if successfully cancelled."""

    @abstractmethod
    async def cancel_all(self, market_id: str | None = None) -> int:
        """Cancel all open orders (optionally for a specific market). Returns count cancelled."""

    @abstractmethod
    async def get_open_orders(self, market_id: str | None = None) -> list[OrderResponse]:
        """Get all open/resting orders."""

    @abstractmethod
    async def get_positions(self, market_id: str | None = None) -> list[Position]:
        """Get current positions."""

    @abstractmethod
    async def get_balance(self) -> Decimal:
        """Get available balance in dollars."""
