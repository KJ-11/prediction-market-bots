"""Collect Polymarket short-duration crypto up/down market data.

Discovers active crypto rounds by constructing predictable slugs
({coin}-updown-{duration}-{unix_timestamp}), subscribes to WS for
order book data, uses Coinbase spot feed for crypto prices, writes
CSV snapshots every ~1 second.

Output: data/rounds/polymarket/{COIN}-{DURATION}-YYYY-MM-DD.csv

Usage:
    python scripts/collect_polymarket.py --coin btc --duration 5m
    python scripts/collect_polymarket.py --coin eth --duration 15m --hours 8760
    python scripts/collect_polymarket.py --coin all --duration 5m
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.clients.polymarket import PolymarketClient
from shared.ws.polymarket import (
    PolymarketBookUpdate,
    PolymarketCryptoPrice,
    PolymarketMarketResolved,
    PolymarketMarketWSFeed,
    PolymarketRTDSFeed,
    PolymarketTradeUpdate,
)
from shared.ws.spot import KrakenWSFeed, SpotPriceUpdate, SpotWSFeed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("poly-collector")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("shared.ws.polymarket").setLevel(logging.WARNING)
logging.getLogger("shared.ws.spot").setLevel(logging.WARNING)

SNAPSHOT_INTERVAL = 1.0
DISCOVERY_POLL = 2.0

DURATIONS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
ALL_COINS = ["btc", "eth", "sol", "xrp"]

CSV_HEADERS = [
    "timestamp",
    "slug",
    "condition_id",
    "coin",
    "duration",
    "end_date",
    "seconds_remaining",
    "up_token_id",
    "down_token_id",
    "up_bid",
    "up_ask",
    "down_bid",
    "down_ask",
    "up_midpoint",
    "spread",
    "last_trade_price",
    "last_trade_side",
    "spot_price",      # Coinbase price for this coin
    "kraken_price",    # Kraken price for cross-validation
    "rtds_price",      # Polymarket RTDS (Binance) price
    "volume",
    "row_type",        # snapshot | round_end
    "outcome",         # up | down | unknown (only on round_end)
]


class RoundCollector:
    """Writes CSV snapshots for one coin+duration series."""

    def __init__(self, coin: str, duration: str, data_dir: str = "data/rounds/polymarket") -> None:
        self._coin = coin.upper()
        self._duration = duration
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = ""
        self._writer: csv.writer | None = None
        self._file = None
        self._rounds_collected = 0
        self._rows_written = 0

    def _ensure_file(self) -> csv.writer:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            self._close()
            self._current_date = today
            path = self._data_dir / f"{self._coin}-{self._duration}-{today}.csv"
            is_new = not path.exists()
            self._file = open(path, "a", newline="")
            self._writer = csv.writer(self._file)
            if is_new:
                self._writer.writerow(CSV_HEADERS)
                logger.info("Created %s", path)
        assert self._writer is not None
        return self._writer

    def write_snapshot(
        self,
        slug: str,
        condition_id: str,
        end_date: str,
        seconds_remaining: float,
        up_token_id: str,
        down_token_id: str,
        up_bid: Decimal | None,
        up_ask: Decimal | None,
        down_bid: Decimal | None,
        down_ask: Decimal | None,
        last_trade_price: Decimal | None,
        last_trade_side: str,
        spot_price: Decimal | None,
        kraken_price: Decimal | None,
        rtds_price: Decimal | None,
        volume: str,
    ) -> None:
        writer = self._ensure_file()

        up_mid = ""
        spread = ""
        if up_bid is not None and up_ask is not None:
            mid = (up_bid + up_ask) / 2
            up_mid = str(mid)
            spread = str(up_ask - up_bid)

        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            slug,
            condition_id,
            self._coin,
            self._duration,
            end_date,
            f"{seconds_remaining:.1f}",
            up_token_id,
            down_token_id,
            str(up_bid) if up_bid is not None else "",
            str(up_ask) if up_ask is not None else "",
            str(down_bid) if down_bid is not None else "",
            str(down_ask) if down_ask is not None else "",
            up_mid,
            spread,
            str(last_trade_price) if last_trade_price is not None else "",
            last_trade_side,
            str(spot_price) if spot_price is not None else "",
            str(kraken_price) if kraken_price is not None else "",
            str(rtds_price) if rtds_price is not None else "",
            volume,
            "snapshot",
            "",
        ])
        self._rows_written += 1
        if self._file:
            self._file.flush()

    def write_round_end(
        self,
        slug: str,
        condition_id: str,
        end_date: str,
        up_token_id: str,
        down_token_id: str,
        up_bid: Decimal | None,
        up_ask: Decimal | None,
        spot_price: Decimal | None,
        kraken_price: Decimal | None,
        rtds_price: Decimal | None,
        volume: str,
        outcome: str,
    ) -> None:
        writer = self._ensure_file()
        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            slug,
            condition_id,
            self._coin,
            self._duration,
            end_date,
            "0.0",
            up_token_id,
            down_token_id,
            str(up_bid) if up_bid is not None else "",
            str(up_ask) if up_ask is not None else "",
            "", "",  # down_bid, down_ask
            "", "",  # midpoint, spread
            "", "",  # last_trade_price, side
            str(spot_price) if spot_price is not None else "",
            str(kraken_price) if kraken_price is not None else "",
            str(rtds_price) if rtds_price is not None else "",
            volume,
            "round_end",
            outcome,
        ])
        self._rounds_collected += 1
        if self._file:
            self._file.flush()
        logger.info(
            "Round %d done: %s outcome=%s",
            self._rounds_collected, slug, outcome,
        )

    def _close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
            self._writer = None

    def close(self) -> None:
        self._close()
        logger.info(
            "Collector done: %d rounds, %d rows",
            self._rounds_collected, self._rows_written,
        )


def _extract_tokens(market: dict) -> tuple[str, str]:
    """Extract (up_token_id, down_token_id) from market dict."""
    clob = market.get("clobTokenIds", "")
    if isinstance(clob, str) and clob:
        parts = [p.strip().strip('"') for p in clob.strip("[]").split(",")]
        if len(parts) >= 2:
            return parts[0], parts[1]
    tokens = market.get("tokens", [])
    if len(tokens) >= 2:
        return tokens[0].get("token_id", ""), tokens[1].get("token_id", "")
    return "", ""


def _seconds_until(iso_str: str) -> float:
    """Seconds until an ISO 8601 timestamp."""
    if not iso_str:
        return -1.0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds()
    except ValueError:
        return -1.0



def _find_last_slug(coin: str, duration: str, data_dir: str = "data/rounds/polymarket") -> str:
    """Find last completed round slug from today's CSV."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = Path(data_dir) / f"{coin.upper()}-{duration}-{today}.csv"
    if not path.exists():
        return ""
    try:
        last_slug = ""
        with open(path) as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 23 and row[21] == "round_end":
                    last_slug = row[1]
        if last_slug:
            logger.info("Resuming after last completed round: %s", last_slug)
        return last_slug
    except Exception as e:
        logger.warning("Could not read last round from CSV: %s", e)
        return ""


