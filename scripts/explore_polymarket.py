"""Explore Polymarket crypto short-duration markets.

Discovers 5m/15m/1h/4h crypto up/down markets and displays their structure.

Usage:
    python scripts/explore_polymarket.py
    python scripts/explore_polymarket.py --duration 15m
    python scripts/explore_polymarket.py --ws-test TOKEN_ID
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.clients.polymarket import PolymarketClient


async def explore(duration: str, coins: list[str]) -> None:
    client = PolymarketClient()
    try:
        print(f"Discovering {duration} crypto markets...")
        print()
        for coin in coins:
            for offset in [0, 1]:
                label = "current" if offset == 0 else "next"
                event = await client.discover_crypto_round(coin, duration, offset)
                if not event or not event.get("markets"):
                    print(f"  {coin.upper()}/{duration} ({label}): not found")
                    continue
                mkt = event["markets"][0]
                slug = mkt.get("slug", "")
                active = mkt.get("active", False)
                end = mkt.get("endDate", "")
                prices = mkt.get("outcomePrices", "")
                volume = mkt.get("volume", "")
                liq = mkt.get("liquidity", mkt.get("liquidityClob", ""))
                cid = mkt.get("conditionId", "")
                clob = mkt.get("clobTokenIds", "")
                fee = mkt.get("makerBaseFee", "")
                taker_fee = mkt.get("takerBaseFee", "")

                print(f"  {coin.upper()}/{duration} ({label}): {slug}")
                print(f"    active={active} end={end}")
                print(f"    prices={prices} vol={volume} liq={liq}")
                print(f"    conditionId={cid}")
                print(f"    clobTokenIds={clob}")
                if fee or taker_fee:
                    print(f"    makerFee={fee} takerFee={taker_fee}")
                print()
    finally:
        await client.close()


async def ws_test(token_id: str) -> None:
    """Quick test of the Polymarket market WS with a single token."""
    from shared.ws.polymarket import (
        PolymarketBookUpdate,
        PolymarketMarketWSFeed,
        PolymarketTradeUpdate,
    )

    print(f"Testing WS feed for token: {token_id}")
    print("Will print updates for 30 seconds...")
    print()

    book_queue: asyncio.Queue[PolymarketBookUpdate] = asyncio.Queue(maxsize=1000)
    trade_queue: asyncio.Queue[PolymarketTradeUpdate] = asyncio.Queue(maxsize=1000)

    feed = PolymarketMarketWSFeed(
        asset_ids=[token_id],
        book_queue=book_queue,
        trade_queue=trade_queue,
    )
    await feed.start()

    try:
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
            while True:
                try:
                    update = book_queue.get_nowait()
                    print(f"BOOK: bid={update.best_bid} ask={update.best_ask} "
                          f"spread={update.spread} asset={update.asset_id[:16]}...")
                except asyncio.QueueEmpty:
                    break
            while True:
                try:
                    trade = trade_queue.get_nowait()
                    print(f"TRADE: price={trade.price} size={trade.size} "
                          f"side={trade.side}")
                except asyncio.QueueEmpty:
                    break
            await asyncio.sleep(0.5)
    finally:
        await feed.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explore Polymarket crypto markets"
    )
    parser.add_argument(
        "--duration", default="5m",
        choices=["5m", "15m", "1h", "4h"],
    )
    parser.add_argument(
        "--coins", default="btc,eth,sol,xrp",
        help="Comma-separated coins",
    )
    parser.add_argument("--ws-test", metavar="TOKEN_ID", help="Test WS with a token ID")
    args = parser.parse_args()

    if args.ws_test:
        asyncio.run(ws_test(args.ws_test))
    else:
        coins = [c.strip() for c in args.coins.split(",")]
        asyncio.run(explore(args.duration, coins))


if __name__ == "__main__":
    main()
