"""Market discovery — find the currently active 15-min crypto market."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from bots.kalshi_crypto.strategy import RoundContext
from shared.clients.kalshi import KalshiClient

logger = logging.getLogger(__name__)

# Series ticker → coin symbol
SERIES_TO_COIN: dict[str, str] = {
    "KXBTC15M": "BTC",
    "KXETH15M": "ETH",
    "KXSOL15M": "SOL",
    "KXXRP15M": "XRP",
}

def _parse_time(iso_str: str | None) -> datetime | None:
    """Parse ISO 8601 timestamp from Kalshi API."""
    if not iso_str:
        return None
    try:
        # Handle both Z suffix and +00:00
        s = iso_str.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


async def discover_active_market(
    client: KalshiClient,
    series: str,
) -> RoundContext | None:
    """Find the currently active market for a series.

    Queries /markets with series_ticker filter. Returns a RoundContext
    for the first active market, or None if no market is active right now.
    """
    coin = SERIES_TO_COIN.get(series)
    if not coin:
        logger.error("Unknown series: %s", series)
        return None

    resp = await client._get("/markets", params={
        "series_ticker": series,
        "status": "open",
        "limit": 5,
    })
    markets = resp.json().get("markets", [])

    for mkt in markets:
        if mkt.get("status") != "active":
            continue

        ticker = mkt["ticker"]

        # floor_strike may be temporarily None early in a round (Kalshi glitch).
        # Skip the market if still missing after 90s — strategy can't run without it.
        raw_strike = mkt.get("floor_strike")
        if raw_strike is None:
            open_time = _parse_time(mkt.get("open_time"))
            elapsed = 0.0
            if open_time:
                elapsed = (datetime.now(timezone.utc) - open_time).total_seconds()
            if elapsed > 90:
                logger.warning(
                    "No floor_strike for %s after %.0fs, skipping", ticker, elapsed,
                )
            else:
                logger.debug("No floor_strike yet for %s (%.0fs elapsed), waiting", ticker, elapsed)
            continue
        try:
            strike = Decimal(str(raw_strike))
        except Exception:
            logger.warning("Could not parse floor_strike for %s: %s", ticker, raw_strike)
            continue

        open_time = _parse_time(mkt.get("open_time"))
        close_time = _parse_time(mkt.get("close_time"))

        if open_time is None or close_time is None:
            logger.warning("Could not parse times for %s", ticker)
            continue

        ctx = RoundContext(
            ticker=ticker,
            series=series,
            coin=coin,
            floor_strike=strike,
            open_time=open_time,
            close_time=close_time,
        )

        # Skip markets that are past their close time
        remaining = ctx.seconds_remaining()
        if remaining <= 0:
            logger.debug("Skipping expired market %s (%.0fs past close)", ticker, -remaining)
            continue

        logger.info(
            "Discovered market: %s strike=%s close=%s (%.0fs remaining)",
            ticker, strike, close_time.isoformat(), remaining,
        )
        return ctx

    return None


ALL_SERIES = list(SERIES_TO_COIN.keys())


async def discover_all_markets(
    client: KalshiClient,
    series_list: list[str] | None = None,
) -> dict[str, RoundContext | None]:
    """Discover active markets for all series simultaneously.

    Returns {series: RoundContext | None} for each series.
    All 4 series share the same round schedule (close at :00, :15, :30, :45).
    """
    if series_list is None:
        series_list = ALL_SERIES

    results: dict[str, RoundContext | None] = {}
    for series in series_list:
        results[series] = await discover_active_market(client, series)

    active = sum(1 for v in results.values() if v is not None)
    logger.info("Discovered %d/%d active markets", active, len(series_list))
    return results
