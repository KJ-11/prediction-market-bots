"""Phase 2: Cross-platform outcome comparison and deeper analysis."""

from __future__ import annotations

import csv
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KALSHI_DIR = Path("data/rounds")
PM_DIR = Path("data/rounds/polymarket")


def dec(val: str) -> Decimal | None:
    if not val or val == "":
        return None
    try:
        return Decimal(val)
    except InvalidOperation:
        return None


def parse_ts(ts_str: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def floor_to_15m(dt: datetime) -> datetime:
    """Floor a datetime to the nearest 15-minute boundary."""
    minute = dt.minute - (dt.minute % 15)
    return dt.replace(minute=minute, second=0, microsecond=0)


# ============================================================
# Cross-platform outcome comparison
# ============================================================
def compare_outcomes():
    print("=" * 80)
    print("CROSS-PLATFORM OUTCOME COMPARISON (BTC 15m)")
    print("=" * 80)

    # Load Kalshi BTC rounds
    kalshi = {}  # floored_end_time -> outcome
    for csv_file in sorted(KALSHI_DIR.glob("KXBTC15M-*.csv")):
        with open(csv_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("row_type") != "round_end":
                    continue
                ts = parse_ts(row.get("timestamp", ""))
                if not ts:
                    continue
                # Kalshi round ends at the 15m boundary. The timestamp is
                # when we recorded it, so floor to 15m.
                floored = floor_to_15m(ts)
                outcome = row.get("outcome", "unknown")
                # Kalshi "yes" = price >= strike = "up"
                kalshi_dir = "up" if outcome == "yes" else ("down" if outcome == "no" else "unknown")
                kalshi[floored] = kalshi_dir

    # Load PM BTC 15m rounds
    pm = {}  # floored_end_time -> outcome
    for csv_file in sorted(PM_DIR.glob("BTC-15m-*.csv")):
        with open(csv_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("row_type") != "round_end":
                    continue
                end_date = row.get("end_date", "")
                dt = parse_ts(end_date)
                if not dt:
                    continue
                outcome = row.get("outcome", "unknown")
                pm[dt] = outcome

    print(f"\nKalshi BTC rounds: {len(kalshi)}")
    print(f"PM BTC 15m rounds: {len(pm)}")

    # Match
    agree = 0
    disagree = 0
    k_unknown = 0
    p_unknown = 0
    disagree_times = []

    for dt, k_out in sorted(kalshi.items()):
        if dt not in pm:
            continue
        p_out = pm[dt]
        if k_out == "unknown" or p_out == "unknown":
            if k_out == "unknown":
                k_unknown += 1
            if p_out == "unknown":
                p_unknown += 1
            continue
        if k_out == p_out:
            agree += 1
        else:
            disagree += 1
            if len(disagree_times) < 30:
                disagree_times.append((dt, k_out, p_out))

    total = agree + disagree
    print(f"\nMatched rounds (both resolved): {total}")
    print(f"  Agree: {agree} ({agree/max(total,1)*100:.1f}%)")
    print(f"  Disagree: {disagree} ({disagree/max(total,1)*100:.1f}%)")
    print(f"  Kalshi unknown: {k_unknown}")
    print(f"  PM unknown: {p_unknown}")

    if disagree_times:
        print(f"\nDisagreements:")
        for dt, k, p in disagree_times:
            print(f"  {dt.isoformat()}: Kalshi={k} PM={p}")

    # This tells us: how often do the platforms disagree on direction?
    # If ~5-8%, that's the CF Benchmarks vs Chainlink resolution difference.
    # If much higher, something is wrong with our data.


# ============================================================
# PM "unknown" outcome deep dive
# ============================================================
def analyze_pm_unknowns():
    print("\n" + "=" * 80)
    print("PM UNKNOWN OUTCOME DEEP DIVE")
    print("=" * 80)

    for dur in ["5m", "15m"]:
        print(f"\n  Duration: {dur}")
        csv_files = sorted(PM_DIR.glob(f"*-{dur}-*.csv"))

        total = 0
        unknowns = 0
        # For unknowns, what was the last_trade_price?
        unknown_last_prices = []
        # For unknowns, what did Coinbase spot say?
        unknown_spot_dirs = Counter()

        for csv_file in csv_files:
            with open(csv_file) as f:
                reader = csv.DictReader(f)
                first_spot = None
                last_trade = None
                slug = ""

                for row in reader:
                    s = row.get("slug", "")
                    if s != slug:
                        slug = s
                        first_spot = None

                    if row.get("row_type") == "snapshot":
                        spot = dec(row.get("spot_price", ""))
                        if spot and first_spot is None:
                            first_spot = spot
                        tp = dec(row.get("last_trade_price", ""))
                        if tp is not None:
                            last_trade = tp

                    elif row.get("row_type") == "round_end":
                        total += 1
                        outcome = row.get("outcome", "unknown")
                        if outcome == "unknown":
                            unknowns += 1
                            if last_trade is not None:
                                unknown_last_prices.append(float(last_trade))
                            end_spot = dec(row.get("spot_price", ""))
                            if first_spot and end_spot:
                                if end_spot >= first_spot:
                                    unknown_spot_dirs["up"] += 1
                                else:
                                    unknown_spot_dirs["down"] += 1
                        first_spot = None
                        last_trade = None

        print(f"    Total: {total}, Unknown: {unknowns} ({unknowns/max(total,1)*100:.1f}%)")

        if unknown_last_prices:
            # How many had a decisive last trade price?
            decisive = sum(1 for p in unknown_last_prices if p >= 0.90 or p <= 0.10)
            ambiguous = len(unknown_last_prices) - decisive
            print(f"    Last trade decisive (>0.90 or <0.10): {decisive}")
            print(f"    Last trade ambiguous (0.10-0.90): {ambiguous}")
            print(f"    (These are UP token trades only — missing DOWN token)")

        print(f"    Coinbase spot direction for unknowns: {dict(unknown_spot_dirs)}")
        up = unknown_spot_dirs.get("up", 0)
        dn = unknown_spot_dirs.get("down", 0)
        if up + dn > 0:
            print(f"    Up rate: {up/(up+dn)*100:.1f}%")


# ============================================================
# Book quality over time (PM)
# ============================================================
def pm_book_quality_by_time():
    """Check if PM books are better/worse at different points in the round."""
    print("\n" + "=" * 80)
    print("PM BOOK QUALITY BY TIME IN ROUND")
    print("=" * 80)

    for dur in ["5m", "15m"]:
        print(f"\n  Duration: {dur}")
        csv_files = sorted(PM_DIR.glob(f"*-{dur}-*.csv"))

        dur_seconds = {"5m": 300, "15m": 900}[dur]

        # Bucket by time remaining
        buckets = defaultdict(lambda: {"real": 0, "empty": 0, "total": 0})

        for csv_file in csv_files:
            with open(csv_file) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("row_type") != "snapshot":
                        continue
                    rem = row.get("seconds_remaining", "")
                    if not rem:
                        continue
                    try:
                        secs = float(rem)
                    except ValueError:
                        continue

                    # Bucket into 60s intervals
                    bucket = int(secs / 60) * 60

                    up_bid = row.get("up_bid", "")
                    up_ask = row.get("up_ask", "")

                    buckets[bucket]["total"] += 1
                    if up_bid == "0.01" and up_ask == "0.99":
                        buckets[bucket]["empty"] += 1
                    elif up_bid and up_ask:
                        buckets[bucket]["real"] += 1

        print(f"    Time remaining → book quality:")
        for bucket in sorted(buckets.keys(), reverse=True):
            d = buckets[bucket]
            if d["total"] == 0:
                continue
            real_pct = d["real"] / d["total"] * 100
            print(f"    {bucket:>4}s remaining: {real_pct:5.1f}% real book ({d['total']:>6} snapshots)")


# ============================================================
# Kalshi book data staleness analysis
# ============================================================
def kalshi_book_staleness():
    """How long does the Kalshi book stay unchanged?"""
    print("\n" + "=" * 80)
    print("KALSHI BOOK UPDATE FREQUENCY")
    print("=" * 80)

    # Look at how many unique (yes_bid, yes_ask) values per round
    updates_per_round = []

    for csv_file in sorted(KALSHI_DIR.glob("KXBTC15M-*.csv")):
        with open(csv_file) as f:
            reader = csv.DictReader(f)
            current_round = None
            seen_prices = set()

            for row in reader:
                ticker = row.get("round_ticker", "")
                if ticker != current_round:
                    if current_round and seen_prices:
                        updates_per_round.append(len(seen_prices))
                    current_round = ticker
                    seen_prices = set()

                if row.get("row_type") == "snapshot":
                    yb = row.get("yes_bid", "")
                    ya = row.get("yes_ask", "")
                    if yb and ya:
                        seen_prices.add((yb, ya))

    if updates_per_round:
        import statistics
        print(f"\n  BTC: unique (yes_bid, yes_ask) pairs per round:")
        print(f"    Mean: {statistics.mean(updates_per_round):.1f}")
        print(f"    Median: {statistics.median(updates_per_round):.0f}")
        print(f"    Min: {min(updates_per_round)}, Max: {max(updates_per_round)}")


# ============================================================
# PM down_bid/down_ask analysis — are they always complement?
# ============================================================
def pm_down_token_analysis():
    print("\n" + "=" * 80)
    print("PM DOWN TOKEN ANALYSIS")
    print("=" * 80)

    print("""
The collector computes down_bid/down_ask from complements (lines 444-447):
    if best_down_bid is None and best_up_ask is not None:
        best_down_bid = 1 - best_up_ask
    if best_down_ask is None and best_up_bid is not None:
        best_down_ask = 1 - best_up_bid

So when up_bid=0.01, up_ask=0.99:
    down_bid = 1 - 0.99 = 0.01
    down_ask = 1 - 0.01 = 0.99

When real book: up_bid=0.45, up_ask=0.47:
    down_bid = 1 - 0.47 = 0.53
    down_ask = 1 - 0.45 = 0.55

But this is WRONG if both tokens have independent order books!
On Polymarket, UP and DOWN are separate tokens. Their prices don't
need to sum to 1.00. The complement calculation is an approximation.
    """)

    # Check: are there cases where down_bid/down_ask != complement?
    csv_files = sorted(PM_DIR.glob("BTC-15m-*.csv"))
    complement_count = 0
    non_complement_count = 0

    for csv_file in csv_files:
        with open(csv_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("row_type") != "snapshot":
                    continue
                up_bid = dec(row.get("up_bid", ""))
                up_ask = dec(row.get("up_ask", ""))
                down_bid = dec(row.get("down_bid", ""))
                down_ask = dec(row.get("down_ask", ""))

                if all(v is not None for v in [up_bid, up_ask, down_bid, down_ask]):
                    # Check if down = 1 - up
                    if (down_bid == Decimal("1") - up_ask and
                        down_ask == Decimal("1") - up_bid):
                        complement_count += 1
                    else:
                        non_complement_count += 1
                        if non_complement_count <= 5:
                            print(f"  Non-complement: up={up_bid}/{up_ask} down={down_bid}/{down_ask}")

    print(f"\n  Complement: {complement_count}")
    print(f"  Non-complement: {non_complement_count}")
    print(f"  (All complement → down_bid/down_ask adds no information)")


# ============================================================
# PM up_bid flicker analysis — is the "real" book actually DOWN token?
# ============================================================
def pm_book_flicker():
    """When up_bid changes from 0.01 to a real value and back, how long
    does the real value persist? If it's 1-2 snapshots, it's likely a
    misattributed best_bid_ask event from the DOWN token."""
    print("\n" + "=" * 80)
    print("PM BOOK FLICKER ANALYSIS (is 'real' book data actually DOWN token?)")
    print("=" * 80)

    csv_files = sorted(PM_DIR.glob("BTC-15m-*.csv"))

    flicker_durations = []  # How many consecutive snapshots was the book "real"?
    streak = 0
    in_real = False

    for csv_file in csv_files:
        with open(csv_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("row_type") == "round_end":
                    if in_real and streak > 0:
                        flicker_durations.append(streak)
                    streak = 0
                    in_real = False
                    continue

                if row.get("row_type") != "snapshot":
                    continue

                up_bid = row.get("up_bid", "")
                up_ask = row.get("up_ask", "")

                is_real = (up_bid != "0.01" and up_bid != "" and
                           up_ask != "0.99" and up_ask != "" and
                           up_bid and up_ask)

                if is_real:
                    if not in_real:
                        in_real = True
                        streak = 1
                    else:
                        streak += 1
                else:
                    if in_real and streak > 0:
                        flicker_durations.append(streak)
                    in_real = False
                    streak = 0

    if flicker_durations:
        import statistics
        print(f"\n  'Real' book episodes (BTC 15m):")
        print(f"    Count: {len(flicker_durations)}")
        print(f"    Mean duration: {statistics.mean(flicker_durations):.1f} snapshots (~seconds)")
        print(f"    Median: {statistics.median(flicker_durations):.0f}")
        print(f"    P10: {sorted(flicker_durations)[int(len(flicker_durations)*0.1)]}")
        print(f"    P90: {sorted(flicker_durations)[int(len(flicker_durations)*0.9)]}")
        print(f"    Max: {max(flicker_durations)}")

        short = sum(1 for d in flicker_durations if d <= 3)
        long = sum(1 for d in flicker_durations if d > 30)
        print(f"    <=3 snapshots (likely flicker): {short} ({short/len(flicker_durations)*100:.1f}%)")
        print(f"    >30 snapshots (sustained): {long} ({long/len(flicker_durations)*100:.1f}%)")

        print("""
  INTERPRETATION:
  - If most episodes are short (1-3 snapshots), these are likely DOWN token
    best_bid_ask events being misattributed to UP.
  - If episodes are sustained (30+ snapshots), the UP token book is
    genuinely narrowing late in the round as the outcome becomes clearer.
  - A mix of both suggests both phenomena are present.
        """)


if __name__ == "__main__":
    compare_outcomes()
    analyze_pm_unknowns()
    pm_book_quality_by_time()
    kalshi_book_staleness()
    pm_down_token_analysis()
    pm_book_flicker()
