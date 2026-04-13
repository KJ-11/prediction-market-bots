"""Kill switch and risk limits — checked before every order."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from shared.execution.base import AbstractExecutionEngine
from shared.types import OrderRequest

logger = logging.getLogger(__name__)

CST = ZoneInfo("America/Chicago")
DEFAULT_BREAKER_FILE = Path("data/circuit_breaker.json")


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
        self._recent_orders.append(time.monotonic())


class CircuitBreaker:
    """Circuit breakers for automated risk management.

    Tracks round-level wins/losses and daily P&L to trigger cool-offs
    and stop-trading conditions. State persists to disk so Docker restarts
    don't reset the breaker mid-day.

    Conditions:
        - 3 consecutive round losses -> skip next round
        - Daily loss > daily_loss_limit_pct of day-start balance -> stop for day
        - Total drawdown > dynamic threshold from ATH -> kill switch (24h pause)

    Dynamic drawdown: at small balances (<$500) a single loss with full
    allocation can wipe 50% equity. Drawdown limit scales with balance
    so the bot survives early variance but tightens as capital grows:
        <$500:  70%    (survive Phase 1 variance)
        $500-1k: 60%
        $1k-5k:  50%
        $5k+:    40%   (protect real capital)
    """

    # (min_balance, drawdown_pct) — checked top-down
    DRAWDOWN_TIERS: list[tuple[Decimal, float]] = [
        (Decimal("5000"), 40.0),
        (Decimal("1000"), 50.0),
        (Decimal("500"), 60.0),
        (Decimal("0"), 70.0),
    ]

    def __init__(
        self,
        max_consecutive_losses: int = 3,
        daily_loss_limit_pct: float = 20.0,
        max_drawdown_pct: float = 40.0,
        state_file: Path | None = DEFAULT_BREAKER_FILE,
        dynamic_drawdown: bool = False,
    ) -> None:
        self._max_consecutive_losses = max_consecutive_losses
        self._daily_loss_limit_pct = daily_loss_limit_pct
        self._max_drawdown_pct = max_drawdown_pct
        self._dynamic_drawdown = dynamic_drawdown
        self._state_file = state_file

        self._consecutive_losses = 0
        self._skip_next_round = False
        self._stopped_for_day = False
        self._day_start_balance: Decimal | None = None
        self._all_time_high: Decimal = Decimal("0")
        self._date: str = ""  # CST date string YYYY-MM-DD

    def _today_cst(self) -> str:
        return datetime.now(CST).strftime("%Y-%m-%d")

    def _save_state(self) -> None:
        """Persist breaker state to disk."""
        if self._state_file is None:
            return
        state = {
            "date": self._date,
            "day_start_balance": str(self._day_start_balance) if self._day_start_balance else None,
            "stopped_for_day": self._stopped_for_day,
            "consecutive_losses": self._consecutive_losses,
            "skip_next_round": self._skip_next_round,
            "all_time_high": str(self._all_time_high),
        }
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps(state))
        except Exception as e:
            logger.warning("Failed to save circuit breaker state: %s", e)

    def _load_state(self) -> bool:
        """Load persisted state. Returns True if same-day state was restored."""
        if self._state_file is None or not self._state_file.exists():
            return False
        try:
            state = json.loads(self._state_file.read_text())
            saved_date = state.get("date", "")
            today = self._today_cst()
            if saved_date != today:
                logger.info(
                    "Circuit breaker: new day (%s vs saved %s), resetting",
                    today, saved_date,
                )
                return False
            # Same day — restore state
            self._date = saved_date
            dsb = state.get("day_start_balance")
            self._day_start_balance = Decimal(dsb) if dsb else None
            self._stopped_for_day = state.get("stopped_for_day", False)
            self._consecutive_losses = state.get("consecutive_losses", 0)
            self._skip_next_round = state.get("skip_next_round", False)
            ath = state.get("all_time_high")
            if ath:
                self._all_time_high = Decimal(ath)
            logger.info(
                "Circuit breaker: restored same-day state (stopped=%s, losses=%d, "
                "day_start=$%s)",
                self._stopped_for_day, self._consecutive_losses,
                self._day_start_balance,
            )
            return True
        except Exception as e:
            logger.warning("Failed to load circuit breaker state: %s", e)
            return False

    def set_day_start_balance(self, balance: Decimal) -> None:
        """Call at startup. Loads persisted state if same day, otherwise resets."""
        restored = self._load_state()
        if restored:
            # Same day: keep stopped_for_day and consecutive_losses from disk.
            # Update ATH if current balance is higher.
            if balance > self._all_time_high:
                self._all_time_high = balance
            return
        # New day or no saved state — fresh start
        self._reset_for_new_day(balance)

    def _reset_for_new_day(self, balance: Decimal) -> None:
        """Fresh-day reset: clears stopped_for_day, losses, and re-anchors day_start."""
        self._date = self._today_cst()
        self._day_start_balance = balance
        if balance > self._all_time_high:
            self._all_time_high = balance
        self._stopped_for_day = False
        self._consecutive_losses = 0
        self._skip_next_round = False
        self._save_state()

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
        self._save_state()

    def _effective_drawdown_pct(self, balance: Decimal) -> float:
        """Return the drawdown limit for the current balance tier.

        When dynamic_drawdown is enabled, smaller balances get a wider
        limit so the bot survives early Phase-1 variance.
        """
        if not self._dynamic_drawdown:
            return self._max_drawdown_pct
        for threshold, pct in self.DRAWDOWN_TIERS:
            if balance >= threshold:
                return pct
        return self._max_drawdown_pct

    def check(self, current_balance: Decimal) -> None:
        """Check circuit breaker conditions. Raises KillSwitchTriggered for drawdown.

        For daily loss, sets stopped_for_day flag.
        """
        # Day rollover: if the calendar day has advanced since the last reset,
        # re-anchor day_start_balance and clear the stopped_for_day flag so the
        # bot resumes trading on a new day without needing a restart.
        today = self._today_cst()
        if self._date and today != self._date:
            logger.info(
                "Circuit breaker: day rollover %s -> %s, resetting daily state",
                self._date, today,
            )
            self._reset_for_new_day(current_balance)

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
                self._save_state()
                logger.warning(
                    "Circuit breaker: daily loss %.1f%% >= %.1f%% limit",
                    daily_loss_pct, self._daily_loss_limit_pct,
                )

        # Total drawdown check — limit scales with balance when dynamic
        if self._all_time_high > 0:
            drawdown_pct = (
                (self._all_time_high - current_balance) / self._all_time_high
            ) * 100
            limit = self._effective_drawdown_pct(current_balance)
            if drawdown_pct >= limit:
                raise KillSwitchTriggered(
                    f"Drawdown {drawdown_pct:.1f}% from ATH ${self._all_time_high} "
                    f"exceeds {limit:.0f}% limit (balance ${current_balance:.2f})"
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
        self._save_state()

    def reset_ath(self, current_balance: Decimal) -> None:
        """Reset ATH to current balance. Use after deposits or manual reset."""
        self._all_time_high = current_balance
        self._stopped_for_day = False
        self._consecutive_losses = 0
        self._skip_next_round = False
        self._save_state()
        logger.info(
            "Circuit breaker: ATH reset to $%s", current_balance,
        )
