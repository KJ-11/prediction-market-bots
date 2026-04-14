"""Kalshi fee model.

Kalshi taker fee per order: round_up(0.07 * C * P * (1 - P)) where C is
contract count and P is price. Rounding applies to the whole order, not
per contract — see https://kalshi.com/docs/fees.
"""

from __future__ import annotations

from decimal import Decimal

FEE_COEFFICIENT = Decimal("0.07")
ONE_CENT = Decimal("0.01")


def kalshi_fee(price: Decimal, contracts: int = 1) -> Decimal:
    """Kalshi taker fee for an order at the given price × contract count."""
    raw = FEE_COEFFICIENT * contracts * price * (Decimal("1") - price)
    return raw.quantize(ONE_CENT, rounding="ROUND_CEILING")
