"""Tests for SpotDistanceStrategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bots.kalshi_crypto.strategies.spot_distance import SpotDistanceStrategy
from bots.kalshi_crypto.strategy import RoundContext
from shared.types import Outcome, PriceUpdate


def _make_ctx(
    series: str = "KXBTC15M",
    coin: str = "BTC",
    seconds_elapsed: float = 300,
    total_duration: float = 900,
    strike: Decimal = Decimal("70000"),
) -> RoundContext:
    now = datetime.now(timezone.utc)
    return RoundContext(
        ticker=f"{series}-TEST-001",
        series=series,
        coin=coin,
        floor_strike=strike,
        open_time=now - timedelta(seconds=seconds_elapsed),
        close_time=now + timedelta(seconds=total_duration - seconds_elapsed),
    )


def _kalshi_update(
    ticker: str,
    yes_bid: str = "0.50",
    yes_ask: str = "0.52",
) -> PriceUpdate:
    yb = Decimal(yes_bid)
    ya = Decimal(yes_ask)
    return PriceUpdate(
        market_id=ticker,
        yes_price=yb,
        no_price=Decimal("1") - yb,
        yes_bid=yb,
        yes_ask=ya,
        no_bid=Decimal("1") - ya,
        no_ask=Decimal("1") - yb,
    )


class TestSpotDistanceStrategy:
    def test_no_signal_before_window(self):
        """No signals before T+300s."""
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=200)  # T+200
        strat.on_round_start(ctx)

        signals = strat.on_update(
            ctx,
            _kalshi_update(ctx.ticker, "0.90", "0.92"),
            Decimal("71000"),  # 1.4% above strike
        )
        assert signals == []

    def test_no_signal_after_window(self):
        """No signals after T+540s."""
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=600)
        strat.on_round_start(ctx)

        signals = strat.on_update(
            ctx,
            _kalshi_update(ctx.ticker, "0.90", "0.92"),
            Decimal("71000"),
        )
        assert signals == []

    def test_signal_yes_in_window(self):
        """YES signal when spot > strike by >0.2% in T+300-540."""
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400, strike=Decimal("70000"))
        strat.on_round_start(ctx)

        signals = strat.on_update(
            ctx,
            _kalshi_update(ctx.ticker, "0.90", "0.92"),
            Decimal("70500"),  # 0.71% above strike
        )
        assert len(signals) == 1
        assert signals[0].order.outcome == Outcome.YES
        assert signals[0].confidence == 0.88

    def test_signal_no_in_window(self):
        """NO signal when spot < strike by >0.2% in T+300-540."""
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400, strike=Decimal("70000"))
        strat.on_round_start(ctx)

        signals = strat.on_update(
            ctx,
            _kalshi_update(ctx.ticker, "0.10", "0.12"),
            Decimal("69500"),  # 0.71% below strike
        )
        assert len(signals) == 1
        assert signals[0].order.outcome == Outcome.NO

    def test_no_signal_below_threshold(self):
        """No signal when distance < 0.2%."""
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400, strike=Decimal("70000"))
        strat.on_round_start(ctx)

        signals = strat.on_update(
            ctx,
            _kalshi_update(ctx.ticker, "0.50", "0.52"),
            Decimal("70100"),  # 0.14% — below threshold
        )
        assert signals == []

    def test_one_trade_per_round(self):
        """Only one trade per round."""
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400, strike=Decimal("70000"))
        strat.on_round_start(ctx)

        s1 = strat.on_update(
            ctx,
            _kalshi_update(ctx.ticker, "0.90", "0.92"),
            Decimal("70500"),
        )
        s2 = strat.on_update(
            ctx,
            _kalshi_update(ctx.ticker, "0.90", "0.92"),
            Decimal("70500"),
        )
        assert len(s1) == 1
        assert len(s2) == 0

    def test_round_reset(self):
        """State resets between rounds."""
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400, strike=Decimal("70000"))

        strat.on_round_start(ctx)
        strat.on_update(
            ctx,
            _kalshi_update(ctx.ticker, "0.90", "0.92"),
            Decimal("70500"),
        )
        strat.on_round_end()

        # New round — should trade again
        strat.on_round_start(ctx)
        assert not strat._traded

    def test_no_signal_without_strike(self):
        """No signal when floor_strike is None."""
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400)
        ctx.floor_strike = None
        strat.on_round_start(ctx)

        signals = strat.on_update(
            ctx,
            _kalshi_update(ctx.ticker, "0.90", "0.92"),
            Decimal("70500"),
        )
        assert signals == []

    def test_no_signal_without_spot(self):
        """No signal when spot price is None."""
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400)
        strat.on_round_start(ctx)

        signals = strat.on_update(
            ctx,
            _kalshi_update(ctx.ticker, "0.90", "0.92"),
            None,
        )
        assert signals == []

    def test_no_signal_without_kalshi(self):
        """No signal when kalshi update is None."""
        strat = SpotDistanceStrategy()
        ctx = _make_ctx(seconds_elapsed=400)
        strat.on_round_start(ctx)

        signals = strat.on_update(ctx, None, Decimal("70500"))
        assert signals == []
