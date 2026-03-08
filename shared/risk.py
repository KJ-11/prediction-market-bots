"""Kill switch and risk limits — checked before every order."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from shared.execution.base import AbstractExecutionEngine
from shared.types import OrderRequest

logger = logging.getLogger(__name__)


class KillSwitchTriggered(Exception):
    """Raised when any kill condition fires. Runner catches this → exit code 42."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class KillSwitch:
    """Three kill conditions: file-based, loss-based, error-based.

    Call check() on every loop iteration. Raises KillSwitchTriggered
    if any condition is met.
    """

    def __init__(
        self,
        kill_file: str = "/tmp/prediction-bots-kill",
        max_loss_pct: float = 20.0,
        max_consecutive_errors: int = 10,
        initial_balance: Decimal = Decimal("50"),
    ) -> None:
        self._kill_file = kill_file
        self._max_loss_pct = max_loss_pct
        self._max_consecutive_errors = max_consecutive_errors
        self._initial_balance = initial_balance
        self._consecutive_errors = 0
        self._disabled = False

    def record_error(self) -> None:
        """Increment consecutive error counter."""
        self._consecutive_errors += 1
        logger.warning("Kill switch: error count = %d", self._consecutive_errors)

    def clear_errors(self) -> None:
        """Reset error counter after a successful operation."""
        if self._consecutive_errors > 0:
            self._consecutive_errors = 0

    def disable(self) -> None:
        """Disable kill switch (for testing only)."""
        self._disabled = True

    def check(self, current_balance: Decimal | None = None) -> None:
        """Check all kill conditions. Raises KillSwitchTriggered if any fire."""
        if self._disabled:
            return

        # File-based kill
        if Path(self._kill_file).exists():
            raise KillSwitchTriggered(
                f"Kill file detected: {self._kill_file}"
            )

        # Error-based kill
        if self._consecutive_errors >= self._max_consecutive_errors:
            raise KillSwitchTriggered(
                f"Too many consecutive errors: {self._consecutive_errors}"
            )

        # Loss-based kill
        if current_balance is not None and self._initial_balance > 0:
            loss_pct = (
                (self._initial_balance - current_balance) / self._initial_balance
            ) * 100
            if loss_pct >= self._max_loss_pct:
                raise KillSwitchTriggered(
                    f"Capital loss {loss_pct:.1f}% exceeds limit {self._max_loss_pct}%"
                )


@dataclass
class RiskCheckResult:
    """Result of a risk check."""

    allowed: bool
    reason: str = ""


class RiskLimits:
    """Pre-trade risk checks: position size, exposure, per-trade loss.

    All limits are percentage-based so they scale with balance.
    Call check() before every order. Returns RiskCheckResult.
    """

    def __init__(
        self,
        max_position_pct: float = 50.0,
        max_exposure_pct: float = 80.0,
        max_loss_per_trade_pct: float = 30.0,
        max_orders_per_min: int = 30,
    ) -> None:
        self._max_position_pct = max_position_pct
        self._max_exposure_pct = max_exposure_pct
        self._max_loss_per_trade_pct = max_loss_per_trade_pct
        self._max_orders_per_min = max_orders_per_min
        self._recent_orders: list[float] = []  # timestamps

    async def check(
        self,
        order: OrderRequest,
        engine: AbstractExecutionEngine,
    ) -> RiskCheckResult:
        """Run all risk checks against an order."""
        balance = await engine.get_balance()
        if balance <= 0:
            return RiskCheckResult(
                allowed=False, reason="Zero balance",
            )

        trade_cost = order.price * order.size
        trade_pct = float(trade_cost / balance) * 100

        # Per-trade loss check (binary: max loss = cost)
        if trade_pct > self._max_loss_per_trade_pct:
            return RiskCheckResult(
                allowed=False,
                reason=(
                    f"Trade ${trade_cost:.2f} = {trade_pct:.0f}% "
                    f"of balance (limit {self._max_loss_per_trade_pct:.0f}%)"
                ),
            )

        # Position size check
        if trade_pct > self._max_position_pct:
            return RiskCheckResult(
                allowed=False,
                reason=(
                    f"Position {trade_pct:.0f}% "
                    f"of balance (limit {self._max_position_pct:.0f}%)"
                ),
            )

        # Total exposure check
        positions = await engine.get_positions()
        total_exposure = sum(
            p.size * p.avg_entry_price for p in positions
        )
        exposure_pct = float(
            (total_exposure + trade_cost) / balance
        ) * 100
        if exposure_pct > self._max_exposure_pct:
            return RiskCheckResult(
                allowed=False,
                reason=(
                    f"Exposure {exposure_pct:.0f}% "
                    f"of balance (limit {self._max_exposure_pct:.0f}%)"
                ),
            )

        # Rate limit check
        import time

        now = time.monotonic()
        self._recent_orders = [
            t for t in self._recent_orders if now - t < 60
        ]
        if len(self._recent_orders) >= self._max_orders_per_min:
            return RiskCheckResult(
                allowed=False,
                reason=f"Rate limit: {len(self._recent_orders)}/min",
            )

        return RiskCheckResult(allowed=True)

    def record_order(self) -> None:
        """Record that an order was placed (for rate limiting)."""
        import time

        self._recent_orders.append(time.monotonic())


