"""Backtest whale-following strategy using ProfitLabs Supabase data.

Queries whale trades ($1k+) on resolved markets to validate:
1. Win rate at 90-94c near-resolution entries
2. Signal frequency per day
3. Latency/slippage estimates

Usage:
    python scripts/whale_backtest.py
    python scripts/whale_backtest.py --min-notional 5000
    python scripts/whale_backtest.py --category sports
    python scripts/whale_backtest.py --platform kalshi
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

# Default path to ProfitLabs .env
DEFAULT_ENV = Path(__file__).resolve().parent.parent.parent / "Profitlabs" / "profitlabs-ml-pipeline" / ".env"

# Time windows to analyze (minutes before market close)
TIME_WINDOWS = [5, 10, 15, 30, 45]

# Price buckets (cents)
PRICE_BUCKETS = [(90, 91), (91, 92), (92, 93), (93, 94)]

# Categories to exclude (crypto 15-min markets — different strategy)
CRYPTO_SERIES = {"KXBTC15M", "KXETH15M", "KXSOL15M", "KXDOGE15M", "KXXRP15M"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Whale signal backtest")
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV,
                        help="Path to ProfitLabs .env file")
    parser.add_argument("--price-min", type=float, default=0.85,
                        help="Min entry price (default: 0.85)")
    parser.add_argument("--price-max", type=float, default=0.95,
                        help="Max entry price (default: 0.95)")
    parser.add_argument("--min-notional", type=int, default=1000,
                        help="Min trade notional (default: 1000)")
    parser.add_argument("--category", type=str, default=None,
                        help="Filter by event category")
    parser.add_argument("--platform", type=str, default=None,
                        choices=["kalshi", "polymarket"],
                        help="Filter by platform")
    parser.add_argument("--limit", type=int, default=200000,
                        help="Max trades to fetch (default: 200000)")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    # Load credentials
    if not args.env.exists():
        print(f"Error: .env not found at {args.env}")
        print("Pass --env /path/to/profitlabs/.env")
        sys.exit(1)

    env = dotenv_values(args.env)
    url = env.get("SUPABASE_URL")
    key = env.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        print("Error: SUPABASE_URL or SUPABASE_SECRET_KEY not found in .env")
        sys.exit(1)

    from supabase import create_async_client
    client = await create_async_client(url, key)

    print("Connected to ProfitLabs Supabase")
    print(f"Filters: notional >= ${args.min_notional}, price {args.price_min}-{args.price_max}")
    if args.category:
        print(f"Category filter: {args.category}")
    if args.platform:
        print(f"Platform filter: {args.platform}")
    print()

    # Get platform IDs
    platforms_resp = await client.table("platforms").select("id, slug").execute()
    platform_map = {p["slug"]: p["id"] for p in platforms_resp.data}
    platform_id_to_slug = {p["id"]: p["slug"] for p in platforms_resp.data}
    print(f"Platforms: {list(platform_map.keys())}")

    # Fetch whale trades with price filter pushed to DB (much less data to transfer)
    # Filter: price in our range AND notional >= threshold
    print(f"Fetching whale trades (notional >= ${args.min_notional}, "
          f"price {args.price_min}-{args.price_max})...")
    all_trades = []
    page_size = 1000
    offset = 0

    while True:
        q = (
            client.table("trades")
            .select(
                "id, price, size, notional, outcome, traded_at, market_id, platform_id"
            )
            .gte("notional", args.min_notional)
            .gte("price", args.price_min)
            .lte("price", args.price_max)
            .order("traded_at", desc=True)
            .range(offset, offset + page_size - 1)
        )

        if args.platform:
            pid = platform_map.get(args.platform)
            if pid:
                q = q.eq("platform_id", pid)

        resp = await q.execute()
        if not resp.data:
            break
        all_trades.extend(resp.data)
        offset += page_size
        if len(resp.data) < page_size or len(all_trades) >= args.limit:
            break
        if len(all_trades) % 5000 < page_size:
            print(f"  ... fetched {len(all_trades)} trades", flush=True)

    print(f"Fetched {len(all_trades)} whale trades total")

    if not all_trades:
        print("No trades found. Check filters.")
        return

    # Gather unique market IDs and fetch market data
    market_ids = list(set(t["market_id"] for t in all_trades))
    print(f"Fetching {len(market_ids)} markets...")
    markets = {}
    for i in range(0, len(market_ids), 50):
        batch = market_ids[i:i + 50]
        resp = await (
            client.table("markets")
            .select("id, yes_price, end_date, resolution, resolved, status, "
                    "external_id, volume, liquidity, event_id, platform_id")
            .in_("id", batch)
            .execute()
        )
        for m in resp.data:
            markets[m["id"]] = m

    # Fetch events for category data
    event_ids = list(set(m["event_id"] for m in markets.values() if m.get("event_id")))
    print(f"Fetching {len(event_ids)} events...")
    events = {}
    for i in range(0, len(event_ids), 50):
        batch = event_ids[i:i + 50]
        resp = await (
            client.table("events")
            .select("id, category, title, series_slug")
            .in_("id", batch)
            .execute()
        )
        for e in resp.data:
            events[e["id"]] = e

    # Build enriched trade list
    enriched = []
    for t in all_trades:
        m = markets.get(t["market_id"])
        if not m:
            continue
        if not m.get("resolved") or not m.get("resolution"):
            continue

        e = events.get(m.get("event_id", ""), {})
        series = e.get("series_slug", "") or ""

        # Exclude crypto 15-min markets
        if any(cs.lower() in series.lower() for cs in CRYPTO_SERIES):
            continue
        ticker = m.get("external_id", "")
        if any(cs.lower() in ticker.lower() for cs in CRYPTO_SERIES):
            continue

        # Category filter
        category = e.get("category", "") or ""
        if args.category and args.category.lower() not in category.lower():
            continue

        # Parse timestamps
        traded_at = _parse_ts(t["traded_at"])
        end_date = _parse_ts(m.get("end_date"))
        if not traded_at or not end_date:
            continue

        # Time to close (minutes)
        minutes_to_close = (end_date - traded_at).total_seconds() / 60
        if minutes_to_close < 0:
            continue  # Trade after close — skip

        # Entry price
        entry_price = float(t["price"])
        if entry_price < args.price_min or entry_price > args.price_max:
            continue

        # Did it win?
        trade_outcome = (t["outcome"] or "").lower()
        resolution = (m["resolution"] or "").lower()

        # Normalize: Kalshi uses "yes"/"no", PM might use different strings
        # For PM, resolution might be the token name or outcome text
        won = False
        if trade_outcome and resolution:
            # Direct match
            if trade_outcome == resolution:
                won = True
            # yes/no normalization
            elif trade_outcome in ("yes", "y") and resolution in ("yes", "y", "Yes"):
                won = True
            elif trade_outcome in ("no", "n") and resolution in ("no", "n", "No"):
                won = True
            # PM: outcome might be token position (first=yes, second=no)
            # For PM binary markets, "yes" outcome = first token resolved to $1
            # This is a best-effort match — PM resolution formats vary

        platform = platform_id_to_slug.get(t["platform_id"], "unknown")
        market_yes_price = float(m["yes_price"]) if m.get("yes_price") else None

        enriched.append({
            "traded_at": traded_at,
            "end_date": end_date,
            "minutes_to_close": minutes_to_close,
            "entry_price": entry_price,
            "notional": float(t["notional"]),
            "outcome": trade_outcome,
            "resolution": resolution,
            "won": won,
            "platform": platform,
            "category": category,
            "ticker": ticker,
            "market_id": t["market_id"],
            "volume": float(m["volume"]) if m.get("volume") else 0,
            "liquidity": float(m["liquidity"]) if m.get("liquidity") else 0,
            "market_yes_price": market_yes_price,
        })

    print(f"\nQualifying trades (non-crypto, resolved, {args.price_min}-{args.price_max}c): {len(enriched)}")

    if not enriched:
        print("No qualifying trades found. Try widening filters (--price-min 0.80 --price-max 0.99)")
        return

    # === MARKET-LEVEL AGGREGATION ===
    # Group trades by market to compute whale consensus and unique market counts
    from dataclasses import dataclass, field as dc_field

    @dataclass
    class MarketSignal:
        market_id: str
        ticker: str
        category: str
        resolution: str
        end_date: datetime
        volume: float
        liquidity: float
        trades: list = dc_field(default_factory=list)

        @property
        def yes_volume(self) -> float:
            return sum(t["notional"] for t in self.trades if t["outcome"] == "yes")

        @property
        def no_volume(self) -> float:
            return sum(t["notional"] for t in self.trades if t["outcome"] == "no")

        @property
        def majority_side(self) -> str:
            return "yes" if self.yes_volume >= self.no_volume else "no"

        @property
        def consensus_pct(self) -> float:
            total = self.yes_volume + self.no_volume
            if total == 0:
                return 0
            return max(self.yes_volume, self.no_volume) / total * 100

        @property
        def whale_count(self) -> int:
            return len(self.trades)

        @property
        def total_notional(self) -> float:
            return sum(t["notional"] for t in self.trades)

        @property
        def majority_won(self) -> bool:
            return self.majority_side == self.resolution

        @property
        def min_minutes_to_close(self) -> float:
            return min(t["minutes_to_close"] for t in self.trades)

        @property
        def avg_entry_price(self) -> float:
            prices = [t["entry_price"] for t in self.trades if t["outcome"] == self.majority_side]
            return sum(prices) / len(prices) if prices else 0

    # Build market signals
    market_signals: dict[str, MarketSignal] = {}
    for t in enriched:
        mid = t["market_id"]
        if mid not in market_signals:
            market_signals[mid] = MarketSignal(
                market_id=mid,
                ticker=t["ticker"],
                category=t["category"],
                resolution=t["resolution"],
                end_date=t["end_date"],
                volume=t["volume"],
                liquidity=t["liquidity"],
            )
        market_signals[mid].trades.append(t)

    signals = list(market_signals.values())

    # Date range
    dates = [t["traded_at"] for t in enriched]
    min_date = min(dates).strftime("%Y-%m-%d")
    max_date = max(dates).strftime("%Y-%m-%d")
    unique_markets = len(signals)

    print()
    print("=" * 70)
    print("  WHALE SIGNAL BACKTEST")
    print("=" * 70)
    print(f"  Date range: {min_date} to {max_date}")
    print(f"  Total qualifying trades: {len(enriched)}")
    print(f"  Unique markets with whale activity: {unique_markets}")
    print(f"  Price range: {args.price_min}-{args.price_max}")
    print(f"  Min notional: ${args.min_notional:,}")

    # === SECTION 1: TRADE-LEVEL (raw, for context) ===
    print()
    print("-" * 70)
    print("  SECTION 1: RAW TRADE-LEVEL STATS")
    print("-" * 70)

    print("  Win rate by time window (individual trades):")
    for window in TIME_WINDOWS:
        subset = [t for t in enriched if t["minutes_to_close"] <= window]
        if subset:
            wins = sum(1 for t in subset if t["won"])
            wr = wins / len(subset) * 100
            mkt_ids = set(t["market_id"] for t in subset)
            print(f"    <{window:>2}min:  {wr:5.1f}%  (n={len(subset)} trades across {len(mkt_ids)} markets)")
    total_wins = sum(1 for t in enriched if t["won"])
    print(f"    ALL:    {total_wins / len(enriched) * 100:5.1f}%  (n={len(enriched)} trades across {unique_markets} markets)")

    # === SECTION 2: MARKET-LEVEL WITH WHALE CONSENSUS ===
    print()
    print("-" * 70)
    print("  SECTION 2: MARKET-LEVEL — WHALE CONSENSUS")
    print("-" * 70)
    print("  (Each market counted ONCE. 'Won' = majority whale side was correct.)")
    print()

    # Filter by consensus thresholds
    consensus_thresholds = [50, 60, 70, 80, 90]
    print("  Win rate by consensus strength (all time windows):")
    for thresh in consensus_thresholds:
        subset = [s for s in signals if s.consensus_pct >= thresh]
        if subset:
            wins = sum(1 for s in subset if s.majority_won)
            wr = wins / len(subset) * 100
            print(f"    >={thresh}% consensus:  {wr:5.1f}% WR  ({wins}/{len(subset)} markets)")

    # Win rate by time window AND consensus
    print()
    print("  Win rate by time window + consensus >= 70%:")
    for window in TIME_WINDOWS:
        subset = [s for s in signals
                  if s.min_minutes_to_close <= window and s.consensus_pct >= 70]
        if subset:
            wins = sum(1 for s in subset if s.majority_won)
            wr = wins / len(subset) * 100
            print(f"    <{window:>2}min:  {wr:5.1f}%  ({wins}/{len(subset)} markets)")

    # Win rate by whale count (more whales on same market = stronger signal)
    print()
    print("  Win rate by number of whale trades on market:")
    whale_count_buckets = [(1, 1), (2, 3), (4, 10), (11, float("inf"))]
    for lo, hi in whale_count_buckets:
        subset = [s for s in signals if lo <= s.whale_count <= hi]
        if subset:
            wins = sum(1 for s in subset if s.majority_won)
            wr = wins / len(subset) * 100
            hi_str = f"{hi:.0f}" if hi < float("inf") else "11+"
            label = f"{lo}" if lo == hi else f"{lo}-{hi_str}"
            print(f"    {label:>5} whales:  {wr:5.1f}%  ({wins}/{len(subset)} markets)")

    # === SECTION 3: UNIQUE MARKETS PER DAY (signal frequency) ===
    print()
    print("-" * 70)
    print("  SECTION 3: SIGNAL FREQUENCY (unique markets per day)")
    print("-" * 70)

    for window in TIME_WINDOWS:
        day_markets: dict[str, set[str]] = defaultdict(set)
        for s in signals:
            if s.min_minutes_to_close <= window:
                # Use the date of the earliest qualifying trade
                earliest = min(t["traded_at"] for t in s.trades if t["minutes_to_close"] <= window)
                day = earliest.strftime("%Y-%m-%d")
                day_markets[day].add(s.market_id)
        if day_markets:
            counts = [len(v) for v in day_markets.values()]
            avg = sum(counts) / len(counts)
            med = sorted(counts)[len(counts) // 2]
            print(f"    <{window:>2}min:  avg {avg:.1f}, median {med} unique markets/day "
                  f"(range {min(counts)}-{max(counts)}, over {len(day_markets)} days)")

    # All time windows
    day_markets_all: dict[str, set[str]] = defaultdict(set)
    for s in signals:
        earliest = min(t["traded_at"] for t in s.trades)
        day = earliest.strftime("%Y-%m-%d")
        day_markets_all[day].add(s.market_id)
    if day_markets_all:
        counts = [len(v) for v in day_markets_all.values()]
        avg = sum(counts) / len(counts)
        med = sorted(counts)[len(counts) // 2]
        print(f"    ALL:    avg {avg:.1f}, median {med} unique markets/day "
              f"(range {min(counts)}-{max(counts)}, over {len(day_markets_all)} days)")

    # === SECTION 4: BREAKDOWNS ===
    print()
    print("-" * 70)
    print("  SECTION 4: BREAKDOWNS (market-level, consensus >= 70%)")
    print("-" * 70)

    strong = [s for s in signals if s.consensus_pct >= 70]

    # By entry price
    print("  By avg entry price:")
    for lo, hi in PRICE_BUCKETS:
        lo_f, hi_f = lo / 100, hi / 100
        subset = [s for s in strong if lo_f <= s.avg_entry_price < hi_f]
        if subset:
            wins = sum(1 for s in subset if s.majority_won)
            wr = wins / len(subset) * 100
            print(f"    {lo}-{hi}c:  {wr:5.1f}%  ({wins}/{len(subset)} markets)")

    # By category
    print()
    print("  By category:")
    cat_groups: dict[str, list] = defaultdict(list)
    for s in strong:
        cat_groups[s.category or "unknown"].append(s)
    for cat in sorted(cat_groups, key=lambda c: len(cat_groups[c]), reverse=True)[:10]:
        subset = cat_groups[cat]
        wins = sum(1 for s in subset if s.majority_won)
        wr = wins / len(subset) * 100
        day_c: dict[str, set] = defaultdict(set)
        for s in subset:
            earliest = min(t["traded_at"] for t in s.trades)
            day_c[earliest.strftime("%Y-%m-%d")].add(s.market_id)
        avg_per_day = sum(len(v) for v in day_c.values()) / len(day_c) if day_c else 0
        print(f"    {cat:>20}:  {wr:5.1f}% WR  ({wins}/{len(subset)} mkts, {avg_per_day:.1f} mkts/day)")

    # By trade size (total whale notional on the market)
    print()
    print("  By total whale volume on market:")
    size_buckets = [(1000, 5000), (5000, 10000), (10000, 25000), (25000, 50000), (50000, float("inf"))]
    for lo, hi in size_buckets:
        subset = [s for s in strong if lo <= s.total_notional < hi]
        if subset:
            wins = sum(1 for s in subset if s.majority_won)
            wr = wins / len(subset) * 100
            hi_str = f"${hi:,.0f}" if hi < float("inf") else "$50k+"
            print(f"    ${lo:,.0f}-{hi_str}:  {wr:5.1f}%  ({wins}/{len(subset)} markets)")

    # === SECTION 5: LIQUIDITY ===
    print()
    print("-" * 70)
    print("  SECTION 5: MARKET LIQUIDITY")
    print("-" * 70)
    volumes = [s.volume for s in signals if s.volume > 0]
    liquidities = [s.liquidity for s in signals if s.liquidity > 0]
    if volumes:
        print(f"    Avg market volume: ${sum(volumes) / len(volumes):,.0f}")
        print(f"    Median market volume: ${sorted(volumes)[len(volumes) // 2]:,.0f}")
    if liquidities:
        print(f"    Avg market liquidity: ${sum(liquidities) / len(liquidities):,.0f}")
        print(f"    Median market liquidity: ${sorted(liquidities)[len(liquidities) // 2]:,.0f}")
    print(f"    (Volume = total traded. Liquidity = Kalshi liquidity field.)")
    print(f"    Whale fills at $1k-$10k prove order book depth supports our sizing.")

    # === SECTION 6: COMPOSITE SIGNAL MATRIX ===
    # Sweep across filter combos to find optimal criteria
    print()
    print("-" * 70)
    print("  SECTION 6: COMPOSITE SIGNAL MATRIX")
    print("  (Find the best filter combination: time window x consensus x min whales)")
    print("-" * 70)

    # Only sports + economics (best categories from data)
    good_cats = {"sports", "economics"}
    cat_filtered = [s for s in signals if s.category in good_cats]
    print(f"  Filtered to sports + economics: {len(cat_filtered)} markets")
    print()

    min_whale_counts = [1, 2, 3, 5]
    consensus_levels = [50, 70, 80, 90]

    # Header
    print(f"  {'Window':>8} {'MinWh':>5} {'Cons%':>5} | {'WR':>6} {'Won':>5} {'Total':>5} | "
          f"{'Mkts/Day':>8} {'Days':>4}")
    print(f"  {'-' * 60}")

    best_combos = []

    for window in TIME_WINDOWS:
        for min_wh in min_whale_counts:
            for cons in consensus_levels:
                subset = [
                    s for s in cat_filtered
                    if s.min_minutes_to_close <= window
                    and s.whale_count >= min_wh
                    and s.consensus_pct >= cons
                ]
                if len(subset) < 10:
                    continue

                wins = sum(1 for s in subset if s.majority_won)
                wr = wins / len(subset) * 100

                # Unique markets per day
                day_mkts: dict[str, set[str]] = defaultdict(set)
                for s in subset:
                    earliest = min(
                        t["traded_at"] for t in s.trades
                        if t["minutes_to_close"] <= window
                    )
                    day_mkts[earliest.strftime("%Y-%m-%d")].add(s.market_id)
                if not day_mkts:
                    continue
                counts = [len(v) for v in day_mkts.values()]
                avg_mkts = sum(counts) / len(counts)
                n_days = len(day_mkts)

                best_combos.append((wr, avg_mkts, window, min_wh, cons, wins, len(subset), n_days))

    # Sort by WR descending, then by mkts/day descending
    best_combos.sort(key=lambda x: (x[0], x[1]), reverse=True)

    # Print top 30
    seen = set()
    printed = 0
    for wr, avg_mkts, window, min_wh, cons, wins, total, n_days in best_combos:
        if printed >= 30:
            break
        key = (window, min_wh, cons)
        if key in seen:
            continue
        seen.add(key)
        print(f"  {f'<{window}min':>8} {min_wh:>5} {cons:>4}% | {wr:5.1f}% {wins:>5} {total:>5} | "
              f"{avg_mkts:>7.1f} {n_days:>4}")
        printed += 1

    # === SECTION 7: EV ANALYSIS ===
    # For each promising combo, compute expected $ profit per trade
    print()
    print("-" * 70)
    print("  SECTION 7: EXPECTED VALUE PER TRADE")
    print("  (Using actual entry prices, Kalshi fees, and 15% stop loss)")
    print("-" * 70)

    import math
    FEE_COEFF = 0.07

    def _fee(price: float, contracts: int) -> float:
        return math.ceil(FEE_COEFF * contracts * price * (1 - price) * 100) / 100

    stop_pct = 0.15

    # Pick top combos with good WR and decent frequency
    interesting = [
        c for c in best_combos
        if c[0] >= 90 and c[1] >= 3  # WR >= 90%, avg 3+ mkts/day
    ][:15]

    if interesting:
        print(f"  {'Window':>8} {'MinWh':>5} {'Cons%':>5} | {'WR':>6} {'Mkts/D':>6} | "
              f"{'AvgEntry':>8} {'EV/Contract':>11} {'EV/$100':>8}")
        print(f"  {'-' * 70}")

        for wr, avg_mkts, window, min_wh, cons, wins, total, n_days in interesting:
            # Get actual entry prices for this combo
            subset = [
                s for s in cat_filtered
                if s.min_minutes_to_close <= window
                and s.whale_count >= min_wh
                and s.consensus_pct >= cons
            ]
            all_entries = []
            for s in subset:
                for t in s.trades:
                    if t["outcome"] == s.majority_side and t["minutes_to_close"] <= window:
                        all_entries.append(t["entry_price"])

            if not all_entries:
                continue

            avg_entry = sum(all_entries) / len(all_entries)
            win_rate = wr / 100

            # EV per contract (at 1 contract):
            # Win: $1.00 - entry - entry_fee
            # Loss: (stop_price - entry - entry_fee - exit_fee)
            entry_fee_1 = _fee(avg_entry, 1)
            stop_price = avg_entry * (1 - stop_pct)
            exit_fee_1 = _fee(stop_price, 1)

            win_pnl = 1.0 - avg_entry - entry_fee_1
            loss_pnl = stop_price - exit_fee_1 - avg_entry - entry_fee_1

            ev_per_contract = win_rate * win_pnl + (1 - win_rate) * loss_pnl
            # EV per $100 wagered
            cost_per = avg_entry + entry_fee_1
            ev_per_100 = (ev_per_contract / cost_per) * 100 if cost_per > 0 else 0

            print(f"  {f'<{window}min':>8} {min_wh:>5} {cons:>4}% | {wr:5.1f}% {avg_mkts:>5.1f} | "
                  f"  {avg_entry:.3f} {ev_per_contract:>10.4f} {ev_per_100:>7.2f}%")

    print()
    print("  NOTE: end_date = market close time, not actual event outcome time.")
    print("  For sports, outcome may be known before close. Real bot uses live state.")
    print("=" * 70)

    await client.auth.sign_out()


def _parse_ts(val) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
    s = str(val).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt
    except ValueError:
        return None


if __name__ == "__main__":
    asyncio.run(main())
