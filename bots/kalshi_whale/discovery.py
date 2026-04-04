# Market discovery. Find game-day markets by series ticker + event date.

"""Market discovery — fetch active Kalshi markets for whale-following.

Game-day markets (NBA games, MLB games, tennis matches, etc.) don't appear
in the generic paginated events endpoint. They must be queried by series
ticker directly. This module queries all known game-day series, parses
the event date from each market ticker, and filters to today/tomorrow.

Ticker date format: 26APR03 = 2026-04-03 (2-digit year, 3-letter month, 2-digit day).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from bots.kalshi_whale.strategy import WhaleConfig
from shared.clients.kalshi import KalshiClient

logger = logging.getLogger(__name__)

# Pattern: 2-digit year + 3-letter month + 2-digit day
_DATE_RE = re.compile(
    r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})",
    re.IGNORECASE,
)

_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Known game-day series tickers. These are the series that produce
# markets with embedded dates (YYMMMDD) that resolve same-day.
# Add new series here as Kalshi launches them.
GAME_DAY_SERIES: list[str] = [
    # Sports — game outcomes
    "KXMLBGAME", "KXNBAGAME", "KXNHLGAME", "KXIPLGAME",
    "KXUFCFIGHT", "KXWBCGAME",
    # Sports — match outcomes
    "KXATPMATCH", "KXATPCHALLENGERMATCH",
    "KXWTAMATCH", "KXWTACHALLENGERMATCH",
    # Sports — spreads/totals
    "KXNBASPREAD", "KXNCAAMBTOTAL",
    # Sports — player/game props
    "KXMLBHR", "KXMLB1HTOTAL", "KXMLBHITS", "KXMLBSTRIKEOUT",
    "KXNBA1HTOTAL", "KXNBAPLAYER", "KXNBAPLAYERPTS",
    "KXNHL1HTOTAL",
    # College sports
    "KXNCAAMBGAME", "KXNCGAME",
    # Soccer
    "KXEPLGAME", "KXLALIGAGAME", "KXLALIGA2GAME", "KXSERIEAGAME",
    "KXLIGUE1GAME", "KXUCLGAME", "KXUELGAME",
    "KXFIFAGAME", "KXINTLFRIENDLYGAME",
    # Esports
    "KXCS2GAME",
    # Economics — daily settlement
    "KXWTI", "KXINXU", "KXINXD", "KXGOLD", "KXSILVER", "KXNATGAS",
    # Crypto 15-min excluded — handled by kalshi_crypto bot
]


def parse_ticker_date(ticker: str) -> date | None:
    """Extract event date from a Kalshi ticker. Returns None if no date found."""
    m = _DATE_RE.search(ticker)
    if not m:
        return None
    year = 2000 + int(m.group(1))
    month = _MONTH_MAP[m.group(2).upper()]
    day = int(m.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


@dataclass
class WatchlistMarket:
    """A market on the watchlist — eligible for whale signals."""

    ticker: str
    event_ticker: str
    category: str
    title: str
    event_date: date


@dataclass
class Watchlist:
    """Tracks markets eligible for whale signal detection."""

    markets: dict[str, WatchlistMarket] = field(default_factory=dict)

    @property
    def tickers(self) -> list[str]:
        return list(self.markets.keys())

    def add(self, market: WatchlistMarket) -> bool:
        """Add market to watchlist. Returns True if newly added."""
        if market.ticker in self.markets:
            return False
        self.markets[market.ticker] = market
        return True

    def remove(self, ticker: str) -> None:
        self.markets.pop(ticker, None)

    def get(self, ticker: str) -> WatchlistMarket | None:
        return self.markets.get(ticker)


async def _fetch_series(
    client: KalshiClient,
    series: str,
    eligible_dates: set[date],
) -> list[WatchlistMarket]:
    """Fetch markets for a single series, filtered to eligible dates."""
    discovered: list[WatchlistMarket] = []

    try:
        resp = await client._get("/events", params={
            "series_ticker": series,
            "status": "open",
            "limit": 200,
            "with_nested_markets": "true",
        })
        events = resp.json().get("events", [])
    except Exception as e:
        logger.warning("Discovery: failed to fetch series %s: %s", series, e)
        return []

    for event in events:
        category = (event.get("category") or "").lower()
        event_ticker = event.get("event_ticker", "")

        for mkt in event.get("markets") or []:
            if mkt.get("status") != "active":
                continue

            ticker = mkt.get("ticker", "")
            event_date = parse_ticker_date(ticker)
            if event_date is None or event_date not in eligible_dates:
                continue

            title = mkt.get("title") or event.get("title") or ticker

            discovered.append(WatchlistMarket(
                ticker=ticker,
                event_ticker=event_ticker,
                category=category,
                title=title,
                event_date=event_date,
            ))

    return discovered


async def discover_markets(
    client: KalshiClient,
    config: WhaleConfig,
) -> list[WatchlistMarket]:
    """Fetch game-day markets across all known series for today/tomorrow.

    Queries each series in GAME_DAY_SERIES concurrently, parses event dates
    from tickers, and returns markets for today and tomorrow.
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)
    eligible_dates = {today, tomorrow}

    # Query series in small batches to avoid Kalshi 429 rate limits.
    # 5 concurrent requests per batch with a short pause between batches.
    batch_size = 5
    results: list[list[WatchlistMarket]] = []
    for i in range(0, len(GAME_DAY_SERIES), batch_size):
        batch = GAME_DAY_SERIES[i : i + batch_size]
        batch_results = await asyncio.gather(*[
            _fetch_series(client, series, eligible_dates)
            for series in batch
        ])
        results.extend(batch_results)
        if i + batch_size < len(GAME_DAY_SERIES):
            await asyncio.sleep(1.0)

    discovered: list[WatchlistMarket] = []
    for markets in results:
        discovered.extend(markets)

    logger.info(
        "Discovery: %d markets for %s/%s across %d series",
        len(discovered),
        today.strftime("%b %d"),
        tomorrow.strftime("%b %d"),
        len(GAME_DAY_SERIES),
    )
    return discovered
