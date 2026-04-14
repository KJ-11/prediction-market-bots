"""Kalshi fee model — float-based for simulation speed.

Canonical Decimal version: shared/fees.py. We duplicate the formula in
floats here so the Monte Carlo hot loop doesn't pay Decimal's overhead.
Formula: fee = ceil_to_cent(0.07 * contracts * price * (1 - price))
"""

from __future__ import annotations

import math

FEE_COEFFICIENT = 0.07


def kalshi_fee(price: float, contracts: int = 1) -> float:
    """Kalshi taker fee, rounded up to the next cent.

    Matches the Decimal version in shared/fees.py.
    """
    raw = FEE_COEFFICIENT * contracts * price * (1.0 - price)
    return math.ceil(raw * 100) / 100
