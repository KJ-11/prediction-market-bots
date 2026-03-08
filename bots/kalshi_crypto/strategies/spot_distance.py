"""Spot-distance strategy: trade when spot is far from strike in T+600-800."""

from __future__ import annotations

from decimal import Decimal

from bots.kalshi_crypto.strategy import BaseStrategy, RoundContext, TradeSignal
from shared.types import OrderRequest, Outcome, PriceUpdate, Side

DIST_THRESHOLD = Decimal("0.002")  # 0.2% minimum distance
WINDOW_START = 600  # seconds
WINDOW_END = 800


class SpotDistanceStrategy(BaseStrategy):
    """Buy YES/NO when spot is >0.2% from strike in the T+600-800 window."""

    def __init__(self) -> None:
        self._ctx: RoundContext | None = None
        self._traded = False

    def on_round_start(self, ctx: RoundContext) -> None:
        self._ctx = ctx
        self._traded = False

    def on_update(
        self,
        ctx: RoundContext,
        kalshi_update: PriceUpdate | None,
        spot_price: Decimal | None,
    ) -> list[TradeSignal]:
        if self._traded:
            return []
        if spot_price is None:
            return []
        if kalshi_update is None or kalshi_update.yes_ask is None:
            return []

        elapsed = ctx.seconds_elapsed()
        if elapsed < WINDOW_START or elapsed > WINDOW_END:
            return []

        strike = ctx.floor_strike
        if not strike:
            return []

        dist = abs(spot_price - strike) / strike
        if dist < DIST_THRESHOLD:
            return []

        if spot_price > strike:
            outcome = Outcome.YES
            price = kalshi_update.yes_ask
        else:
            outcome = Outcome.NO
            price = kalshi_update.no_ask
            if price is None:
                # Kalshi: no_ask = 1 - yes_bid (or approximate from yes_ask)
                if kalshi_update.yes_bid is not None:
                    price = Decimal("1") - kalshi_update.yes_bid
                else:
                    return []

        # Empirical win rate: 98.5% across all trades passing filters
        # The filters (dist>0.2%, T+600-800) ARE the edge — once passed,
        # confidence is the empirical rate, not a function of distance.
        confidence = 0.985
        self._traded = True

        return [
            TradeSignal(
                order=OrderRequest(
                    market_id=ctx.ticker,
                    side=Side.BUY,
                    outcome=outcome,
                    price=price,
                    size=Decimal("1"),  # Sizer overrides this
                ),
                reason=(
                    f"spot_distance: {ctx.coin} dist={float(dist):.4f}"
                    f" spot={spot_price} strike={strike}"
                    f" {outcome.value}@${price}"
                ),
                confidence=confidence,
            )
        ]

    def on_round_end(self) -> None:
        self._ctx = None
        self._traded = False
