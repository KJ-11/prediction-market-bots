"""Smoke test — verify Kalshi REST + WS + Telegram alerts work end-to-end.

Usage:
    python scripts/smoke_test_kalshi.py

Requires .env with:
    KALSHI_API_KEY_ID=...
    KALSHI_PRIVATE_KEY=...
    TELEGRAM_BOT_TOKEN=... (optional)
    TELEGRAM_CHAT_ID=... (optional)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.alerts.telegram import send_telegram
from shared.clients.kalshi import KalshiClient
from shared.config import Settings
from shared.types import PriceUpdate
from shared.utils.logging import setup_logging
from shared.ws.kalshi import KalshiWSManager

logger = logging.getLogger(__name__)


async def test_rest_api(client: KalshiClient) -> list[str]:
    """Test REST API — fetch events and markets. Returns list of active tickers."""
    print("\n--- REST API ---")

    # 1. Fetch balance
    try:
        balance = await client.get_balance()
        print(f"  Balance: {balance}")
    except Exception as e:
        print(f"  Balance: FAILED ({e})")

    # 2. Fetch first page of events with markets
    events, cursor = await client.fetch_events(
        status="open", with_nested_markets=True, limit=5
    )
    print(f"  Fetched {len(events)} events (first page, limit=5)")

    tickers = []
    for evt in events[:3]:
        title = evt.get("title", "?")[:60]
        markets = evt.get("markets", [])
        print(f"  Event: {title}")
        for mkt in markets[:2]:
            ticker = mkt.get("ticker", "?")
            yes_bid = mkt.get("yes_bid_dollars", "?")
            yes_ask = mkt.get("yes_ask_dollars", "?")
            vol = mkt.get("volume_24h_fp", "?")
            print(f"    {ticker}  bid={yes_bid} ask={yes_ask} vol24h={vol}")
            tickers.append(ticker)

    print(f"  Collected {len(tickers)} tickers for WS test")
    return tickers


async def test_websocket(client: KalshiClient, tickers: list[str], duration: int = 15) -> int:
    """Test WebSocket — subscribe to tickers, count updates for `duration` seconds."""
    print(f"\n--- WebSocket (listening {duration}s) ---")

    if not tickers:
        print("  No tickers to subscribe to, skipping WS test")
        return 0

    queue: asyncio.Queue[PriceUpdate] = asyncio.Queue(maxsize=10000)
    ws = KalshiWSManager(client, price_queue=queue, tickers=tickers)

    # Run WS in background, collect updates for `duration` seconds
    ws_task = asyncio.create_task(ws.start())
    update_count = 0

    try:
        end_time = asyncio.get_event_loop().time() + duration
        while asyncio.get_event_loop().time() < end_time:
            try:
                update = await asyncio.wait_for(queue.get(), timeout=1.0)
                update_count += 1
                if update_count <= 5:
                    print(
                        f"  #{update_count} {update.market_id}: "
                        f"yes_bid={update.yes_bid} yes_ask={update.yes_ask} "
                        f"last={update.last_trade_price}"
                    )
                elif update_count == 6:
                    print("  ... (suppressing further output, still counting)")
            except asyncio.TimeoutError:
                continue
    finally:
        await ws.stop()
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass

    print(f"  Received {update_count} price updates in {duration}s")
    return update_count


async def test_telegram(settings: Settings) -> bool:
    """Test Telegram alerts — raw send + AlertManager formatted messages."""
    print("\n--- Telegram ---")

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print("  Skipped (no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env)")
        return False

    from decimal import Decimal

    from shared.alerts.manager import AlertManager

    alerts = AlertManager(
        bot_name="smoke-test",
        telegram_token=settings.telegram_bot_token,
        telegram_chat_id=settings.telegram_chat_id,
    )

    # Test bot_started format
    sent = await alerts.bot_started(
        mode="PAPER", coins=["BTC", "ETH", "SOL"], balance=Decimal("50.00"),
    )
    print(f"  bot_started: {sent}")

    # Test round_summary format (with trades)
    sent2 = await alerts.round_summary(
        window="13:00\u201313:15",
        coin_lines=[
            "\u2705 ETH: YES @ $0.07 x14 \u2192 WON +$12.58",
            "\u274c BTC: NO @ $0.05 x10 \u2192 LOST -$0.50",
            "SOL: skipped (price $0.98, no edge)",
        ],
        total_signals=3,
        total_trades=2,
        pnl=Decimal("12.08"),
        balance=Decimal("62.08"),
        shadow_summary="P&L: +$12.08 | Balance: $62.08",
    )
    print(f"  round_summary: {sent2}")

    # Test file logging worked
    import glob
    logs = glob.glob("data/alerts/*.log")
    print(f"  Alert log files: {logs}")

    return sent and sent2


async def test_order_lifecycle(client: KalshiClient, ticker: str) -> bool:
    """Test order placement + cancellation with a 1-cent limit order.

    Places a YES buy at $0.01 (will never fill), verifies it appears
    in open orders, then cancels it.
    """
    print(f"\n--- Order Lifecycle ({ticker}) ---")

    # Place a 1-cent limit order (won't fill)
    order = {
        "ticker": ticker,
        "side": "yes",
        "action": "buy",
        "count": 1,
        "type": "limit",
        "yes_price": 1,  # 1 cent
    }

    try:
        result = await client.place_order(order)
        order_id = result.get("order_id", "")
        status = result.get("status", "?")
        print(f"  Placed: order_id={order_id} status={status}")

        if not order_id:
            print("  FAILED — no order_id returned")
            return False

        # Verify in open orders
        open_orders = await client.get_open_orders(ticker=ticker)
        found = any(o.get("order_id") == order_id for o in open_orders)
        print(f"  In open orders: {found} ({len(open_orders)} total)")

        # Cancel
        cancel_result = await client.cancel_order(order_id)
        print(f"  Cancelled: {cancel_result}")

        # Verify gone
        open_orders = await client.get_open_orders(ticker=ticker)
        still_there = any(o.get("order_id") == order_id for o in open_orders)
        print(f"  Verified removed: {not still_there}")

        return True

    except Exception as e:
        print(f"  FAILED: {e}")
        return False


async def main():
    setup_logging(level="INFO", fmt="human")

    print("=" * 60)
    print("KALSHI SMOKE TEST")
    print("=" * 60)

    settings = Settings()

    if not settings.kalshi_api_key_id or not settings.kalshi_private_key:
        print("ERROR: Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY in .env")
        sys.exit(1)

    client = KalshiClient(
        api_key_id=settings.kalshi_api_key_id,
        private_key_pem=settings.kalshi_private_key,
    )

    results = {}

    try:
        # 1. REST API
        tickers = await test_rest_api(client)
        results["rest"] = len(tickers) > 0

        # 2. WebSocket
        update_count = await test_websocket(client, tickers[:10], duration=15)
        results["ws"] = update_count > 0

        # 3. Telegram
        results["telegram"] = await test_telegram(settings)

        # 4. Order lifecycle (optional — uncomment to test)
        # WARNING: This places a real order (1 cent, immediately cancelled)
        # if tickers:
        #     results["orders"] = await test_order_lifecycle(client, tickers[0])

    finally:
        await client.close()

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for test, passed in results.items():
        status = "PASS" if passed else ("SKIP" if passed is None else "FAIL")
        print(f"  {test:12s} {status}")

    all_critical = results.get("rest", False) and results.get("ws", False)
    print(f"\n{'ALL CRITICAL TESTS PASSED' if all_critical else 'SOME TESTS FAILED'}")


if __name__ == "__main__":
    asyncio.run(main())
