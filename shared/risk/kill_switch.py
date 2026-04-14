"""File-based + error-based hard stop.

Checked on every loop iteration. The file-based kill (`touch KILL`) is the
primary manual emergency brake. The error-based kill trips when the bot sees
too many consecutive exceptions from its HTTP/WS dependencies.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

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
        kill_file: str = "KILL",
        max_loss_pct: float | None = 20.0,
        max_consecutive_errors: int = 10,
        initial_balance: Decimal = Decimal("50"),
    ) -> None:
        """Pass max_loss_pct=None to disable the loss-based kill (e.g. when
        drawdown is handled by CircuitBreaker instead)."""
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

        if Path(self._kill_file).exists():
            raise KillSwitchTriggered(
                f"Kill file detected: {self._kill_file}"
            )

        if self._consecutive_errors >= self._max_consecutive_errors:
            raise KillSwitchTriggered(
                f"Too many consecutive errors: {self._consecutive_errors}"
            )

        if (
            self._max_loss_pct is not None
            and current_balance is not None
            and self._initial_balance > 0
        ):
            loss_pct = (
                (self._initial_balance - current_balance) / self._initial_balance
            ) * 100
            if loss_pct >= self._max_loss_pct:
                raise KillSwitchTriggered(
                    f"Capital loss {loss_pct:.1f}% exceeds limit {self._max_loss_pct}%"
                )