async def collect(coin: str, duration: str, hours: float) -> None:
    interval_s = DURATIONS[duration]
    client = PolymarketClient()

    # Coinbase spot feed (primary)
    spot_queue: asyncio.Queue[SpotPriceUpdate] = asyncio.Queue(maxsize=10000)
    spot_feed = SpotWSFeed(coins=[coin.upper()], price_queue=spot_queue)
    await spot_feed.start()

    # Kraken spot feed (cross-validation, CF Benchmarks constituent)
    kraken_queue: asyncio.Queue[SpotPriceUpdate] = asyncio.Queue(maxsize=10000)
    kraken_feed = KrakenWSFeed(coins=[coin.upper()], price_queue=kraken_queue)
    await kraken_feed.start()

    # Polymarket RTDS feed (Binance prices — PM resolution proxy via Chainlink)
    rtds_queue: asyncio.Queue[PolymarketCryptoPrice] = asyncio.Queue(maxsize=10000)
    rtds_feed = PolymarketRTDSFeed(price_queue=rtds_queue)
    await rtds_feed.start()

    collector = RoundCollector(coin, duration)

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    deadline = time.monotonic() + hours * 3600
    latest_spot: Decimal | None = None
    latest_kraken: Decimal | None = None
    latest_rtds: Decimal | None = None
    rtds_symbol = f"{coin.lower()}usdt"  # RTDS uses btcusdt, ethusdt, etc.
    last_slug = _find_last_slug(coin, duration)

    logger.info("Starting: coin=%s duration=%s hours=%.1f", coin.upper(), duration, hours)

    try:
        while not shutdown.is_set() and time.monotonic() < deadline:
            # Drain spot queues
            while True:
                try:
                    su = spot_queue.get_nowait()
                    latest_spot = su.price
                except asyncio.QueueEmpty:
                    break
            while True:
                try:
                    ku = kraken_queue.get_nowait()
                    latest_kraken = ku.price
                except asyncio.QueueEmpty:
                    break
            while True:
                try:
                    ru = rtds_queue.get_nowait()
                    if ru.symbol == rtds_symbol:
                        latest_rtds = ru.price
                except asyncio.QueueEmpty:
                    break

            # Discover current round
            event = await client.discover_crypto_round(coin, duration)
            if not event or not event.get("markets"):
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=DISCOVERY_POLL)
                except asyncio.TimeoutError:
                    pass
                continue

            market = event["markets"][0]
            if not market.get("active") or market.get("closed"):
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=DISCOVERY_POLL)
                except asyncio.TimeoutError:
                    pass
                continue

            slug = market.get("slug", "")
            condition_id = market.get("conditionId", "")
            end_date = market.get("endDate", "")
            remaining = _seconds_until(end_date)
            volume = str(market.get("volume", ""))

            if remaining <= 5:
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=DISCOVERY_POLL)
                except asyncio.TimeoutError:
                    pass
                continue

            if slug == last_slug:
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=DISCOVERY_POLL)
                except asyncio.TimeoutError:
                    pass
                continue

            last_slug = slug
            up_token_id, down_token_id = _extract_tokens(market)
            if not up_token_id or not down_token_id:
                logger.warning("No token IDs for %s", slug)
                continue

            logger.info(
                "Round: %s remaining=%.0fs up_price=%s",
                slug, remaining, market.get("outcomePrices", ""),
            )

            # Start market WS
            book_queue: asyncio.Queue[PolymarketBookUpdate] = asyncio.Queue(maxsize=10000)
            trade_queue: asyncio.Queue[PolymarketTradeUpdate] = asyncio.Queue(maxsize=10000)
            resolution_queue: asyncio.Queue[PolymarketMarketResolved] = asyncio.Queue(maxsize=100)
            market_ws = PolymarketMarketWSFeed(
                asset_ids=[up_token_id, down_token_id],
                book_queue=book_queue,
                trade_queue=trade_queue,
                resolution_queue=resolution_queue,
            )
            await market_ws.start()

            best_up_bid: Decimal | None = None
            best_up_ask: Decimal | None = None
            best_down_bid: Decimal | None = None
            best_down_ask: Decimal | None = None
            last_trade_price: Decimal | None = None
            last_trade_side: str = ""
            last_snapshot = 0.0

            try:
                while _seconds_until(end_date) > 1 and not shutdown.is_set():
                    # Drain spot queues
                    while True:
                        try:
                            su = spot_queue.get_nowait()
                            latest_spot = su.price
                        except asyncio.QueueEmpty:
                            break
                    while True:
                        try:
                            ku = kraken_queue.get_nowait()
                            latest_kraken = ku.price
                        except asyncio.QueueEmpty:
                            break
                    while True:
                        try:
                            ru = rtds_queue.get_nowait()
                            if ru.symbol == rtds_symbol:
                                latest_rtds = ru.price
                        except asyncio.QueueEmpty:
                            break

                    # Drain book queue
                    while True:
                        try:
                            bu = book_queue.get_nowait()
                            if bu.asset_id == up_token_id or not bu.asset_id:
                                if bu.best_bid is not None:
                                    best_up_bid = bu.best_bid
                                if bu.best_ask is not None:
                                    best_up_ask = bu.best_ask
                            if bu.asset_id == down_token_id:
                                if bu.best_bid is not None:
                                    best_down_bid = bu.best_bid
                                if bu.best_ask is not None:
                                    best_down_ask = bu.best_ask
                            # Derive from complement
                            if best_down_bid is None and best_up_ask is not None:
                                best_down_bid = Decimal("1") - best_up_ask
                            if best_down_ask is None and best_up_bid is not None:
                                best_down_ask = Decimal("1") - best_up_bid
                        except asyncio.QueueEmpty:
                            break

                    # Drain trade queue
                    while True:
                        try:
                            tu = trade_queue.get_nowait()
                            if tu.asset_id == up_token_id:
                                last_trade_price = tu.price
                                last_trade_side = tu.side
                        except asyncio.QueueEmpty:
                            break

                    # Write snapshot
                    now_mono = time.monotonic()
                    if now_mono - last_snapshot >= SNAPSHOT_INTERVAL:
                        remaining = _seconds_until(end_date)
                        collector.write_snapshot(
                            slug=slug,
                            condition_id=condition_id,
                            end_date=end_date,
                            seconds_remaining=remaining,
                            up_token_id=up_token_id,
                            down_token_id=down_token_id,
                            up_bid=best_up_bid,
                            up_ask=best_up_ask,
                            down_bid=best_down_bid,
                            down_ask=best_down_ask,
                            last_trade_price=last_trade_price,
                            last_trade_side=last_trade_side,
                            spot_price=latest_spot,
                            kraken_price=latest_kraken,
                            rtds_price=latest_rtds,
                            volume=volume,
                        )
                        last_snapshot = now_mono

                    await asyncio.sleep(0.1)

            finally:
                # Round end — determine outcome
                # Priority: WS market_resolved > last trade price > bid/ask
                outcome = "unknown"

                # Check for WS resolution event
                while True:
                    try:
                        res = resolution_queue.get_nowait()
                        if res.outcome:
                            outcome = res.outcome
                            logger.info("Got market_resolved: %s", outcome)
                    except asyncio.QueueEmpty:
                        break

                # Fallback to price-based inference
                if outcome == "unknown":
                    if last_trade_price is not None and last_trade_price >= Decimal("0.90"):
                        outcome = "up"
                    elif last_trade_price is not None and last_trade_price <= Decimal("0.10"):
                        outcome = "down"
                    elif best_up_bid is not None and best_up_bid >= Decimal("0.90"):
                        outcome = "up"
                    elif best_up_ask is not None and best_up_ask <= Decimal("0.10"):
                        outcome = "down"

                collector.write_round_end(
                    slug=slug,
                    condition_id=condition_id,
                    end_date=end_date,
                    up_token_id=up_token_id,
                    down_token_id=down_token_id,
                    up_bid=best_up_bid,
                    up_ask=best_up_ask,
                    spot_price=latest_spot,
                    kraken_price=latest_kraken,
                    rtds_price=latest_rtds,
                    volume=volume,
                    outcome=outcome,
                )

                await market_ws.stop()

    finally:
        collector.close()
        await spot_feed.stop()
        await kraken_feed.stop()
        await rtds_feed.stop()
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect Polymarket crypto up/down market data"
    )
    parser.add_argument(
        "--coin", default="btc",
        help="Coin to collect (btc, eth, sol, xrp, or 'all')",
    )
    parser.add_argument(
        "--duration", default="5m",
        choices=list(DURATIONS.keys()),
        help="Market duration (5m, 15m, 1h, 4h)",
    )
    parser.add_argument(
        "--hours", type=float, default=24,
        help="Hours to collect",
    )
    args = parser.parse_args()

    if args.coin == "all":
        # Run all coins concurrently
        async def collect_all() -> None:
            tasks = [
                collect(c, args.duration, args.hours)
                for c in ALL_COINS
            ]
            await asyncio.gather(*tasks)
        asyncio.run(collect_all())
    else:
        asyncio.run(collect(args.coin, args.duration, args.hours))


if __name__ == "__main__":
    main()
