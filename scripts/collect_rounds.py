"""Collect 15-min crypto round data for strategy calibration.

Observes every round: logs spot prices, Kalshi bid/ask, and round outcome
to CSV. Designed to run unattended for 24+ hours.

Output: data/rounds/kalshi/KXBTC15M-YYYY-MM-DD.csv

Each row = one snapshot (every ~1 second):
    timestamp, round_ticker, seconds_remaining, spot_price,
    yes_bid, yes_ask, no_bid, no_ask, volume

Plus a ROUND_END summary row with the final outcome.

Usage:
    python scripts/collect_rounds.py [--series KXBTC15M] [--hours 24]
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

from bots.kalshi_crypto.discovery import SERIES_TO_COIN, discover_active_market
from shared.clients.kalshi import KalshiClient
from shared.config import Settings
from shared.types import PriceUpdate
from shared.ws.kalshi import KalshiWSManager
from shared.ws.spot import KrakenWSFeed, SpotPriceUpdate, SpotWSFeed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("collector")

# Suppress noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("shared.ws.kalshi").setLevel(logging.WARNING)
logging.getLogger("shared.ws.spot").setLevel(logging.WARNING)

SNAPSHOT_INTERVAL = 1.0  # seconds between CSV rows
DISCOVERY_POLL = 2.0  # seconds between discovery attempts (fast!)

CSV_HEADERS = [
    "timestamp",
    "round_ticker",
    "strike",
    "seconds_remaining",
    "seconds_elapsed",
    "spot_price",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "volume",
    "spot_minus_strike",
    "spot_move_pct",
    "row_type",  # "snapshot" or "round_end"
    "outcome",  # only set on round_end rows
    "kraken_spot",  # Kraken price for CF Benchmarks cross-validation
]


class RoundCollector:
    """Collects data for one series across many rounds."""

    def __init__(self, series: str, data_dir: str = "data/rounds/kalshi") -> None:
        self._series = series
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
            path = self._data_dir / f"{self._series}-{today}.csv"
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
        round_ticker: str,
        strike: Decimal,
        seconds_remaining: float,
        seconds_elapsed: float,
        spot_price: Decimal | None,
        yes_bid: Decimal | None,
        yes_ask: Decimal | None,
        no_bid: Decimal | None,
        no_ask: Decimal | None,
        volume: Decimal | None,
        kraken_spot: Decimal | None = None,
    ) -> None:
        writer = self._ensure_file()

        spot_minus = ""
        move_pct = ""
        if spot_price is not None and strike > 0:
            diff = spot_price - strike
            spot_minus = f"{diff:.2f}"
            move_pct = f"{(diff / strike) * 100:.6f}"

        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            round_ticker,
            str(strike),
            f"{seconds_remaining:.1f}",
            f"{seconds_elapsed:.1f}",
            str(spot_price) if spot_price else "",
            str(yes_bid) if yes_bid is not None else "",
            str(yes_ask) if yes_ask is not None else "",
            str(no_bid) if no_bid is not None else "",
            str(no_ask) if no_ask is not None else "",
            str(volume) if volume is not None else "",
            spot_minus,
            move_pct,
            "snapshot",
            "",
            str(kraken_spot) if kraken_spot is not None else "",
        ])
        self._rows_written += 1
        if self._file:
            self._file.flush()

    def write_round_end(
        self,
        round_ticker: str,
        strike: Decimal,
        final_spot: Decimal | None,
        yes_bid: Decimal | None,
        yes_ask: Decimal | None,
        outcome: str,
        kraken_spot: Decimal | None = None,
    ) -> None:
        writer = self._ensure_file()

        spot_minus = ""
        move_pct = ""
        if final_spot is not None and strike > 0:
            diff = final_spot - strike
            spot_minus = f"{diff:.2f}"
            move_pct = f"{(diff / strike) * 100:.6f}"

        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            round_ticker,
            str(strike),
            "0.0",
            "",
            str(final_spot) if final_spot else "",
            str(yes_bid) if yes_bid is not None else "",
            str(yes_ask) if yes_ask is not None else "",
            "",
            "",
            "",
            spot_minus,
            move_pct,
            "round_end",
            outcome,
            str(kraken_spot) if kraken_spot is not None else "",
        ])
        self._rounds_collected += 1
        if self._file:
            self._file.flush()

        logger.info(
            "Round %d done: %s outcome=%s (final_spot=%s strike=%s)",
            self._rounds_collected, round_ticker, outcome,
            final_spot, strike,
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



def _find_last_round_ticker(series: str, data_dir: str = "data/rounds/kalshi") -> str:
    """Check existing CSV for the last completed round to avoid duplicates on restart."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = Path(data_dir) / f"{series}-{today}.csv"
    if not path.exists():
        return ""
    try:
        last_ticker = ""
        with open(path) as f:
            reader = csv.reader(f)
            next(reader, None)  # skip header
            for row in reader:
                if len(row) >= 14 and row[13] == "round_end":
                    last_ticker = row[1]
        if last_ticker:
            logger.info("Resuming after last completed round: %s", last_ticker)
        return last_ticker
    except Exception as e:
        logger.warning("Could not read last round from CSV: %s", e)
        return ""


