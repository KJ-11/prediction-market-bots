"""Spot-distance strategy: trade when spot is far from strike in T+300-540.

Revised Mar 9, 2026 based on 219-round lifecycle analysis:
- Directional accuracy is ~85-92% across T+300-600, but prices ramp from
  $0.85 to $0.95 in that window. The edge is in T+300-540 where accuracy
  is high enough and prices haven't caught up yet.
- Confidence set to 0.88 (conservative estimate from observed data).
- Same-round comparison: T+300 is $0.10/contract cheaper than T+600
  with nearly identical accuracy (86% vs 88%).
"""

from __future__ import annotations

from decimal import Decimal

from bots.kalshi_crypto.strategy import BaseStrategy, RoundContext, TradeSignal
from shared.types import OrderRequest, Outcome, PriceUpdate, Side

DIST_THRESHOLD = Decimal("0.002")  # 0.2% minimum distance
WINDOW_START = 300  # seconds into round
WINDOW_END = 540


class SpotDistanceStrategy(BaseStrategy):
    """Buy YES/NO when spot is >0.2% from strike in the T+300-540 window."""

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
                if kalshi_update.yes_bid is not None:
                    price = Decimal("1") - kalshi_update.yes_bid
                else:
                    return []

        # Conservative confidence from 219-round lifecycle analysis (Mar 8-9).
        # Observed: 85-92% accuracy in T+300-540 at 0.2%+ distance.
        # Using 0.88 keeps Kelly sizing small while we validate with more data.
        confidence = 0.88
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
