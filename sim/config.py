"""Configuration dataclasses for whale signal Monte Carlo simulation."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BankrollPhase:
    """Risk-based sizing cap for a bankroll range.

    max_bet_pct: max fraction of bankroll per position (risk tolerance).
    The effective position size is: min(sizing_output, bankroll * max_bet_pct, liquidity).
    Liquidity is a separate constraint modeled at the trade level, not here.
    """

    floor: float  # Inclusive lower bound
    ceiling: float  # Exclusive upper bound
    max_bet_pct: float  # Max fraction of bankroll per trade (0.0-1.0)


DEFAULT_PHASES: tuple[BankrollPhase, ...] = (
    BankrollPhase(0.0, 500.0, 1.0),       # Full flex, small bets anyway
    BankrollPhase(500.0, 1_000.0, 0.50),   # Starting to diversify
    BankrollPhase(1_000.0, 5_000.0, 0.30), # Moderate risk
    BankrollPhase(5_000.0, 50_000.0, 0.20),
    BankrollPhase(50_000.0, float("inf"), 0.10),  # Risk management at scale
)


@dataclass(frozen=True)
class TradeParams:
    """Parameters for a single simulation scenario."""

    win_rate: float = 0.93
    entry_price: float = 0.92
    stop_loss_pct: float = 0.10  # Fraction below entry (0.10 = sell at 90% of entry)
    trades_per_day: int = 10
    sizing_strategy: str = "half_port"  # full_port | half_port | fractional_kelly
    kelly_fraction: float = 0.25  # Only used for fractional_kelly
    max_concurrent: int = 1
    drift_penalty: float = 0.0  # Subtracted from win_rate for effective win rate
    liquidity: float = 5_000.0  # Max dollars fillable at target price (order book depth)


@dataclass(frozen=True)
class SimConfig:
    """Simulation-level settings (not swept)."""

    starting_bankroll: float = 100.0
    num_simulations: int = 10_000
    num_days: int = 365
    phases: tuple[BankrollPhase, ...] = DEFAULT_PHASES
    seed: int | None = None


@dataclass(frozen=True)
class SweepConfig:
    """Parameter ranges for the full matrix sweep."""

    win_rates: tuple[float, ...] = (0.90, 0.91, 0.92, 0.93, 0.94, 0.95, 0.96)
    entry_prices: tuple[float, ...] = (0.90, 0.91, 0.92, 0.93, 0.94)
    stop_loss_pcts: tuple[float, ...] = (0.05, 0.08, 0.10, 0.15)
    trades_per_day: tuple[int, ...] = (5, 8, 10, 15)
    sizing_strategies: tuple[str, ...] = ("full_port", "half_port", "fractional_kelly")
    max_concurrents: tuple[int, ...] = (1, 2)
    drift_penalties: tuple[float, ...] = (0.0, 0.02, 0.04)
    liquidities: tuple[float, ...] = (2_000.0, 5_000.0, 10_000.0, 25_000.0)

    @property
    def total_combos(self) -> int:
        return (
            len(self.win_rates)
            * len(self.entry_prices)
            * len(self.stop_loss_pcts)
            * len(self.trades_per_day)
            * len(self.sizing_strategies)
            * len(self.max_concurrents)
            * len(self.drift_penalties)
            * len(self.liquidities)
        )


# Quick sweep: reduced grid for fast iteration
QUICK_SWEEP = SweepConfig(
    win_rates=(0.91, 0.93, 0.95),
    entry_prices=(0.90, 0.92, 0.94),
    stop_loss_pcts=(0.05, 0.10, 0.15),
    trades_per_day=(5, 10),
    sizing_strategies=("half_port", "fractional_kelly"),
    max_concurrents=(1,),
    drift_penalties=(0.0, 0.02),
    liquidities=(2_000.0, 5_000.0, 10_000.0),
)
