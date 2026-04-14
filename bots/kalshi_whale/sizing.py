"""Position sizing — risk-phased half_port from Monte Carlo sim.

Phases (from sim validation):
    $0-500:    100% of balance per trade
    $500-1k:   50%
    $1k-5k:    30%
    $5k-50k:   20%
    $50k+:     10%

After computing the dollar amount, converts to contracts at the given price,
then subtracts Kalshi fees to ensure we don't exceed available balance.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from shared.fees import kalshi_fee

logger = logging.getLogger(__name__)

# Phase thresholds: (min_balance, allocation_pct)
PHASES: list[tuple[Decimal, float]] = [
    (Decimal("50000"), 0.10),
    (Decimal("5000"), 0.20),
    (Decimal("1000"), 0.30),
    (Decimal("500"), 0.50),
    (Decimal("0"), 1.00),
]


def compute_size(price: Decimal, balance: Decimal) -> int:
    """Compute number of contracts using risk-phased half_port sizing.

    Args:
        price: Contract price (0.01-0.99).
        balance: Current available balance.

    Returns:
        Number of contracts (integer, >= 0).
    """
    if price <= 0 or price >= Decimal("1") or balance <= 0:
        return 0

    # Determine allocation fraction from phase table
    alloc_pct = 1.0
    for threshold, pct in PHASES:
        if balance >= threshold:
            alloc_pct = pct
            break

    dollar_amount = balance * Decimal(str(alloc_pct))

    # Convert to contracts
    cost_per_contract = price + kalshi_fee(price)
    if cost_per_contract <= 0:
        return 0

    size = int(dollar_amount / cost_per_contract)

    # Verify total cost doesn't exceed balance (rounding can cause overshoot)
    while size > 0:
        fee = kalshi_fee(price, size)
        total_cost = price * size + fee
        if total_cost <= balance:
            break
        size -= 1

    return max(size, 0)
