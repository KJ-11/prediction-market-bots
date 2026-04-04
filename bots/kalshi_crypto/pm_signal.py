"""PM 5m signal poller: polls Polymarket for resolved 5-minute rounds.

Runs as an async task during each Kalshi 15-minute round. Checks if the
first 5-minute slot (slot 1) of the current 15-minute window has resolved,
and updates a shared dict with the outcome.

Timing: A Kalshi round closing at :15 has PM 5m slots at :05, :10, :15.
Slot 1 (:05) is the signal — it resolves with ~10 minutes remaining on Kalshi.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from shared.clients.polymarket import PolymarketClient

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5.0  # seconds between polls
MAX_POLLS = 30  # stop after this many attempts (~2.5 min)


def _slot1_close_timestamp(kalshi_close: datetime) -> int:
    """Compute the unix timestamp when PM 5m slot 1 closes.

    For a Kalshi round closing at time T, the 15-minute window is [T-15m, T].
    PM 5m slots within this window close at: T-10m, T-5m, T.
    Slot 1 closes at T-10m (the earliest, giving us the most time to act).
    """
    slot1_close = kalshi_close.timestamp() - 600  # T - 10 minutes
    return int(slot1_close)


async def poll_pm_5m_signal(
    pm_client: PolymarketClient,
    coin: str,
    kalshi_close: datetime,
    pm_signals: dict[str, str | None],
    stop_event: asyncio.Event | None = None,
) -> None:
    """Poll PM for the slot-1 5m outcome and update pm_signals dict.

    Args:
        pm_client: Polymarket REST client.
        coin: Coin symbol (BTC, ETH, SOL).
        kalshi_close: When the current Kalshi 15m round closes.
        pm_signals: Shared dict to update with outcome.
        stop_event: Optional event to cancel polling early.
    """
    slot1_ts = _slot1_close_timestamp(kalshi_close)
    slug = f"{coin.lower()}-updown-5m-{slot1_ts}"
    pm_signals[coin] = None

    # Don't start polling until slot 1 should have closed
    now = time.time()
    wait_until = slot1_ts + 5  # 5s buffer for resolution
    if now < wait_until:
        delay = wait_until - now
        logger.debug("pm_signal: %s waiting %.0fs for slot 1 to close", coin, delay)
        if stop_event:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                return  # stopped early
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(delay)

    for attempt in range(MAX_POLLS):
        if stop_event and stop_event.is_set():
            return

        try:
            event = await pm_client.get_event_by_slug(slug)
            if event and event.get("markets"):
                market = event["markets"][0]
                # Check if market has resolved
                if market.get("closed") or market.get("resolved"):
                    # Determine outcome from market data
                    outcome = _extract_outcome(market)
                    if outcome:
                        pm_signals[coin] = outcome
                        logger.info(
                            "pm_signal: %s slot-1 resolved → %s (slug=%s)",
                            coin, outcome, slug,
                        )
                        return
        except Exception as e:
            logger.debug("pm_signal: %s poll %d failed: %s", coin, attempt, e)

        await asyncio.sleep(POLL_INTERVAL)

    logger.warning("pm_signal: %s slot-1 not resolved after %d polls", coin, MAX_POLLS)


def _extract_outcome(market: dict) -> str | None:
    """Extract 'up' or 'down' from a resolved PM market.

    PM markets have tokens. The market outcome determines which token won.
    Check groupItemTitle, outcomePrices, or winner fields.
    """
    # Try outcomePrices (JSON string like "[\"1\",\"0\"]" for up/down tokens)
    outcome_prices = market.get("outcomePrices")
    if outcome_prices:
        try:
            import json
            prices = (
                json.loads(outcome_prices) if isinstance(outcome_prices, str)
                else outcome_prices
            )
            if len(prices) >= 2:
                # First token is "Up", second is "Down"
                if float(prices[0]) > 0.5:
                    return "up"
                elif float(prices[1]) > 0.5:
                    return "down"
        except (json.JSONDecodeError, ValueError, IndexError):
            pass

    # Try winner field
    winner = market.get("winner")
    if winner:
        # winner is the token_id that won
        tokens = market.get("tokens", [])
        for token in tokens:
            if token.get("token_id") == winner:
                outcome_str = token.get("outcome", "").lower()
                if "up" in outcome_str:
                    return "up"
                elif "down" in outcome_str:
                    return "down"

    # Try resolved_by or other fields
    resolved = market.get("resolved_by")
    if resolved:
        return resolved.lower() if resolved.lower() in ("up", "down") else None

    return None


async def start_pm_signal_tasks(
    pm_client: PolymarketClient,
    coins: list[str],
    kalshi_close: datetime,
    pm_signals: dict[str, str | None],
    stop_event: asyncio.Event,
) -> list[asyncio.Task]:
    """Start PM signal polling tasks for all coins.

    Returns list of tasks that can be cancelled on round end.
    """
    tasks = []
    for coin in coins:
        task = asyncio.create_task(
            poll_pm_5m_signal(pm_client, coin, kalshi_close, pm_signals, stop_event),
            name=f"pm-signal-{coin}",
        )
        tasks.append(task)
    return tasks
