"""Polymarket REST API client for market discovery (read-only, no auth required).

Uses the Gamma API (gamma-api.polymarket.com) for event/market discovery.

Crypto short-duration markets use predictable slugs:
    5m/15m/4h: {coin}-updown-{duration}-{unix_close_timestamp}
               e.g. btc-updown-5m-1773150900
    1h:        {fullname}-up-or-down-{month}-{day}-{year}-{hour}{am/pm}-et
               e.g. bitcoin-up-or-down-march-17-2026-12pm-et
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from shared.utils.retry import http_retry

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"


class PolymarketClient:
    """Read-only Polymarket client. No authentication required."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=30)

    async def close(self) -> None:
        await self._http.aclose()

    # ---- Gamma API (discovery) ----

    @http_retry("Polymarket-Gamma")
    async def get_event(self, event_id: str) -> dict:
        """Fetch a single event by ID."""
        resp = await self._http.get(f"{GAMMA_BASE}/events/{event_id}")
        resp.raise_for_status()
        return resp.json()

    @http_retry("Polymarket-Gamma")
    async def get_event_by_slug(self, slug: str) -> dict:
        """Fetch a single event by slug."""
        resp = await self._http.get(f"{GAMMA_BASE}/events/slug/{slug}")
        resp.raise_for_status()
        return resp.json()

    # ---- Crypto short-duration market discovery ----

    CRYPTO_COINS = ["btc", "eth", "sol", "xrp"]
    CRYPTO_DURATIONS = {
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
    }
    # 1h slugs use full coin names
    _COIN_FULLNAMES = {
        "btc": "bitcoin",
        "eth": "ethereum",
        "sol": "solana",
        "xrp": "xrp",
    }

    async def discover_crypto_round(
        self,
        coin: str,
        duration: str,
        offset_windows: int = 0,
    ) -> dict | None:
        """Discover a crypto up/down market by constructing its slug.

        Slug formats:
            5m/15m/4h: {coin}-updown-{duration}-{unix_close_timestamp}
            1h:        {fullname}-up-or-down-{month}-{day}-{year}-{hour}{am/pm}-et

        Args:
            coin: btc, eth, sol, xrp
            duration: 5m, 15m, 1h, 4h
            offset_windows: 0 = current window, 1 = next, -1 = previous

        Returns: Event dict with nested market, or None if not found.
        """
        interval = self.CRYPTO_DURATIONS.get(duration)
        if interval is None:
            logger.error("Unknown duration: %s", duration)
            return None

        if duration == "1h":
            slug = self._build_1h_slug(coin, offset_windows)
        else:
            now = int(time.time())
            window_end = ((now // interval) + 1 + offset_windows) * interval
            slug = f"{coin.lower()}-updown-{duration}-{window_end}"

        try:
            return await self.get_event_by_slug(slug)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def _build_1h_slug(self, coin: str, offset_windows: int = 0) -> str:
        """Build the human-readable slug for 1h crypto markets.

        Format: {fullname}-up-or-down-{month}-{day}-{year}-{hour}{am/pm}-et
        The hour in the slug is the *start* of the window in Eastern Time.
        """
        ET = timezone(timedelta(hours=-4))  # EDT (summer), adjust if needed
        now_et = datetime.now(ET)
        # Round down to current hour boundary, then apply offset
        window_start = now_et.replace(
            minute=0, second=0, microsecond=0,
        ) + timedelta(hours=offset_windows)
        fullname = self._COIN_FULLNAMES.get(coin.lower(), coin.lower())
        month = window_start.strftime("%B").lower()
        day = window_start.day
        year = window_start.year
        hour_12 = window_start.strftime("%I").lstrip("0")
        ampm = window_start.strftime("%p").lower()
        return f"{fullname}-up-or-down-{month}-{day}-{year}-{hour_12}{ampm}-et"

    async def discover_all_crypto_rounds(
        self,
        duration: str,
        coins: list[str] | None = None,
    ) -> list[dict]:
        """Discover current crypto rounds for all coins at a given duration.

        Returns list of event dicts (with nested markets) for active rounds.
        """
        coins = coins or self.CRYPTO_COINS
        results = []
        for coin in coins:
            # Try current and next window
            for offset in [0, 1]:
                event = await self.discover_crypto_round(coin, duration, offset)
                if event and event.get("markets"):
                    mkt = event["markets"][0]
                    if mkt.get("active") and not mkt.get("closed"):
                        results.append(event)
                        break
        return results
