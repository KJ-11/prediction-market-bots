"""Polymarket REST API client for market data (read-only, no auth required).

Two APIs:
- Gamma API (gamma-api.polymarket.com): Market discovery, events, metadata
- CLOB API (clob.polymarket.com): Order books, prices, spreads

Crypto short-duration markets use predictable slugs:
    {coin}-updown-{duration}-{unix_close_timestamp}
    e.g. btc-updown-5m-1773150900
"""

from __future__ import annotations

import logging
import time

import httpx

from shared.utils.retry import http_retry

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


class PolymarketClient:
    """Read-only Polymarket client. No authentication required."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=30)

    async def close(self) -> None:
        await self._http.aclose()

    # ---- Gamma API (discovery) ----

    @http_retry("Polymarket-Gamma")
    async def search_markets(
        self,
        query: str | None = None,
        tag: str | None = None,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Search markets via Gamma API."""
        params: dict = {"limit": limit, "offset": offset}
        if active:
            params["active"] = "true"
        if not closed:
            params["closed"] = "false"
        if tag:
            params["tag_id"] = tag

        resp = await self._http.get(f"{GAMMA_BASE}/markets", params=params)
        resp.raise_for_status()
        return resp.json()

    @http_retry("Polymarket-Gamma")
    async def search_events(
        self,
        query: str | None = None,
        tag: str | None = None,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Search events via Gamma API."""
        params: dict = {"limit": limit, "offset": offset}
        if active:
            params["active"] = "true"
        if not closed:
            params["closed"] = "false"
        if tag:
            params["tag_id"] = tag

        resp = await self._http.get(f"{GAMMA_BASE}/events", params=params)
        resp.raise_for_status()
        return resp.json()

    @http_retry("Polymarket-Gamma")
    async def get_market(self, market_id: str) -> dict:
        """Fetch a single market by condition_id."""
        resp = await self._http.get(f"{GAMMA_BASE}/markets/{market_id}")
        resp.raise_for_status()
        return resp.json()

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

    # ---- CLOB API (order books, prices) ----

    @http_retry("Polymarket-CLOB")
    async def get_order_book(self, token_id: str) -> dict:
        """Get order book for a single token.

        Returns: {"bids": [{"price": "0.65", "size": "100"}, ...], "asks": [...]}
        """
        resp = await self._http.get(
            f"{CLOB_BASE}/book", params={"token_id": token_id}
        )
        resp.raise_for_status()
        return resp.json()

    @http_retry("Polymarket-CLOB")
    async def get_midpoint(self, token_id: str) -> str | None:
        """Get midpoint price for a token. Returns price as string or None."""
        resp = await self._http.get(
            f"{CLOB_BASE}/midpoint", params={"token_id": token_id}
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("mid")

    @http_retry("Polymarket-CLOB")
    async def get_spread(self, token_id: str) -> dict:
        """Get spread for a token. Returns {"spread": "0.02"}."""
        resp = await self._http.get(
            f"{CLOB_BASE}/spread", params={"token_id": token_id}
        )
        resp.raise_for_status()
        return resp.json()

    @http_retry("Polymarket-CLOB")
    async def get_price(self, token_id: str, side: str = "BUY") -> str | None:
        """Get price for a token. side = BUY or SELL."""
        resp = await self._http.get(
            f"{CLOB_BASE}/price",
            params={"token_id": token_id, "side": side},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("price")

    @http_retry("Polymarket-CLOB")
    async def get_last_trade_price(self, token_id: str) -> dict:
        """Get last trade price for a token."""
        resp = await self._http.get(
            f"{CLOB_BASE}/last-trade-price",
            params={"token_id": token_id},
        )
        resp.raise_for_status()
        return resp.json()

    @http_retry("Polymarket-CLOB")
    async def get_prices_history(
        self, token_id: str, interval: str = "1m", fidelity: int = 60,
    ) -> list[dict]:
        """Get historical prices for a token."""
        resp = await self._http.get(
            f"{CLOB_BASE}/prices-history",
            params={
                "token_id": token_id,
                "interval": interval,
                "fidelity": fidelity,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("history", [])

    @http_retry("Polymarket-CLOB")
    async def get_tick_size(self, token_id: str) -> str | None:
        """Get minimum price increment for a token."""
        resp = await self._http.get(f"{CLOB_BASE}/tick-size/{token_id}")
        resp.raise_for_status()
        data = resp.json()
        return data.get("minimum_tick_size")

    # ---- Crypto short-duration market discovery ----

    CRYPTO_COINS = ["btc", "eth", "sol", "xrp"]
    CRYPTO_DURATIONS = {
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
    }

    async def discover_crypto_round(
        self,
        coin: str,
        duration: str,
        offset_windows: int = 0,
    ) -> dict | None:
        """Discover a crypto up/down market by constructing its slug.

        Slug format: {coin}-updown-{duration}-{unix_close_timestamp}

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

        now = int(time.time())
        window_end = ((now // interval) + 1 + offset_windows) * interval
        slug = f"{coin.lower()}-updown-{duration}-{window_end}"

        try:
            return await self.get_event_by_slug(slug)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

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
