"""Analyze trade CSVs for strategy performance insights."""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

CST = ZoneInfo("America/Chicago")

def load_trades(data_dir: str = "data/trades") -> list[dict]:
    """Load all trade CSVs, return only actual trades (not round summaries)."""
    trades = []
    for f in sorted(Path(data_dir).glob("*.csv")):
        with open(f) as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if row.get("strategy") and row["strategy"] != "ROUND_SUMMARY":
                    trades.append(row)
    return trades


def load_rounds(data_dir: str = "data/trades") -> list[dict]:
    """Load round summaries."""
    rounds = []
    for f in sorted(Path(data_dir).glob("*.csv")):
        with open(f) as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if row.get("strategy") == "ROUND_SUMMARY":
                    rounds.append(row)
    return rounds


def analyze(trades: list[dict]) -> None:
    # Filter to filled trades only
    filled = [t for t in trades if t["status"] == "filled"]
    cancelled = [t for t in trades if t["status"] == "cancelled"]
    failed = [t for t in trades if t["status"] == "failed"]

    print(f"=== TRADE ANALYSIS ({len(trades)} signals) ===\n")
    print(f"Filled: {len(filled)}  |  Cancelled (IOC no-fill): {len(cancelled)}  |  Failed: {len(failed)}")
    fill_rate = len(filled) / len(trades) * 100 if trades else 0
    print(f"Fill rate: {fill_rate:.0f}%\n")

    # We need to match fills to outcomes. The CSV doesn't have outcome result,
    # but we can infer from balance changes between rounds.
    # For now, analyze what we CAN see: price distribution, coin distribution, timing.

    # --- By coin ---
    print("=== BY COIN ===")
    coin_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "cancelled": 0, "total_cost": Decimal("0"), "prices": []})
    for t in filled:
        ticker = t["round_ticker"]
        if "BTC" in ticker:
            coin = "BTC"
        elif "ETH" in ticker:
            coin = "ETH"
        elif "SOL" in ticker:
            coin = "SOL"
        elif "XRP" in ticker:
            coin = "XRP"
        else:
            coin = "???"
        coin_stats[coin]["trades"] += 1
        price = Decimal(t["price"])
        size = Decimal(t["size"])
        coin_stats[coin]["total_cost"] += price * size
        coin_stats[coin]["prices"].append(float(price))
    for t in cancelled:
        ticker = t["round_ticker"]
        if "BTC" in ticker:
            coin = "BTC"
        elif "ETH" in ticker:
            coin = "ETH"
        elif "SOL" in ticker:
            coin = "SOL"
        else:
            coin = "???"
        coin_stats[coin]["cancelled"] += 1

    for coin in sorted(coin_stats):
        s = coin_stats[coin]
        avg_price = sum(s["prices"]) / len(s["prices"]) if s["prices"] else 0
        print(f"  {coin}: {s['trades']} fills, {s['cancelled']} cancelled, avg price ${avg_price:.2f}, total cost ${s['total_cost']:.2f}")

    # --- By hour (CST) ---
    print("\n=== BY HOUR (CST) ===")
    hour_stats: dict[int, dict] = defaultdict(lambda: {"trades": 0, "cost": Decimal("0"), "prices": []})
    for t in filled:
        ts = datetime.fromisoformat(t["timestamp"]).astimezone(CST)
        h = ts.hour
        hour_stats[h]["trades"] += 1
        price = Decimal(t["price"])
        size = Decimal(t["size"])
        hour_stats[h]["cost"] += price * size
        hour_stats[h]["prices"].append(float(price))

    for h in sorted(hour_stats):
        s = hour_stats[h]
        avg_p = sum(s["prices"]) / len(s["prices"]) if s["prices"] else 0
        print(f"  {h:02d}:00 CST: {s['trades']:2d} trades, avg price ${avg_p:.2f}, total cost ${s['cost']:.2f}")

    # --- Entry price distribution ---
    print("\n=== ENTRY PRICE DISTRIBUTION ===")
    price_buckets: dict[str, int] = defaultdict(int)
    for t in filled:
        p = float(t["price"])
        if p >= 0.95:
            bucket = "$0.95+"
        elif p >= 0.93:
            bucket = "$0.93-0.94"
        elif p >= 0.91:
            bucket = "$0.91-0.92"
        elif p >= 0.89:
            bucket = "$0.89-0.90"
        elif p >= 0.85:
            bucket = "$0.85-0.88"
        else:
            bucket = "<$0.85"
        price_buckets[bucket] += 1

    for bucket in ["<$0.85", "$0.85-0.88", "$0.89-0.90", "$0.91-0.92", "$0.93-0.94", "$0.95+"]:
        count = price_buckets.get(bucket, 0)
        pct = count / len(filled) * 100 if filled else 0
        bar = "█" * int(pct / 2)
        print(f"  {bucket:>12s}: {count:3d} ({pct:4.0f}%) {bar}")

    # --- Outcome (yes/no) distribution ---
    print("\n=== OUTCOME DISTRIBUTION ===")
    yes_count = sum(1 for t in filled if t["outcome"] == "yes")
    no_count = sum(1 for t in filled if t["outcome"] == "no")
    print(f"  YES: {yes_count}  |  NO: {no_count}")

    # --- Distance distribution ---
    print("\n=== DISTANCE DISTRIBUTION ===")
    dist_buckets: dict[str, int] = defaultdict(int)
    for t in filled:
        reason = t.get("reason", "")
        if "dist=" in reason:
            dist_str = reason.split("dist=")[1].split(" ")[0]
            try:
                dist = float(dist_str)
                if dist >= 0.005:
                    bucket = "0.50%+"
                elif dist >= 0.004:
                    bucket = "0.40-0.49%"
                elif dist >= 0.003:
                    bucket = "0.30-0.39%"
                elif dist >= 0.002:
                    bucket = "0.20-0.29%"
                else:
                    bucket = "<0.20%"
                dist_buckets[bucket] += 1
            except ValueError:
                pass

    for bucket in ["0.20-0.29%", "0.30-0.39%", "0.40-0.49%", "0.50%+"]:
        count = dist_buckets.get(bucket, 0)
        pct = count / len(filled) * 100 if filled else 0
        bar = "█" * int(pct / 2)
        print(f"  {bucket:>12s}: {count:3d} ({pct:4.0f}%) {bar}")

    # --- Balance trajectory ---
    print("\n=== BALANCE TRAJECTORY ===")
    rounds = load_rounds()
    # Dedupe rounds (3 per round, one per coin — take first)
    seen_tickers = set()
    unique_rounds = []
    for r in rounds:
        # Use timestamp as round identifier (all 3 coins have same timestamp)
        ts = r["timestamp"][:19]  # Truncate to second
        if ts not in seen_tickers:
            seen_tickers.add(ts)
            unique_rounds.append(r)

    if unique_rounds:
        balances = [float(r["balance_after"]) for r in unique_rounds if r.get("balance_after")]
        if balances:
            print(f"  Start: ${balances[0]:.2f}")
            print(f"  End:   ${balances[-1]:.2f}")
            print(f"  High:  ${max(balances):.2f}")
            print(f"  Low:   ${min(balances):.2f}")
            print(f"  P&L:   ${balances[-1] - balances[0]:+.2f}")
            print(f"  Rounds: {len(balances)}")

    # --- Trades per round ---
    print("\n=== TRADES PER ROUND ===")
    round_trade_counts: dict[str, int] = defaultdict(int)
    for r in rounds:
        reason = r.get("reason", "")
        if "trades=" in reason:
            trade_count = int(reason.split("trades=")[1])
            ts = r["timestamp"][:19]
            round_trade_counts[ts] = max(round_trade_counts[ts], trade_count)

    trade_count_dist: dict[int, int] = defaultdict(int)
    for count in round_trade_counts.values():
        trade_count_dist[count] += 1
    for n in sorted(trade_count_dist):
        print(f"  {n} trades: {trade_count_dist[n]} rounds")

    # --- Size distribution ---
    print("\n=== SIZE DISTRIBUTION (contracts per trade) ===")
    sizes = [int(t["size"]) for t in filled]
    size_dist: dict[int, int] = defaultdict(int)
    for s in sizes:
        size_dist[s] += 1
    for s in sorted(size_dist):
        print(f"  {s} contracts: {size_dist[s]} trades")

    total_contracts = sum(sizes)
    total_cost = sum(Decimal(t["price"]) * Decimal(t["size"]) for t in filled)
    print(f"\n  Total contracts: {total_contracts}")
    print(f"  Total capital deployed: ${total_cost:.2f}")
    avg_size = total_contracts / len(filled) if filled else 0
    print(f"  Avg size per trade: {avg_size:.1f} contracts")


if __name__ == "__main__":
    trades = load_trades()
    if not trades:
        print("No trades found in data/trades/")
        sys.exit(1)
    analyze(trades)
