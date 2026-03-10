"""Decimal helpers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def dec(value) -> Decimal | None:
    """Parse a value to Decimal, returning None on failure."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
