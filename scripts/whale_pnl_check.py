"""Compare bot vs manual P&L from Kalshi API for the last N days.

Bot tickers are read from /tmp/bot_tickers.txt (extracted from VM trade CSVs).
P&L = revenue - (yes_total_cost_dollars + no_total_cost_dollars) - fee_cost.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.clients.kalshi import KalshiClient
from shared.config import Settings


def _parse_ts(ts: str) -> datetime:
    ts = ts.replace("Z", "+00:00")
    ts = re.sub(r"\.\d+", "", ts)
    return datetime.fromisoformat(ts)


def _settlement_pnl(s: dict) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return (revenue, cost, fee, pnl) all in dollars."""
    revenue = Decimal(s.get("revenue", 0)) / 100
    yes_cost = Decimal(s.get("yes_total_cost_dollars", "0"))
    no_cost = Decimal(s.get("no_total_cost_dollars", "0"))
    cost = yes_cost + no_cost
    fee = Decimal(s.get("fee_cost", "0"))
    pnl = revenue - cost - fee
    return revenue, cost, fee, pnl


async def main():
    days = 7
    bot_ticker_file = Path("/tmp/bot_tickers.txt")
    bot_tickers = set(bot_ticker_file.read_text().split())
    print(f"Loaded {len(bot_tickers)} bot tickers")

    settings = Settings()
    client = KalshiClient(
        api_key_id=settings.kalshi_api_key_id,
        private_key_pem=settings.kalshi_private_key,
    )

    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Fetch settlements (paginate until older than `since`)
    settlements = []
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = await client._get("/portfolio/settlements", params=params)
        data = resp.json()
        page = data.get("settlements", [])
        if not page:
            break
        stop = False
        for s in page:
            ts = _parse_ts(s["settled_time"])
            if ts < since:
                stop = True
                continue
            settlements.append(s)
        cursor = data.get("cursor")
        if stop or not cursor:
            break
    print(f"Got {len(settlements)} settlements in last {days}d")

    # Categorize and tally
    bot = []
    manual = []
    for s in settlements:
        rev, cost, fee, pnl = _settlement_pnl(s)
        # Skip rows with zero cost AND zero revenue (no actual position)
        if cost == 0 and rev == 0:
            continue
        row = {
            "ticker": s["ticker"],
            "result": s["market_result"],
            "rev": rev,
            "cost": cost,
            "fee": fee,
            "pnl": pnl,
            "yes_count": Decimal(s.get("yes_count_fp", "0")),
            "no_count": Decimal(s.get("no_count_fp", "0")),
        }
        if s["ticker"] in bot_tickers:
            bot.append(row)
        else:
            manual.append(row)

    def summarize(rows, label):
        if not rows:
            print(f"\n{label}: no settlements")
            return
        wins = [r for r in rows if r["pnl"] > 0]
        losses = [r for r in rows if r["pnl"] <= 0]
        total_pnl = sum((r["pnl"] for r in rows), Decimal(0))
        total_cost = sum((r["cost"] for r in rows), Decimal(0))
        total_fee = sum((r["fee"] for r in rows), Decimal(0))
        print(f"\n=== {label} ===")
        print(f"Settled markets: {len(rows)} ({len(wins)}W / {len(losses)}L)")
        print(f"Volume (cost basis): ${total_cost:,.2f}")
        print(f"Fees: ${total_fee:,.2f}")
        print(f"Net P&L: ${total_pnl:+,.2f}")

    summarize(bot, "BOT (whale-followed)")
    summarize(manual, "MANUAL")

    total_pnl = sum((r["pnl"] for r in bot + manual), Decimal(0))
    print(f"\n=== TOTAL last {days}d ===  ${total_pnl:+,.2f}")

    # Worst losses
    print("\nWorst MANUAL losses:")
    for r in sorted(manual, key=lambda x: x["pnl"])[:10]:
        if r["pnl"] < 0:
            print(f"  {r['ticker']}: cost=${r['cost']:.2f} rev=${r['rev']:.2f} fee=${r['fee']:.2f} → ${r['pnl']:+.2f}")

    print("\nWorst BOT losses:")
    for r in sorted(bot, key=lambda x: x["pnl"])[:10]:
        if r["pnl"] < 0:
            print(f"  {r['ticker']}: cost=${r['cost']:.2f} rev=${r['rev']:.2f} fee=${r['fee']:.2f} → ${r['pnl']:+.2f}")

    print("\nBest BOT wins:")
    for r in sorted(bot, key=lambda x: x["pnl"], reverse=True)[:10]:
        print(f"  {r['ticker']}: cost=${r['cost']:.2f} rev=${r['rev']:.2f} → ${r['pnl']:+.2f}")

    bal = await client.get_balance()
    print(f"\nKalshi balance: ${Decimal(bal['balance'])/100:.2f}  "
          f"portfolio: ${Decimal(bal['portfolio_value'])/100:.2f}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
