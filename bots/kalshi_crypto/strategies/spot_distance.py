# Concrete strategy. Trades when spot is far from strike.

"""Spot-distance strategy: trade when spot is far from strike.

v2 — Mar 10, 2026. Changes based on 644-round tick-accurate backtest:
- Window: T+250-500 (from T+300-540). Earlier entry captures cheaper prices
  with comparable accuracy. EV/contract +$0.065 vs +$0.049.
- Threshold: 0.15% (from 0.2%). Lower WR (83% vs 87%) but cheaper avg price
  ($0.76 vs $0.80) = higher EV/contract. More trades for compounding.
- Confidence: 0.88 (unchanged).
- SOL dropped (in main.py) — worst coin by WR (79%), drags overall edge.
- Kelly fraction bumped to 30% (in main.py) — captures more of validated edge.
"""

from __future__ import annotations

from decimal import Decimal

from bots.kalshi_crypto.strategy import BaseStrategy, RoundContext, TradeSignal
from shared.types import OrderRequest, Outcome, PriceUpdate, Side

DIST_THRESHOLD = Decimal("0.0015")  # 0.15% minimum distance
WINDOW_START = 250  # seconds into round
WINDOW_END = 500


class SpotDistanceStrategy(BaseStrategy):
    """Buy YES/NO when spot is >0.15% from strike in the T+250-500 window."""

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
