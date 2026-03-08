"""Strategy base class and round context for 15-min crypto markets."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from shared.types import OrderRequest, PriceUpdate


@dataclass
class RoundContext:
    """Describes the currently active 15-min market round."""

    ticker: str  # e.g. KXBTC15M-26MAR071345-45
    series: str  # e.g. KXBTC15M
    coin: str  # e.g. BTC
    floor_strike: Decimal  # From Kalshi API (set after ~60s of round)
    open_time: datetime
    close_time: datetime

    def seconds_remaining(self) -> float:
        """Seconds until round closes. Negative means past close."""
        now = datetime.now(timezone.utc)
        return (self.close_time - now).total_seconds()

    def seconds_elapsed(self) -> float:
        """Seconds since round opened."""
        now = datetime.now(timezone.utc)
        return (now - self.open_time).total_seconds()


@dataclass
class TradeSignal:
    """A strategy's recommendation to trade. Not executed directly."""

    order: OrderRequest
    reason: str
    confidence: float  # 0.0 to 1.0


class BaseStrategy(ABC):
    """Abstract base for all strategies running in the round loop.

    Strategies receive price updates and return TradeSignal recommendations.
    The runner applies sizing and risk checks before execution.
    """

    @abstractmethod
    def on_round_start(self, ctx: RoundContext) -> None:
        """Called once at the start of a new round. Reset state here."""

    @abstractmethod
    def on_update(
        self,
        ctx: RoundContext,
        kalshi_update: PriceUpdate | None,
        spot_price: Decimal | None,
    ) -> list[TradeSignal]:
        """Called on every price update. Return signals (may be empty)."""

    @abstractmethod
    def on_round_end(self) -> None:
        """Called when the round closes. Cleanup state here."""
