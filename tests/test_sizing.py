"""Tests for bots/kalshi_crypto/sizing.py — fees and position sizing."""

from __future__ import annotations

from decimal import Decimal

from bots.kalshi_crypto.sizing import PositionSizer, SizingMode, kalshi_fee


class TestKalshiFee:
    def test_fee_matches_kalshi_table(self):
        # Verify against official Kalshi fee table (1 contract)
        assert kalshi_fee(Decimal("0.01")) == Decimal("0.01")
        assert kalshi_fee(Decimal("0.10")) == Decimal("0.01")
        assert kalshi_fee(Decimal("0.50")) == Decimal("0.02")
        assert kalshi_fee(Decimal("0.90")) == Decimal("0.01")
        assert kalshi_fee(Decimal("0.99")) == Decimal("0.01")

    def test_fee_100_contracts(self):
        assert kalshi_fee(Decimal("0.50"), 100) == Decimal("1.75")
        assert kalshi_fee(Decimal("0.90"), 100) == Decimal("0.63")


class TestPositionSizer:
    def test_kelly_positive_edge(self):
        sizer = PositionSizer(mode=SizingMode.KELLY)
        size = sizer.compute(Decimal("0.50"), 0.80, Decimal("500"))
        assert size > 0

    def test_kelly_no_edge(self):
        sizer = PositionSizer(mode=SizingMode.KELLY)
        size = sizer.compute(Decimal("0.50"), 0.50, Decimal("500"))
        assert size == 0

    def test_fractional_kelly_smaller_than_full(self):
        full = PositionSizer(mode=SizingMode.KELLY)
        frac = PositionSizer(mode=SizingMode.FRACTIONAL_KELLY, kelly_fraction=0.25)
        full_size = full.compute(Decimal("0.40"), 0.80, Decimal("500"))
        frac_size = frac.compute(Decimal("0.40"), 0.80, Decimal("500"))
        assert 0 < frac_size <= full_size

    def test_kelly_scales_with_balance(self):
        sizer = PositionSizer(mode=SizingMode.FRACTIONAL_KELLY, kelly_fraction=0.25)
        small = sizer.compute(Decimal("0.90"), 0.97, Decimal("100"))
        large = sizer.compute(Decimal("0.90"), 0.97, Decimal("1000"))
        assert large > small

    def test_fixed_capped_by_balance(self):
        sizer = PositionSizer(mode=SizingMode.FIXED, fixed_size=100)
        size = sizer.compute(Decimal("0.50"), 0.8, Decimal("5"))
        assert 0 < size < 100
