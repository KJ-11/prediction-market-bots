"""Tests for trading strategies — SpotDistance and Cascade."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bots.kalshi_crypto.strategies.cascade import CascadeStrategy
from bots.kalshi_crypto.strategies.spot_distance import SpotDistanceStrategy
from bots.kalshi_crypto.strategy import RoundContext
from shared.types import Outcome, PriceUpdate


def _make_ctx(
    seconds_elapsed: float = 300,
    strike: Decimal = Decimal("70000"),
) -> RoundContext:
    now = datetime.now(timezone.utc)
    return RoundContext(
        ticker="KXBTC15M-TEST-001",
        series="KXBTC15M",
        coin="BTC",
        floor_strike=strike,
        open_time=now - timedelta(seconds=seconds_elapsed),
        close_time=now + timedelta(seconds=900 - seconds_elapsed),
    )


def _kalshi_update(ticker: str, yes_bid: str = "0.50", yes_ask: str = "0.52") -> PriceUpdate:
    yb, ya = Decimal(yes_bid), Decimal(yes_ask)
    return PriceUpdate(
        market_id=ticker, yes_price=yb, no_price=Decimal("1") - yb,
        yes_bid=yb, yes_ask=ya,
        no_bid=Decimal("1") - ya, no_ask=Decimal("1") - yb,
    )


class TestSpotDistanceStrategy:
    def test_no_signal_before_window(self):
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=200)
        strat.on_round_start(ctx)
        signals = strat.on_update(ctx, _kalshi_update(ctx.ticker, "0.90", "0.92"), Decimal("71000"))
        assert signals == []

    def test_no_signal_after_window(self):
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=600)
        strat.on_round_start(ctx)
        signals = strat.on_update(ctx, _kalshi_update(ctx.ticker, "0.90", "0.92"), Decimal("71000"))
        assert signals == []

    def test_signal_yes_above_threshold(self):
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400, strike=Decimal("70000"))
        strat.on_round_start(ctx)
        signals = strat.on_update(ctx, _kalshi_update(ctx.ticker, "0.90", "0.92"), Decimal("70500"))
        assert len(signals) == 1
        assert signals[0].order.outcome == Outcome.YES

    def test_signal_no_below_threshold(self):
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400, strike=Decimal("70000"))
        strat.on_round_start(ctx)
        signals = strat.on_update(ctx, _kalshi_update(ctx.ticker, "0.10", "0.12"), Decimal("69500"))
        assert len(signals) == 1
        assert signals[0].order.outcome == Outcome.NO

    def test_no_signal_below_threshold(self):
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400, strike=Decimal("70000"))
        strat.on_round_start(ctx)
        signals = strat.on_update(ctx, _kalshi_update(ctx.ticker, "0.50", "0.52"), Decimal("70100"))
        assert signals == []

    def test_one_trade_per_round(self):
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400, strike=Decimal("70000"))
        strat.on_round_start(ctx)
        s1 = strat.on_update(ctx, _kalshi_update(ctx.ticker, "0.90", "0.92"), Decimal("70500"))
        s2 = strat.on_update(ctx, _kalshi_update(ctx.ticker, "0.90", "0.92"), Decimal("70500"))
        assert len(s1) == 1
        assert len(s2) == 0

    def test_round_reset(self):
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400, strike=Decimal("70000"))
        strat.on_round_start(ctx)
        strat.on_update(ctx, _kalshi_update(ctx.ticker, "0.90", "0.92"), Decimal("70500"))
        strat.on_round_end()
        strat.on_round_start(ctx)
        assert not strat._traded


def _make_ctx_coin(
    coin: str = "ETH",
    seconds_remaining: float = 600,
) -> RoundContext:
    """Make a RoundContext with specific coin and seconds_remaining."""
    now = datetime.now(timezone.utc)
    elapsed = 900 - seconds_remaining
    series = f"KX{coin}15M"
    return RoundContext(
        ticker=f"{series}-TEST-001",
        series=series,
        coin=coin,
        floor_strike=Decimal("3000"),
        open_time=now - timedelta(seconds=elapsed),
        close_time=now + timedelta(seconds=seconds_remaining),
    )


class TestCascadeStrategy:
    def test_signal_on_pm_up(self):
        pm_signals: dict[str, str | None] = {"ETH": "up"}
        strat = CascadeStrategy(pm_signals)
        ctx = _make_ctx_coin("ETH", seconds_remaining=600)
        strat.on_round_start(ctx)
        signals = strat.on_update(
            ctx, _kalshi_update(ctx.ticker, "0.70", "0.72"), Decimal("3050"),
        )
        assert len(signals) == 1
        assert signals[0].order.outcome == Outcome.YES

    def test_no_signal_on_pm_down(self):
        """YES-only strategy: PM 5m=down should NOT generate a signal."""
        pm_signals: dict[str, str | None] = {"ETH": "down"}
        strat = CascadeStrategy(pm_signals)
        ctx = _make_ctx_coin("ETH", seconds_remaining=600)
        strat.on_round_start(ctx)
        signals = strat.on_update(
            ctx, _kalshi_update(ctx.ticker, "0.30", "0.32"), Decimal("2950"),
        )
        assert signals == []

    def test_no_signal_before_window(self):
        """Too early — PM 5m hasn't resolved yet (SR > 660)."""
        pm_signals: dict[str, str | None] = {"ETH": "up"}
        strat = CascadeStrategy(pm_signals)
        ctx = _make_ctx_coin("ETH", seconds_remaining=700)
        strat.on_round_start(ctx)
        signals = strat.on_update(
            ctx, _kalshi_update(ctx.ticker, "0.70", "0.72"), Decimal("3050"),
        )
        assert signals == []

    def test_no_signal_after_window(self):
        """Too late — signal has decayed (SR < 540)."""
        pm_signals: dict[str, str | None] = {"ETH": "up"}
        strat = CascadeStrategy(pm_signals)
        ctx = _make_ctx_coin("ETH", seconds_remaining=500)
        strat.on_round_start(ctx)
        signals = strat.on_update(
            ctx, _kalshi_update(ctx.ticker, "0.70", "0.72"), Decimal("3050"),
        )
        assert signals == []

    def test_no_signal_for_btc(self):
        """BTC excluded — weakest signal."""
        pm_signals: dict[str, str | None] = {"BTC": "up"}
        strat = CascadeStrategy(pm_signals)
        ctx = _make_ctx_coin("BTC", seconds_remaining=600)
        strat.on_round_start(ctx)
        signals = strat.on_update(
            ctx, _kalshi_update(ctx.ticker, "0.70", "0.72"), Decimal("70500"),
        )
        assert signals == []

    def test_no_signal_when_pm_not_resolved(self):
        """PM hasn't resolved yet."""
        pm_signals: dict[str, str | None] = {"ETH": None}
        strat = CascadeStrategy(pm_signals)
        ctx = _make_ctx_coin("ETH", seconds_remaining=600)
        strat.on_round_start(ctx)
        signals = strat.on_update(
            ctx, _kalshi_update(ctx.ticker, "0.70", "0.72"), Decimal("3050"),
        )
        assert signals == []

    def test_one_trade_per_round(self):
        pm_signals: dict[str, str | None] = {"ETH": "up"}
        strat = CascadeStrategy(pm_signals)
        ctx = _make_ctx_coin("ETH", seconds_remaining=600)
        strat.on_round_start(ctx)
        s1 = strat.on_update(
            ctx, _kalshi_update(ctx.ticker, "0.70", "0.72"), Decimal("3050"),
        )
        s2 = strat.on_update(
            ctx, _kalshi_update(ctx.ticker, "0.70", "0.72"), Decimal("3050"),
        )
        assert len(s1) == 1
        assert len(s2) == 0

    def test_rejects_extreme_price(self):
        """Don't buy at $0.92 — too expensive, no edge."""
        pm_signals: dict[str, str | None] = {"ETH": "up"}
        strat = CascadeStrategy(pm_signals)
        ctx = _make_ctx_coin("ETH", seconds_remaining=600)
        strat.on_round_start(ctx)
        signals = strat.on_update(
            ctx, _kalshi_update(ctx.ticker, "0.90", "0.92"), Decimal("3050"),
        )
        assert signals == []

    def test_round_reset(self):
        pm_signals: dict[str, str | None] = {"ETH": "up"}
        strat = CascadeStrategy(pm_signals)
        ctx = _make_ctx_coin("ETH", seconds_remaining=600)
        strat.on_round_start(ctx)
        strat.on_update(
            ctx, _kalshi_update(ctx.ticker, "0.70", "0.72"), Decimal("3050"),
        )
        strat.on_round_end()
        strat.on_round_start(ctx)
        assert not strat._traded
