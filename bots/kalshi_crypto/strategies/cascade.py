"""Cascade strategy: PM 5m resolution → Kalshi 15m entry.

When a Polymarket 5-minute round resolves "up" for a coin, buy YES on the
overlapping Kalshi 15-minute market for that coin. YES-only — the NO side
is consistently negative EV due to asymmetric repricing.

Validated on 600-670 slot-1 signals per coin (Mar 8 - Apr 1, 2026):
  ETH slot-1 YES @ SR=600: 76.1% WR, +$0.035 maker EV [-0.011, +0.080]
  SOL slot-1 YES @ SR=600: 74.9% WR, +$0.040 maker EV [-0.005, +0.083]
  BTC slot-1 YES @ SR=550: 71.9% WR, +$0.010 maker EV (too weak, excluded)

Two-slot cascade (slots 1+2 both "up"):
  ETH: 95.7% WR, +$0.048 maker EV at SR=300
  Higher confidence but pricing often >$0.85 — less room for error.

Entry window: SR 550-650 (right after PM 5m slot 1 resolves, ~10 min remaining).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from bots.kalshi_crypto.strategy import BaseStrategy, RoundContext, TradeSignal
from shared.types import OrderRequest, Outcome, PriceUpdate, Side

logger = logging.getLogger(__name__)

# Coins with validated YES-only edge
ENABLED_COINS = {"ETH", "SOL"}

# Entry window: seconds remaining on the Kalshi 15m round
# Slot 1 resolves at ~600s remaining, we enter shortly after
ENTRY_SR_MIN = 540  # don't enter too late (signal decays)
ENTRY_SR_MAX = 660  # don't enter before slot 1 resolves

# Confidence from validation data (conservative: below observed WR)
# ETH: 76.1% observed → use 0.74
# SOL: 74.9% observed → use 0.73
CONFIDENCE = {
    "ETH": 0.74,
    "SOL": 0.73,
}


class CascadeStrategy(BaseStrategy):
    """Buy YES on Kalshi when PM 5m resolves 'up' for the same coin.

    Requires pm_signals dict to be updated externally by the round loop
    with PM 5m resolution outcomes. Format: {coin: "up"|"down"|None}
    """

    def __init__(self, pm_signals: dict[str, str | None]) -> None:
        """Args:
            pm_signals: Shared dict updated by round loop with PM 5m outcomes.
                        Keys are coin symbols ("ETH", "SOL"), values are
                        "up", "down", or None (not yet resolved).
        """
        self._pm_signals = pm_signals
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
        if ctx.coin not in ENABLED_COINS:
            return []

        # Check timing: only enter in the slot-1 window
        sr = ctx.seconds_remaining()
        if sr < ENTRY_SR_MIN or sr > ENTRY_SR_MAX:
            return []

        # Check PM signal
        pm_outcome = self._pm_signals.get(ctx.coin)
        if pm_outcome != "up":
            # Only trade YES when PM 5m resolved UP
            return []

        # Need Kalshi book data
        if kalshi_update is None or kalshi_update.yes_ask is None:
            return []

        price = kalshi_update.yes_ask
        # Sanity: don't buy at extreme prices
        if price > Decimal("0.90") or price < Decimal("0.10"):
            logger.info(
                "cascade: %s YES ask $%s out of range, skipping",
                ctx.coin, price,
            )
            return []

        confidence = CONFIDENCE.get(ctx.coin, 0.73)
        self._traded = True

        logger.info(
            "cascade: %s PM 5m=up → buy YES @ $%s (sr=%.0f, conf=%.2f)",
            ctx.coin, price, sr, confidence,
        )

        return [
            TradeSignal(
                order=OrderRequest(
                    market_id=ctx.ticker,
                    side=Side.BUY,
                    outcome=Outcome.YES,
                    price=price,
                    size=Decimal("1"),  # Sizer overrides
                ),
                reason=(
                    f"cascade: {ctx.coin} PM_5m=up → YES"
                    f" @ ${price} (sr={sr:.0f})"
                ),
                confidence=confidence,
            )
        ]

    def on_round_end(self) -> None:
        self._ctx = None
        self._traded = False