class CircuitBreaker:
    """Circuit breakers for automated risk management.

    Tracks round-level wins/losses and daily P&L to trigger cool-offs
    and stop-trading conditions.

    Conditions:
        - 3 consecutive round losses -> skip next round
        - Daily loss > 20% of day-start balance -> stop for day
        - Total drawdown > 40% from all-time high -> kill switch
    """

    def __init__(
        self,
        max_consecutive_losses: int = 3,
        daily_loss_limit_pct: float = 20.0,
        max_drawdown_pct: float = 40.0,
    ) -> None:
        self._max_consecutive_losses = max_consecutive_losses
        self._daily_loss_limit_pct = daily_loss_limit_pct
        self._max_drawdown_pct = max_drawdown_pct

        self._consecutive_losses = 0
        self._skip_next_round = False
        self._stopped_for_day = False
        self._day_start_balance: Decimal | None = None
        self._all_time_high: Decimal = Decimal("0")

    def set_day_start_balance(self, balance: Decimal) -> None:
        """Call at the start of each trading day."""
        self._day_start_balance = balance
        if balance > self._all_time_high:
            self._all_time_high = balance
        self._stopped_for_day = False
        self._consecutive_losses = 0
        self._skip_next_round = False

    def record_round_result(self, won: bool, current_balance: Decimal) -> None:
        """Record the result of a completed round."""
        if current_balance > self._all_time_high:
            self._all_time_high = current_balance

        if won:
            self._consecutive_losses = 0
            self._skip_next_round = False
        else:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self._max_consecutive_losses:
                self._skip_next_round = True
                logger.warning(
                    "Circuit breaker: %d consecutive losses, skipping next round",
                    self._consecutive_losses,
                )

    def check(self, current_balance: Decimal) -> None:
        """Check circuit breaker conditions. Raises KillSwitchTriggered for drawdown.

        For daily loss, sets stopped_for_day flag.
        """
        # Update ATH
        if current_balance > self._all_time_high:
            self._all_time_high = current_balance

        # Daily loss check
        if self._day_start_balance is not None and self._day_start_balance > 0:
            daily_loss_pct = (
                (self._day_start_balance - current_balance) / self._day_start_balance
            ) * 100
            if daily_loss_pct >= self._daily_loss_limit_pct:
                self._stopped_for_day = True
                logger.warning(
                    "Circuit breaker: daily loss %.1f%% >= %.1f%% limit",
                    daily_loss_pct, self._daily_loss_limit_pct,
                )

        # Total drawdown check
        if self._all_time_high > 0:
            drawdown_pct = (
                (self._all_time_high - current_balance) / self._all_time_high
            ) * 100
            if drawdown_pct >= self._max_drawdown_pct:
                raise KillSwitchTriggered(
                    f"Drawdown {drawdown_pct:.1f}% from ATH ${self._all_time_high} "
                    f"exceeds {self._max_drawdown_pct}% limit"
                )

    @property
    def should_skip_round(self) -> bool:
        """True if the next round should be skipped (consecutive losses)."""
        return self._skip_next_round

    @property
    def stopped_for_day(self) -> bool:
        """True if daily loss limit was hit."""
        return self._stopped_for_day

    def clear_skip(self) -> None:
        """Clear the skip-round flag after skipping one round."""
        self._skip_next_round = False
