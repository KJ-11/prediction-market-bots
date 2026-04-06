# Whale-following strategy types and configuration.

"""Strategy config, signal types, and scoring for whale-following bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal


@dataclass(frozen=True)
class WhaleConfig:
    """All configurable knobs — defaults from validated backtest."""

    whale_threshold: Decimal = Decimal("1000")  # Min notional for whale trade
    min_whale_count: int = 3  # Min whale trades to trigger signal
    consensus_pct: float = 0.90  # Min % of whale volume on one side
    price_min: Decimal = Decimal("0.85")  # Entry price range
    price_max: Decimal = Decimal("0.95")
    whale_window_min: int = 30  # All whale trades must be within this window
    categories: tuple[str, ...] = ("sports", "economics")
    max_concurrent: int = 2  # Max open positions at once


@dataclass
class WhaleTrade:
    """A single whale trade detected via WS."""

    trade_id: str
    market_ticker: str
    price: Decimal
    size: Decimal  # Number of contracts
    notional: Decimal  # price * size
    taker_side: str  # "yes" or "no"
    timestamp: datetime


@dataclass
class MarketWhaleState:
    """Aggregated whale activity on a single market."""

    market_ticker: str
    trades: list[WhaleTrade] = field(default_factory=list)
    yes_volume: Decimal = Decimal("0")  # Total whale notional on YES
    no_volume: Decimal = Decimal("0")  # Total whale notional on NO
    signal_emitted: bool = False  # Only emit once per market

    @property
    def whale_count(self) -> int:
        return len(self.trades)

    @property
    def total_volume(self) -> Decimal:
        return self.yes_volume + self.no_volume

    @property
    def consensus_side(self) -> str | None:
        """Side with majority of whale volume, or None if no trades."""
        if self.total_volume == 0:
            return None
        if self.yes_volume >= self.no_volume:
            return "yes"
        return "no"

    @property
    def consensus_pct(self) -> float:
        """Fraction of whale volume on the majority side."""
        if self.total_volume == 0:
            return 0.0
        majority = max(self.yes_volume, self.no_volume)
        return float(majority / self.total_volume)

    def add_trade(self, trade: WhaleTrade) -> None:
        self.trades.append(trade)
        if trade.taker_side == "yes":
            self.yes_volume += trade.notional
        else:
            self.no_volume += trade.notional

    def recent_trades(self, window_min: int) -> list[WhaleTrade]:
        """Return trades within the last window_min minutes."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_min)
        return [t for t in self.trades if t.timestamp >= cutoff]


@dataclass
class WhaleSignal:
    """Signal emitted when a market meets all whale criteria."""

    market_ticker: str
    side: str  # "yes" or "no" — the consensus side
    whale_count: int
    consensus_pct: float
    total_volume: Decimal
    best_ask: Decimal  # Current ask on consensus side
    confidence: float  # Used for sizing (= consensus_pct)
    whale_avg_price: Decimal = Decimal("0")  # VWAP of whale trades on consensus side
