"""Kalshi REST API client with RSA-PSS authentication.

Adapted from Profitlabs. Decoupled from Settings — accepts raw credentials.
Adds _post/_delete for order execution.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import AsyncIterator

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from shared.utils.retry import http_retry

logger = logging.getLogger(__name__)

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiClient:
    def __init__(self, api_key_id: str, private_key_pem: str) -> None:
        self._http = httpx.AsyncClient(timeout=30)
        self._key_id = api_key_id
        self._private_key = serialization.load_pem_private_key(
            private_key_pem.encode(), password=None
        )

    async def close(self) -> None:
        await self._http.aclose()

    def _sign(self, method: str, path: str) -> dict[str, str]:
        """Generate RSA-PSS auth headers. Matches official Kalshi SDK."""
        timestamp_ms = str(int(time.time() * 1000))
        msg_string = timestamp_ms + method + path
        signature = self._private_key.sign(
            msg_string.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self._key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }

    def sign_ws(self) -> dict[str, str]:
        """Generate auth headers for WebSocket handshake."""
        return self._sign("GET", "/trade-api/ws/v2")

    @http_retry("Kalshi")
    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        headers = self._sign("GET", f"/trade-api/v2{path}")
        resp = await self._http.get(f"{KALSHI_BASE}{path}", params=params, headers=headers)
        resp.raise_for_status()
        return resp

    @http_retry("Kalshi")
    async def _post(self, path: str, json_body: dict | None = None) -> httpx.Response:
        """POST with RSA-PSS auth. Body is NOT included in signature."""
        headers = self._sign("POST", f"/trade-api/v2{path}")
        headers["Content-Type"] = "application/json"
        resp = await self._http.post(
            f"{KALSHI_BASE}{path}", json=json_body, headers=headers
        )
        resp.raise_for_status()
        return resp

    @http_retry("Kalshi")
    async def _delete(self, path: str) -> httpx.Response:
        """DELETE with RSA-PSS auth."""
        headers = self._sign("DELETE", f"/trade-api/v2{path}")
        resp = await self._http.delete(f"{KALSHI_BASE}{path}", headers=headers)
        resp.raise_for_status()
        return resp

    # ---- Series ----

    async def fetch_all_series(self) -> list[dict]:
        """Fetch all series (for category/fee cache)."""
        all_series: list[dict] = []
        cursor: str | None = None
        while True:
            params: dict = {"limit": 1000}
            if cursor:
                params["cursor"] = cursor
            resp = await self._get("/series", params=params)
            data = resp.json()
            batch = data.get("series", [])
            all_series.extend(batch)
            cursor = data.get("cursor", "")
            if not cursor or not batch:
                break
        logger.info("Kalshi: fetched %d series", len(all_series))
        return all_series

    # ---- Events ----

    async def fetch_events(
        self,
        status: str = "open",
        limit: int = 200,
        cursor: str | None = None,
        with_nested_markets: bool = False,
    ) -> tuple[list[dict], str]:
        """Fetch a page of events. Returns (events, next_cursor)."""
        params: dict = {"limit": limit, "status": status}
        if with_nested_markets:
            params["with_nested_markets"] = "true"
        if cursor:
            params["cursor"] = cursor
        resp = await self._get("/events", params=params)
        data = resp.json()
        return data.get("events", []), data.get("cursor", "")

    async def fetch_all_events(
        self, status: str = "open", with_nested_markets: bool = False
    ) -> list[dict]:
        """Paginate through all events."""
        all_events: list[dict] = []
        cursor: str | None = None
        while True:
            batch, cursor = await self.fetch_events(
                status=status, cursor=cursor, with_nested_markets=with_nested_markets
            )
            all_events.extend(batch)
            if not cursor or not batch:
                break
        logger.info(
            "Kalshi: fetched %d events (nested_markets=%s)",
            len(all_events), with_nested_markets,
        )
        return all_events

    async def iter_event_pages(
        self, status: str = "open", with_nested_markets: bool = False
    ) -> AsyncIterator[list[dict]]:
        """Yield pages of events without accumulating all in memory."""
        cursor: str | None = None
        total = 0
        while True:
            batch, cursor = await self.fetch_events(
                status=status, cursor=cursor, with_nested_markets=with_nested_markets
            )
            if not batch:
                break
            total += len(batch)
            yield batch
            if not cursor:
                break
        logger.info("Kalshi: streamed %d events (nested_markets=%s)", total, with_nested_markets)

    # ---- Single-item fetches ----

    async def fetch_market(self, ticker: str) -> dict | None:
        """Fetch a single market by ticker. Returns None on 404."""
        try:
            resp = await self._get(f"/markets/{ticker}")
            return resp.json().get("market")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def fetch_event(self, event_ticker: str) -> dict | None:
        """Fetch a single event by ticker. Returns None on 404."""
        try:
            resp = await self._get(f"/events/{event_ticker}")
            return resp.json().get("event")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    # ---- Trades ----

    async def fetch_trades(
        self,
        min_ts: str | None = None,
        max_ts: str | None = None,
        limit: int = 1000,
        cursor: str | None = None,
    ) -> tuple[list[dict], str]:
        """Fetch a page of trades. Returns (trades, next_cursor)."""
        params: dict = {"limit": limit}
        if min_ts:
            params["min_ts"] = min_ts
        if max_ts:
            params["max_ts"] = max_ts
        if cursor:
            params["cursor"] = cursor
        resp = await self._get("/markets/trades", params=params)
        data = resp.json()
        return data.get("trades", []), data.get("cursor", "")

    async def fetch_trades_paginated(
        self,
        min_ts: str | None = None,
        max_ts: str | None = None,
        max_pages: int = 500,
    ) -> list[dict]:
        """Paginate through trades in a time window."""
        all_trades: list[dict] = []
        cursor: str | None = None
        pages = 0
        while pages < max_pages:
            batch, cursor = await self.fetch_trades(
                min_ts=min_ts, max_ts=max_ts, cursor=cursor
            )
            all_trades.extend(batch)
            pages += 1
            if not cursor or not batch:
                break
        if pages >= max_pages:
            logger.warning("Kalshi: hit max_pages (%d) fetching trades", max_pages)
        logger.info(
            "Kalshi: fetched %d trades in %d pages (min_ts=%s, max_ts=%s)",
            len(all_trades), pages, min_ts, max_ts,
        )
        return all_trades

    # ---- Portfolio (for execution engine) ----

    async def get_balance(self) -> dict:
        """Get account balance."""
        resp = await self._get("/portfolio/balance")
        return resp.json()

    async def get_positions(self, ticker: str | None = None) -> list[dict]:
        """Get open positions."""
        params: dict = {"limit": 1000}
        if ticker:
            params["ticker"] = ticker
        resp = await self._get("/portfolio/positions", params=params)
        return resp.json().get("market_positions", [])

    async def place_order(self, order: dict) -> dict:
        """Place an order. Returns order response dict."""
        resp = await self._post("/portfolio/orders", json_body=order)
        return resp.json().get("order", {})

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel a single order."""
        resp = await self._delete(f"/portfolio/orders/{order_id}")
        return resp.json()

    async def get_open_orders(self, ticker: str | None = None) -> list[dict]:
        """Get open orders."""
        params: dict = {"status": "resting"}
        if ticker:
            params["ticker"] = ticker
        resp = await self._get("/portfolio/orders", params=params)
        return resp.json().get("orders", [])
