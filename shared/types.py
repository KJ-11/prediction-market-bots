"""Shared dataclasses for inter-component communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Outcome(str, Enum):
    YES = "yes"
    NO = "no"


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class PriceUpdate:
    """Emitted by WS handlers onto asyncio.Queue."""

    market_id: str  # Kalshi ticker
    yes_price: Decimal | None = None
    no_price: Decimal | None = None
    yes_bid: Decimal | None = None
    yes_ask: Decimal | None = None
    no_bid: Decimal | None = None
    no_ask: Decimal | None = None
    yes_bid_size: Decimal | None = None
    yes_ask_size: Decimal | None = None
    volume: Decimal | None = None
    last_trade_price: Decimal | None = None


@dataclass
class OrderRequest:
    """Submitted to an execution engine."""

    market_id: str  # Kalshi ticker
    side: Side
    outcome: Outcome
    price: Decimal  # Limit price in dollars (0.01 - 0.99)
    size: Decimal  # Number of contracts
    client_order_id: str | None = None


@dataclass
class OrderResponse:
    """Returned by execution engine after placing an order."""

    order_id: str
    market_id: str
    status: OrderStatus
    side: Side
    outcome: Outcome
    price: Decimal
    size: Decimal
    filled_size: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Fill:
    """A single fill event."""

    order_id: str
    market_id: str
    price: Decimal
    size: Decimal
    side: Side
    outcome: Outcome
    fee: Decimal = Decimal("0")
    timestamp: str | None = None


@dataclass
class Position:
    """Current position in a market."""

    market_id: str
    outcome: Outcome
    size: Decimal  # Positive = long
    avg_entry_price: Decimal
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
