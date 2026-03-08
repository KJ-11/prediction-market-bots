"""Tests for bots/kalshi_crypto/sizing.py — PositionSizer."""

from __future__ import annotations

from decimal import Decimal

from bots.kalshi_crypto.sizing import PositionSizer, SizingMode, kalshi_fee


class TestKalshiFee:
    def test_fee_at_50_cents(self):
        # round_up(0.07 * 1 * 0.50 * 0.50) = round_up(0.0175) = $0.02
        fee = kalshi_fee(Decimal("0.50"))
        assert fee == Decimal("0.02")

    def test_fee_at_extremes(self):
        # Fee is lowest at extremes
        fee_10 = kalshi_fee(Decimal("0.10"))
        fee_50 = kalshi_fee(Decimal("0.50"))
        assert fee_10 < fee_50

    def test_fee_symmetric(self):
        assert kalshi_fee(Decimal("0.30")) == kalshi_fee(Decimal("0.70"))

    def test_fee_matches_kalshi_table(self):
        # Verify against official Kalshi fee table (1 contract)
        assert kalshi_fee(Decimal("0.01")) == Decimal("0.01")
        assert kalshi_fee(Decimal("0.05")) == Decimal("0.01")
        assert kalshi_fee(Decimal("0.10")) == Decimal("0.01")
        assert kalshi_fee(Decimal("0.20")) == Decimal("0.02")
        assert kalshi_fee(Decimal("0.50")) == Decimal("0.02")
        assert kalshi_fee(Decimal("0.80")) == Decimal("0.02")
        assert kalshi_fee(Decimal("0.90")) == Decimal("0.01")
        assert kalshi_fee(Decimal("0.95")) == Decimal("0.01")
        assert kalshi_fee(Decimal("0.99")) == Decimal("0.01")

    def test_fee_100_contracts_matches_table(self):
        # Verify against official Kalshi fee table (100 contracts)
        assert kalshi_fee(Decimal("0.50"), 100) == Decimal("1.75")
        assert kalshi_fee(Decimal("0.90"), 100) == Decimal("0.63")


class TestPositionSizerFixed:
    def test_fixed_returns_fixed_size(self):
        sizer = PositionSizer(mode=SizingMode.FIXED, fixed_size=10)
        size = sizer.compute(Decimal("0.50"), 0.8, Decimal("500"))
        assert size == 10

    def test_fixed_capped_by_balance(self):
        sizer = PositionSizer(mode=SizingMode.FIXED, fixed_size=100)
        # Balance only $5, at $0.50+fee can afford ~9
        size = sizer.compute(Decimal("0.50"), 0.8, Decimal("5"))
        assert size > 0
        assert size < 100

    def test_fixed_zero_balance(self):
        sizer = PositionSizer(mode=SizingMode.FIXED, fixed_size=10)
        size = sizer.compute(Decimal("0.50"), 0.8, Decimal("0"))
        assert size == 0


class TestPositionSizerKelly:
    def test_kelly_positive_edge(self):
        sizer = PositionSizer(mode=SizingMode.KELLY)
        # 80% win rate at $0.50 -> strong positive edge
        size = sizer.compute(Decimal("0.50"), 0.80, Decimal("500"))
        assert size > 0

    def test_kelly_no_edge(self):
        sizer = PositionSizer(mode=SizingMode.KELLY)
        # 50% win rate at $0.50 -> no edge after fees
        size = sizer.compute(Decimal("0.50"), 0.50, Decimal("500"))
        assert size == 0

    def test_fractional_kelly_smaller_than_full(self):
        full = PositionSizer(mode=SizingMode.KELLY)
        frac = PositionSizer(
            mode=SizingMode.FRACTIONAL_KELLY, kelly_fraction=0.25,
        )
        full_size = full.compute(Decimal("0.40"), 0.80, Decimal("500"))
        frac_size = frac.compute(Decimal("0.40"), 0.80, Decimal("500"))
        assert frac_size <= full_size
        assert frac_size > 0

    def test_kelly_zero_confidence(self):
        sizer = PositionSizer(mode=SizingMode.KELLY)
        size = sizer.compute(Decimal("0.50"), 0.0, Decimal("500"))
        assert size == 0

    def test_kelly_extreme_price(self):
        sizer = PositionSizer(mode=SizingMode.KELLY)
        # Price at $0.99 -> very small net win
        size = sizer.compute(Decimal("0.99"), 0.99, Decimal("500"))
        assert size >= 0  # Should not crash

    def test_kelly_high_confidence_high_price(self):
        # Spot distance scenario: 97% confidence at $0.90
        sizer = PositionSizer(
            mode=SizingMode.FRACTIONAL_KELLY, kelly_fraction=0.25,
        )
        size = sizer.compute(Decimal("0.90"), 0.97, Decimal("500"))
        assert size > 0  # Should trade, not reject

    def test_kelly_scales_with_balance(self):
        sizer = PositionSizer(
            mode=SizingMode.FRACTIONAL_KELLY, kelly_fraction=0.25,
        )
        small = sizer.compute(Decimal("0.90"), 0.97, Decimal("100"))
        large = sizer.compute(Decimal("0.90"), 0.97, Decimal("1000"))
        assert large > small
