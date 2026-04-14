"""Pre-trade risk checks: position size, exposure, per-trade loss, rate limit.

All limits are percentage-based so they scale with balance. Call check()
before every order and gate on the returned RiskCheckResult.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from shared.execution.base import AbstractExecutionEngine
from shared.types import OrderRequest


@dataclass
class RiskCheckResult:
    """Result of a risk check."""

    allowed: bool
    reason: str = ""


class RiskLimits:
    """Pre-trade risk checks.

    Call check() before every order. Returns RiskCheckResult — gate on
    .allowed; the .reason is for logging/alerting when rejected.
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
        *,
        balance: Decimal | None = None,
        equity: Decimal | None = None,
    ) -> RiskCheckResult:
        """Run all risk checks against an order.

        Args:
            balance: Pre-fetched cash balance. If None, fetches from engine.
            equity: Pre-fetched equity (cash + positions). If None, computes
                    from engine.get_balance() + engine.get_positions().
        """
        if balance is None:
            balance = await engine.get_balance()
        if balance <= 0:
            return RiskCheckResult(allowed=False, reason="Zero balance")

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

        # Total exposure check — use equity (cash + positions) as denominator
        # so that filled slots don't shrink the denominator and block new entries
        if equity is None:
            positions = await engine.get_positions()
            total_exposure = sum(
                p.size * p.avg_entry_price for p in positions
            )
            equity = balance + total_exposure
        exposure = equity - balance + trade_cost
        exposure_pct = float(exposure / equity) * 100 if equity > 0 else 0
        if exposure_pct > self._max_exposure_pct:
            return RiskCheckResult(
                allowed=False,
                reason=(
                    f"Exposure {exposure_pct:.0f}% "
                    f"of equity (limit {self._max_exposure_pct:.0f}%)"
                ),
            )

        # Rate limit check
        now = time.monotonic()
        self._recent_orders = [t for t in self._recent_orders if now - t < 60]
        if len(self._recent_orders) >= self._max_orders_per_min:
            return RiskCheckResult(
                allowed=False,
                reason=f"Rate limit: {len(self._recent_orders)}/min",
            )

        return RiskCheckResult(allowed=True)

    def record_order(self) -> None:
        """Record that an order was placed (for rate limiting)."""
        self._recent_orders.append(time.monotonic())
