# Position sizing. Kelly criterion with Kalshi fee model.

"""Position sizing — fractional Kelly criterion for Kalshi contracts."""

from __future__ import annotations

import logging
from decimal import Decimal
from enum import Enum

logger = logging.getLogger(__name__)

# Kalshi taker fee: round_up(0.07 * C * P * (1 - P))
# Rounding applies to the whole order, not per contract.
FEE_COEFFICIENT = Decimal("0.07")
ONE_CENT = Decimal("0.01")


def kalshi_fee(price: Decimal, contracts: int = 1) -> Decimal:
    """Calculate Kalshi taker fee for an order (rounded up to next cent)."""
    raw = FEE_COEFFICIENT * contracts * price * (Decimal("1") - price)
    return raw.quantize(ONE_CENT, rounding="ROUND_CEILING")


class SizingMode(str, Enum):
    FIXED = "fixed"
    KELLY = "kelly"
    FRACTIONAL_KELLY = "fractional_kelly"


class PositionSizer:
    """Computes position size using Kelly criterion.

    Modes:
        FIXED — always returns fixed_size contracts
        KELLY — full Kelly criterion
        FRACTIONAL_KELLY — fraction of Kelly (default: quarter-Kelly)

    Only capped by available balance — no artificial position limits.
    """

    def __init__(
        self,
        mode: SizingMode = SizingMode.FRACTIONAL_KELLY,
        fixed_size: int = 10,
        kelly_fraction: float = 0.25,
    ) -> None:
        self._mode = mode
        self._fixed_size = fixed_size
        self._kelly_fraction = kelly_fraction

    def compute(
        self,
        price: Decimal,
        confidence: float,
        balance: Decimal,
    ) -> int:
        """Compute number of contracts to trade.

        Args:
            price: Contract price (0.01-0.99).
            confidence: Estimated win probability (0.0-1.0).
            balance: Current available balance.

        Returns:
            Number of contracts (integer, >= 0).
        """
        if self._mode == SizingMode.FIXED:
            size = self._fixed_size
        else:
            size = self._kelly_size(price, confidence, balance)

        # Only cap by what we can afford
        if price > 0 and size > 0:
            fee = kalshi_fee(price, size)
            total_cost = price * size + fee
            while total_cost > balance and size > 0:
                size -= 1
                fee = kalshi_fee(price, size)
                total_cost = price * size + fee

        return max(size, 0)

    def _kelly_size(
        self,
        price: Decimal,
        confidence: float,
        balance: Decimal,
    ) -> int:
        """Kelly criterion adjusted for fees.

        Kelly fraction f = (p * b - q) / b
        where p = win prob, q = 1-p, b = net payout ratio

        For binary contract at price P:
            Win: payout = $1 - fee, cost = P + fee -> net = (1 - P - 2*fee)
            b = net / (P + fee)
        """
        if confidence <= 0 or price <= 0 or price >= Decimal("1"):
            return 0

        fee = kalshi_fee(price)
        cost_per_contract = price + fee
        net_win = Decimal("1") - price - fee

        if net_win <= 0:
            return 0

        b = float(net_win / cost_per_contract)  # odds ratio
        p = confidence
        q = 1.0 - p

        kelly_f = (p * b - q) / b if b > 0 else 0.0

        if self._mode == SizingMode.FRACTIONAL_KELLY:
            kelly_f *= self._kelly_fraction

        if kelly_f <= 0:
            return 0

        # Convert fraction of bankroll to contracts
        dollar_size = float(balance) * kelly_f
        contracts = int(dollar_size / float(cost_per_contract))

        return max(contracts, 0)