async def collect(series: str, hours: float) -> None:
    settings = Settings()
    coin = SERIES_TO_COIN.get(series)
    if not coin:
        logger.error("Unknown series: %s", series)
        return

    client = KalshiClient(
        api_key_id=settings.kalshi_api_key_id,
        private_key_pem=settings.kalshi_private_key,
    )

    # Spot feeds — Coinbase (primary) + Kraken (CF Benchmarks cross-validation)
    spot_queue: asyncio.Queue[SpotPriceUpdate] = asyncio.Queue(maxsize=10000)
    spot_feed = SpotWSFeed(coins=[coin], price_queue=spot_queue)
    await spot_feed.start()

    kraken_queue: asyncio.Queue[SpotPriceUpdate] = asyncio.Queue(maxsize=10000)
    kraken_feed = KrakenWSFeed(coins=[coin], price_queue=kraken_queue)
    await kraken_feed.start()

    # Kalshi WS — reconnects each round
    kalshi_queue: asyncio.Queue[PriceUpdate] = asyncio.Queue(maxsize=10000)
    kalshi_ws = KalshiWSManager(client, price_queue=kalshi_queue)

    collector = RoundCollector(series)

    # Shutdown handling
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    deadline = time.monotonic() + hours * 3600

    # Resume from last completed round to avoid duplicates on restart
    last_round_ticker = _find_last_round_ticker(series)

    logger.info(
        "Starting collection: series=%s coin=%s hours=%.1f",
        series, coin, hours,
    )

    try:
        while not shutdown.is_set() and time.monotonic() < deadline:
            # Discover active market — poll aggressively
            try:
                ctx = await discover_active_market(client, series)
            except Exception as e:
                logger.warning("Discovery error: %s", e)
                await asyncio.sleep(DISCOVERY_POLL)
                continue

            if ctx is None or ctx.seconds_remaining() <= 5:
                try:
                    await asyncio.wait_for(
                        shutdown.wait(), timeout=DISCOVERY_POLL
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            # Skip if we already collected this round
            if ctx.ticker == last_round_ticker:
                try:
                    await asyncio.wait_for(
                        shutdown.wait(), timeout=DISCOVERY_POLL
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            last_round_ticker = ctx.ticker

            # Observe this round
            logger.info(
                "Round: %s strike=%s remaining=%.0fs",
                ctx.ticker, ctx.floor_strike, ctx.seconds_remaining(),
            )

            # Start Kalshi WS
            kalshi_ws.set_tickers([ctx.ticker])
            kalshi_task = asyncio.create_task(kalshi_ws.start())

            latest_spot: Decimal | None = None
            latest_kraken: Decimal | None = None
            last_snapshot = 0.0

            # Track best Kalshi data from WS
            best_yes_bid: Decimal | None = None
            best_yes_ask: Decimal | None = None
            best_no_bid: Decimal | None = None
            best_no_ask: Decimal | None = None
            best_volume: Decimal | None = None

            try:
                while ctx.seconds_remaining() > 1 and not shutdown.is_set():
                    # Drain spot queue (Coinbase)
                    while True:
                        try:
                            su = spot_queue.get_nowait()
                            latest_spot = su.price
                        except asyncio.QueueEmpty:
                            break

                    # Drain Kraken queue
                    while True:
                        try:
                            ku = kraken_queue.get_nowait()
                            latest_kraken = ku.price
                        except asyncio.QueueEmpty:
                            break

                    # Drain Kalshi WS queue — update best values
                    while True:
                        try:
                            ku = kalshi_queue.get_nowait()
                            if ku.yes_bid is not None:
                                best_yes_bid = ku.yes_bid
                            if ku.yes_ask is not None:
                                best_yes_ask = ku.yes_ask
                            if ku.no_bid is not None:
                                best_no_bid = ku.no_bid
                            if ku.no_ask is not None:
                                best_no_ask = ku.no_ask
                            if ku.volume is not None:
                                best_volume = ku.volume
                        except asyncio.QueueEmpty:
                            break

                    # Write snapshot at interval
                    now = time.monotonic()
                    if now - last_snapshot >= SNAPSHOT_INTERVAL:
                        collector.write_snapshot(
                            round_ticker=ctx.ticker,
                            strike=ctx.floor_strike,
                            seconds_remaining=ctx.seconds_remaining(),
                            seconds_elapsed=ctx.seconds_elapsed(),
                            spot_price=latest_spot,
                            yes_bid=best_yes_bid,
                            yes_ask=best_yes_ask,
                            no_bid=best_no_bid,
                            no_ask=best_no_ask,
                            volume=best_volume,
                            kraken_spot=latest_kraken,
                        )
                        last_snapshot = now

                    await asyncio.sleep(0.1)

            finally:
                # Round end — fetch official result from Kalshi API
                outcome = "unknown"
                for _ in range(6):  # retry up to 30s for finalization
                    try:
                        mkt = await client.fetch_market(ctx.ticker)
                        if mkt and mkt.get("result") in ("yes", "no"):
                            outcome = mkt["result"]
                            break
                    except Exception as e:
                        logger.debug("Result fetch error: %s", e)
                    await asyncio.sleep(5)

                # Fallback to spot-based inference if API didn't return result
                if outcome == "unknown" and latest_spot is not None:
                    outcome = (
                        "yes" if latest_spot >= ctx.floor_strike else "no"
                    )

                collector.write_round_end(
                    round_ticker=ctx.ticker,
                    strike=ctx.floor_strike,
                    final_spot=latest_spot,
                    yes_bid=best_yes_bid,
                    yes_ask=best_yes_ask,
                    outcome=outcome,
                    kraken_spot=latest_kraken,
                )

                # Stop Kalshi WS
                await kalshi_ws.stop()
                kalshi_task.cancel()
                try:
                    await kalshi_task
                except asyncio.CancelledError:
                    pass

    finally:
        collector.close()
        await spot_feed.stop()
        await kraken_feed.stop()
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect 15-min crypto round data"
    )
    parser.add_argument("--series", default="KXBTC15M", help="Series ticker")
    parser.add_argument(
        "--hours", type=float, default=24, help="Hours to collect"
    )
    args = parser.parse_args()

    asyncio.run(collect(args.series, args.hours))


if __name__ == "__main__":
    main()
