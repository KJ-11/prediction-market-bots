"""Kalshi fee model — float-based for simulation speed.

Canonical Decimal version: bots/kalshi_crypto/sizing.py:19-22
Formula: fee = ceil_to_cent(0.07 * contracts * price * (1 - price))
"""

from __future__ import annotations

import math

FEE_COEFFICIENT = 0.07


def kalshi_fee(price: float, contracts: int = 1) -> float:
    """Kalshi taker fee, rounded up to the next cent.

    Matches the Decimal version in bots/kalshi_crypto/sizing.py.
    """
    raw = FEE_COEFFICIENT * contracts * price * (1.0 - price)
    return math.ceil(raw * 100) / 100
