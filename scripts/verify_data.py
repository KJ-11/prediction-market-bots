"""Comprehensive data verification for Kalshi and Polymarket collected data.

Checks:
- Kalshi: timing consistency, outcome determination, data gaps, book parsing
- PM: token mapping, book staleness, trade tracking, outcome reliability
- Cross-platform: time alignment, resolution disagreement
"""

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


# ============================================================
# KALSHI VERIFICATION
# ============================================================
def verify_kalshi():
    print("=" * 80)
    print("KALSHI DATA VERIFICATION")
    print("=" * 80)

    csv_files = sorted(KALSHI_DIR.glob("KX*15M-*.csv"))
    print(f"\nFound {len(csv_files)} Kalshi CSV files")

    total_rounds = 0
    total_snapshots = 0
    outcome_counts = Counter()
    timing_issues = []
    stale_book_count = 0
    missing_spot_count = 0
    missing_book_count = 0
    rounds_per_day_coin = defaultdict(int)
    round_details = []  # (ticker, outcome, final_spot, strike, seconds_elapsed_range)

    for csv_file in csv_files:
        series = csv_file.stem.rsplit("-", 3)[0]  # KXBTC15M
        date = "-".join(csv_file.stem.rsplit("-", 3)[1:])

        with open(csv_file) as f:
            reader = csv.DictReader(f)
            current_round = None
            round_snapshots = 0
            round_start_time = None
            round_max_elapsed = 0
            round_min_remaining = 999
            prev_yes_bid = None
            prev_yes_ask = None
            stale_streak = 0
            max_stale_streak = 0

            for row in reader:
                row_type = row.get("row_type", "")
                ticker = row.get("round_ticker", "")

                if ticker != current_round:
                    # New round
                    if current_round and round_snapshots > 0:
                        # Check previous round
                        pass
                    current_round = ticker
                    round_snapshots = 0
                    round_start_time = parse_ts(row.get("timestamp", ""))
                    stale_streak = 0
                    max_stale_streak = 0
                    prev_yes_bid = None
                    prev_yes_ask = None

                if row_type == "snapshot":
                    total_snapshots += 1
                    round_snapshots += 1

                    # Check timing
                    sec_rem = row.get("seconds_remaining", "")
                    sec_ela = row.get("seconds_elapsed", "")
                    if sec_rem and sec_ela:
                        try:
                            rem = float(sec_rem)
                            ela = float(sec_ela)
                            total_time = rem + ela
                            if abs(total_time - 900) > 10:
                                timing_issues.append(
                                    f"{ticker}: rem={rem:.0f} + ela={ela:.0f} = {total_time:.0f} (expected ~900)"
                                )
                            round_max_elapsed = max(round_max_elapsed, ela)
                            round_min_remaining = min(round_min_remaining, rem)
                        except ValueError:
                            pass

                    # Check for missing data
                    spot = row.get("spot_price", "")
                    yes_bid = row.get("yes_bid", "")
                    yes_ask = row.get("yes_ask", "")

                    if not spot:
                        missing_spot_count += 1
                    if not yes_bid and not yes_ask:
                        missing_book_count += 1

                    # Check book staleness
                    if yes_bid == prev_yes_bid and yes_ask == prev_yes_ask and yes_bid:
                        stale_streak += 1
                        max_stale_streak = max(max_stale_streak, stale_streak)
                    else:
                        stale_streak = 0
                    prev_yes_bid = yes_bid
                    prev_yes_ask = yes_ask

                elif row_type == "round_end":
                    total_rounds += 1
                    outcome = row.get("outcome", "unknown")
                    outcome_counts[outcome] += 1
                    rounds_per_day_coin[f"{series}-{date}"] += 1

                    strike_str = row.get("strike", "")
                    spot_str = row.get("spot_price", "")
                    strike = dec(strike_str)
                    spot = dec(spot_str)

                    # Verify outcome vs spot
                    if strike and spot:
                        expected = "yes" if spot >= strike else "no"
                        if outcome != expected and outcome != "unknown":
                            round_details.append({
                                "ticker": ticker,
                                "outcome": outcome,
                                "expected": expected,
                                "spot": spot,
                                "strike": strike,
                                "diff": spot - strike,
                            })

                    if max_stale_streak > 30:
                        stale_book_count += 1

    print(f"\nTotal rounds: {total_rounds}")
    print(f"Total snapshots: {total_snapshots}")
    print(f"Avg snapshots/round: {total_snapshots / max(total_rounds, 1):.0f}")

    print(f"\nOutcome distribution:")
    for k, v in sorted(outcome_counts.items()):
        print(f"  {k}: {v} ({v/max(total_rounds,1)*100:.1f}%)")

    print(f"\nTiming issues (rem+ela != ~900): {len(timing_issues)}")
    if timing_issues:
        for t in timing_issues[:10]:
            print(f"  {t}")
        if len(timing_issues) > 10:
            print(f"  ... and {len(timing_issues)-10} more")

    print(f"\nMissing spot price snapshots: {missing_spot_count} ({missing_spot_count/max(total_snapshots,1)*100:.2f}%)")
    print(f"Missing book data snapshots: {missing_book_count} ({missing_book_count/max(total_snapshots,1)*100:.2f}%)")
    print(f"Rounds with >30s stale book: {stale_book_count}")

    # Outcome vs spot disagreements
    print(f"\nOutcome vs spot price disagreements: {len(round_details)}")
    if round_details:
        print("  (These indicate CF Benchmarks resolution differs from our Coinbase spot)")
        for rd in round_details[:20]:
            print(f"  {rd['ticker']}: outcome={rd['outcome']} expected={rd['expected']} "
                  f"spot={rd['spot']} strike={rd['strike']} diff={rd['diff']}")
        if len(round_details) > 20:
            print(f"  ... and {len(round_details)-20} more")

    # Check for day gaps
    print(f"\nRounds per day per coin (sample):")
    for key in sorted(rounds_per_day_coin.keys())[:20]:
        count = rounds_per_day_coin[key]
        expected = 96  # 24*4 = 96 rounds per day
        gap_pct = (expected - count) / expected * 100
        print(f"  {key}: {count} rounds ({gap_pct:.0f}% missed)")

    return round_details


# ============================================================
# POLYMARKET VERIFICATION
# ============================================================
def verify_polymarket():
    print("\n" + "=" * 80)
    print("POLYMARKET DATA VERIFICATION")
    print("=" * 80)

    csv_files = sorted(PM_DIR.glob("*.csv"))
    print(f"\nFound {len(csv_files)} PM CSV files")

    # Separate by duration
    by_duration = defaultdict(list)
    for f in csv_files:
        parts = f.stem.split("-")
        if len(parts) >= 2:
            dur = parts[1]
            by_duration[dur].append(f)

    for dur in sorted(by_duration.keys()):
        print(f"\n{'─' * 60}")
        print(f"Duration: {dur} ({len(by_duration[dur])} files)")
        print(f"{'─' * 60}")

        total_rounds = 0
        total_snapshots = 0
        outcome_counts = Counter()
        book_01_99_count = 0
        book_real_count = 0
        book_empty_count = 0
        trade_price_available = 0
        trade_price_missing = 0
        last_trade_side_counts = Counter()
        midpoint_050_count = 0
        midpoint_real_count = 0

        # Track stale books
        stale_book_streaks = []
        # Track down_bid/down_ask quality
        down_bid_has_value = 0
        down_ask_has_value = 0
        down_always_01 = 0

        for csv_file in by_duration[dur]:
            with open(csv_file) as f:
                reader = csv.DictReader(f)
                prev_up_bid = None
                prev_up_ask = None
                stale_count = 0

                for row in reader:
                    row_type = row.get("row_type", "")

                    if row_type == "round_end":
                        total_rounds += 1
                        outcome = row.get("outcome", "unknown")
                        outcome_counts[outcome] += 1
                        if stale_count > 0:
                            stale_book_streaks.append(stale_count)
                        stale_count = 0
                        prev_up_bid = None
                        prev_up_ask = None
                        continue

                    if row_type == "snapshot":
                        total_snapshots += 1

                        up_bid = row.get("up_bid", "")
                        up_ask = row.get("up_ask", "")
                        down_bid = row.get("down_bid", "")
                        down_ask = row.get("down_ask", "")
                        midpoint = row.get("up_midpoint", "")
                        last_trade = row.get("last_trade_price", "")
                        last_side = row.get("last_trade_side", "")

                        # Book quality
                        if up_bid == "0.01" and up_ask == "0.99":
                            book_01_99_count += 1
                        elif up_bid and up_ask and up_bid != "" and up_ask != "":
                            book_real_count += 1
                        else:
                            book_empty_count += 1

                        # Midpoint quality
                        if midpoint:
                            mid_val = dec(midpoint)
                            if mid_val is not None:
                                if abs(mid_val - Decimal("0.50")) < Decimal("0.01"):
                                    midpoint_050_count += 1
                                else:
                                    midpoint_real_count += 1

                        # Trade price
                        if last_trade and last_trade != "":
                            trade_price_available += 1
                        else:
                            trade_price_missing += 1

                        if last_side:
                            last_trade_side_counts[last_side] += 1

                        # Down token quality
                        if down_bid and down_bid != "":
                            down_bid_has_value += 1
                            if down_bid == "0.01":
                                down_always_01 += 1
                        if down_ask and down_ask != "":
                            down_ask_has_value += 1

                        # Book staleness
                        if up_bid == prev_up_bid and up_ask == prev_up_ask and up_bid:
                            stale_count += 1
                        else:
                            if stale_count > 0:
                                stale_book_streaks.append(stale_count)
                            stale_count = 0
                        prev_up_bid = up_bid
                        prev_up_ask = up_ask

        print(f"  Rounds: {total_rounds}, Snapshots: {total_snapshots}")
        print(f"  Avg snapshots/round: {total_snapshots / max(total_rounds, 1):.0f}")

        print(f"\n  Outcomes:")
        for k, v in sorted(outcome_counts.items()):
            print(f"    {k}: {v} ({v/max(total_rounds,1)*100:.1f}%)")

        unknown_pct = outcome_counts.get("unknown", 0) / max(total_rounds, 1) * 100
        print(f"  ** Unknown rate: {unknown_pct:.1f}% **")

        print(f"\n  Book quality:")
        print(f"    0.01/0.99 (empty): {book_01_99_count} ({book_01_99_count/max(total_snapshots,1)*100:.1f}%)")
        print(f"    Real spread: {book_real_count} ({book_real_count/max(total_snapshots,1)*100:.1f}%)")
        print(f"    No data: {book_empty_count} ({book_empty_count/max(total_snapshots,1)*100:.1f}%)")

        print(f"\n  Midpoint quality:")
        print(f"    ~0.50 (useless): {midpoint_050_count} ({midpoint_050_count/max(total_snapshots,1)*100:.1f}%)")
        print(f"    Real midpoint: {midpoint_real_count} ({midpoint_real_count/max(total_snapshots,1)*100:.1f}%)")

        print(f"\n  Last trade price:")
        print(f"    Available: {trade_price_available} ({trade_price_available/max(total_snapshots,1)*100:.1f}%)")
        print(f"    Missing: {trade_price_missing} ({trade_price_missing/max(total_snapshots,1)*100:.1f}%)")
        print(f"    Side distribution: {dict(last_trade_side_counts)}")

        print(f"\n  Down token data:")
        print(f"    down_bid present: {down_bid_has_value} ({down_bid_has_value/max(total_snapshots,1)*100:.1f}%)")
        print(f"    down_bid always 0.01: {down_always_01} ({down_always_01/max(down_bid_has_value,1)*100:.1f}%)")
        print(f"    down_ask present: {down_ask_has_value} ({down_ask_has_value/max(total_snapshots,1)*100:.1f}%)")

        if stale_book_streaks:
            print(f"\n  Book staleness (consecutive unchanged snapshots):")
            print(f"    Median streak: {sorted(stale_book_streaks)[len(stale_book_streaks)//2]}s")
            print(f"    Max streak: {max(stale_book_streaks)}s")
            p90 = sorted(stale_book_streaks)[int(len(stale_book_streaks)*0.9)]
            print(f"    P90 streak: {p90}s")


# ============================================================
# PM best_bid_ask BUG ANALYSIS
# ============================================================
def verify_pm_best_bid_ask_bug():
    """Deep dive into the best_bid_ask event → book update pipeline.

    The WS handler for best_bid_ask events sets asset_id="" (line 277).
    The collector (line 433) has: if bu.asset_id == up_token_id or not bu.asset_id
    This means ALL best_bid_ask events go to the UP token bucket.

    But best_bid_ask events are per-market (not per-token).
    The WS subscribes to both token IDs.

    Key question: does the server send separate best_bid_ask events per token,
    or one per market? If per-market, we're mixing UP and DOWN book data.
    """
    print("\n" + "=" * 80)
    print("PM best_bid_ask BUG ANALYSIS")
    print("=" * 80)

    print("""
FINDING: The PM WS handler for 'best_bid_ask' events creates PolymarketBookUpdate
with asset_id="" (polymarket.py line 277). This is because the best_bid_ask event
format does NOT include an asset_id field.

In the collector (collect_polymarket.py line 433):
    if bu.asset_id == up_token_id or not bu.asset_id:
        # → True when asset_id is "" (empty string is falsy)
        best_up_bid = bu.best_bid
        best_up_ask = bu.best_ask

This means ALL best_bid_ask events (regardless of which token they're for)
are assigned to the UP token.

The 'book' events DO include asset_id (polymarket.py line 232), so initial
snapshots are correctly attributed. But subsequent best_bid_ask updates
(which are the frequent ones) are all dumped into UP.

IMPACT ANALYSIS:
Looking at the data, up_bid/up_ask is almost always 0.01/0.99. This tells us:
1. The initial 'book' snapshot correctly shows the UP token book (thin, 0.01/0.99)
2. When a best_bid_ask event arrives (potentially for the DOWN token), it
   OVERWRITES the UP book data with the DOWN token's bid/ask
3. But then the next event (possibly for UP again) overwrites back to 0.01/0.99

The data shows occasional "real" books (0.45/0.47, 0.48/0.49) popping up
for 1-2 snapshots then reverting to 0.01/0.99. These are likely DOWN token
best_bid_ask events being misattributed to UP.

SEVERITY: MODERATE
- up_midpoint is already known to be useless (0.01/0.99 → 0.50)
- last_trade_price is tracked separately and is NOT affected
- The intermittent "real" book values in the data are likely DOWN token values
  mistakenly assigned to UP — this means any analysis using up_bid/up_ask
  intra-round is unreliable
    """)


# ============================================================
# PM TRADE TRACKING BUG
# ============================================================
def verify_pm_trade_tracking():
    """Analyze the impact of only tracking UP token trades."""
    print("\n" + "=" * 80)
    print("PM TRADE TRACKING ANALYSIS")
    print("=" * 80)

    print("""
BUG: collect_polymarket.py line 455:
    if tu.asset_id == up_token_id:
        last_trade_price = tu.price
        last_trade_side = tu.side

This ONLY records UP token trades. DOWN token trades are silently dropped.

IMPACT:
1. last_trade_price is always the UP token's last trade price. This is
   actually CONSISTENT — as long as we interpret it as "probability of UP".
   If UP last traded at 0.60, that's the market's latest read on P(up) = 60%.

2. However, we MISS information when only the DOWN token trades:
   - If DOWN trades at 0.60, that implies UP ≈ 0.40
   - We could infer UP price from DOWN trades as (1 - down_price)
   - Currently these gaps show up as stale last_trade_price

3. For outcome determination (lines 502-511): if last_trade_price >= 0.90 → "up"
   This works correctly IF the last trade happens to be an UP token trade near
   settlement. But if the last trade was a DOWN token trade at 0.95 (meaning
   UP ≈ 0.05), we'd miss it entirely and fall through to the bid/ask check
   or get "unknown".
    """)

    # Quantify: look at rounds where outcome is "unknown" and check if
    # there's a last_trade_price that's ambiguous (0.10-0.90)
    csv_files = sorted(PM_DIR.glob("*-15m-*.csv"))
    unknown_with_trade = 0
    unknown_no_trade = 0
    unknown_ambiguous = 0
    total_unknown = 0

    for csv_file in csv_files:
        with open(csv_file) as f:
            reader = csv.DictReader(f)
            last_trade = None
            for row in reader:
                if row.get("row_type") == "snapshot":
                    tp = row.get("last_trade_price", "")
                    if tp:
                        last_trade = dec(tp)
                elif row.get("row_type") == "round_end":
                    if row.get("outcome") == "unknown":
                        total_unknown += 1
                        if last_trade is not None:
                            unknown_with_trade += 1
                            if Decimal("0.10") < last_trade < Decimal("0.90"):
                                unknown_ambiguous += 1
                        else:
                            unknown_no_trade += 1
                    last_trade = None

    print(f"\n  15m rounds with unknown outcome: {total_unknown}")
    print(f"    Had a last_trade_price: {unknown_with_trade}")
    print(f"    No trade price at all: {unknown_no_trade}")
    print(f"    Trade price in ambiguous range (0.10-0.90): {unknown_ambiguous}")


# ============================================================
# PM OUTCOME VERIFICATION
# ============================================================
def verify_pm_outcomes():
    """Compare PM outcomes against spot price direction."""
    print("\n" + "=" * 80)
    print("PM OUTCOME vs SPOT VERIFICATION")
    print("=" * 80)

    csv_files = sorted(PM_DIR.glob("*-15m-*.csv"))

    agree = 0
    disagree = 0
    no_data = 0
    disagree_details = []

    for csv_file in csv_files:
        with open(csv_file) as f:
            reader = csv.DictReader(f)
            round_spots = []
            round_slug = ""

            for row in reader:
                slug = row.get("slug", "")
                if slug != round_slug:
                    round_spots = []
                    round_slug = slug

                if row.get("row_type") == "snapshot":
                    spot = row.get("spot_price", "")
                    if spot:
                        round_spots.append(dec(spot))

                elif row.get("row_type") == "round_end":
                    outcome = row.get("outcome", "unknown")
                    spot_end = row.get("spot_price", "")

                    if outcome == "unknown" or not round_spots or not spot_end:
                        no_data += 1
                        continue

                    first_spot = round_spots[0]
                    end_spot = dec(spot_end)
                    if first_spot is None or end_spot is None:
                        no_data += 1
                        continue

                    # PM "up" means price went up from round start
                    spot_direction = "up" if end_spot >= first_spot else "down"

                    # Note: PM resolves on Chainlink/Binance, not Coinbase
                    # So disagreement here is expected when sources diverge
                    if spot_direction == outcome:
                        agree += 1
                    else:
                        disagree += 1
                        if len(disagree_details) < 20:
                            disagree_details.append({
                                "slug": round_slug,
                                "outcome": outcome,
                                "spot_dir": spot_direction,
                                "first_spot": first_spot,
                                "end_spot": end_spot,
                                "diff_pct": float((end_spot - first_spot) / first_spot * 100),
                            })

    total = agree + disagree
    print(f"\n  PM 15m outcome vs Coinbase spot direction:")
    print(f"    Agree: {agree} ({agree/max(total,1)*100:.1f}%)")
    print(f"    Disagree: {disagree} ({disagree/max(total,1)*100:.1f}%)")
    print(f"    No data: {no_data}")

    if disagree_details:
        print(f"\n  Disagreement examples (PM outcome != Coinbase direction):")
        small_diff = sum(1 for d in disagree_details if abs(d["diff_pct"]) < 0.05)
        print(f"    Within 0.05% of flat: {small_diff}/{len(disagree_details)}")
        for d in disagree_details[:10]:
            print(f"    {d['slug']}: PM={d['outcome']} CB={d['spot_dir']} "
                  f"diff={d['diff_pct']:.4f}%")


# ============================================================
# CROSS-PLATFORM TIME ALIGNMENT
# ============================================================
def verify_cross_platform():
    """Check that Kalshi and PM rounds actually overlap in time."""
    print("\n" + "=" * 80)
    print("CROSS-PLATFORM TIME ALIGNMENT")
    print("=" * 80)

    # Collect Kalshi round start times
    kalshi_rounds = {}  # ticker -> (first_ts, last_ts)
    for csv_file in sorted(KALSHI_DIR.glob("KXBTC15M-*.csv")):
        with open(csv_file) as f:
            reader = csv.DictReader(f)
            current = None
            first_ts = None
            for row in reader:
                ticker = row.get("round_ticker", "")
                ts = parse_ts(row.get("timestamp", ""))
                if ticker != current:
                    current = ticker
                    first_ts = ts
                if row.get("row_type") == "round_end" and first_ts:
                    kalshi_rounds[ticker] = (first_ts, ts)

    # Collect PM round start times (15m only, BTC)
    pm_rounds = {}  # slug -> (first_ts, last_ts, end_date)
    for csv_file in sorted(PM_DIR.glob("BTC-15m-*.csv")):
        with open(csv_file) as f:
            reader = csv.DictReader(f)
            current = None
            first_ts = None
            for row in reader:
                slug = row.get("slug", "")
                ts = parse_ts(row.get("timestamp", ""))
                if slug != current:
                    current = slug
                    first_ts = ts
                if row.get("row_type") == "round_end" and first_ts:
                    pm_rounds[slug] = (first_ts, ts, row.get("end_date", ""))

    print(f"\n  Kalshi BTC rounds: {len(kalshi_rounds)}")
    print(f"  PM BTC 15m rounds: {len(pm_rounds)}")

    # Match by overlapping time windows
    matches = 0
    unmatched_kalshi = 0

    for kticker, (k_start, k_end) in sorted(kalshi_rounds.items()):
        found = False
        for pslug, (p_start, p_end, p_end_date) in pm_rounds.items():
            # Check if timestamps overlap
            if abs((k_start - p_start).total_seconds()) < 120:
                matches += 1
                found = True
                break
        if not found:
            unmatched_kalshi += 1

    print(f"  Matched by timestamp (±120s): {matches}")
    print(f"  Unmatched Kalshi rounds: {unmatched_kalshi}")

    # Check if PM end_date aligns with Kalshi round boundaries
    # Kalshi rounds are on :00, :15, :30, :45
    # PM rounds should be too
    pm_end_minutes = Counter()
    for slug, (_, _, end_date) in pm_rounds.items():
        dt = parse_ts(end_date)
        if dt:
            pm_end_minutes[dt.minute] += 1

    print(f"\n  PM round end minutes: {dict(sorted(pm_end_minutes.items()))}")
    print("  (Should be 0, 15, 30, 45 for 15m rounds)")


# ============================================================
# KALSHI OUTCOME vs SETTLEMENT VERIFICATION
# ============================================================
def verify_kalshi_outcomes_vs_spot():
    """Check how often our recorded outcome matches spot-based inference."""
    print("\n" + "=" * 80)
    print("KALSHI OUTCOME CONSISTENCY CHECK")
    print("=" * 80)

    outcome_source = Counter()  # api vs spot-inferred
    close_calls = []  # rounds where spot was very close to strike

    for csv_file in sorted(KALSHI_DIR.glob("KX*15M-*.csv")):
        with open(csv_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("row_type") != "round_end":
                    continue

                outcome = row.get("outcome", "unknown")
                spot = dec(row.get("spot_price", ""))
                strike = dec(row.get("strike", ""))

                if spot and strike:
                    spot_inferred = "yes" if spot >= strike else "no"
                    dist_pct = abs(float((spot - strike) / strike * 100))

                    if outcome == spot_inferred:
                        outcome_source["api_agrees_spot"] += 1
                    else:
                        outcome_source["api_disagrees_spot"] += 1
                        close_calls.append({
                            "ticker": row.get("round_ticker", ""),
                            "outcome": outcome,
                            "spot_inferred": spot_inferred,
                            "spot": spot,
                            "strike": strike,
                            "dist_pct": dist_pct,
                            "kraken": row.get("kraken_spot", ""),
                        })

                    if dist_pct < 0.02:
                        outcome_source["very_close_to_strike"] += 1
                else:
                    outcome_source["missing_data"] += 1

    print(f"\n  Outcome consistency:")
    for k, v in sorted(outcome_source.items()):
        print(f"    {k}: {v}")

    if close_calls:
        print(f"\n  Rounds where API outcome != spot-inferred ({len(close_calls)}):")
        print("  (These are expected — Kalshi uses CF Benchmarks 60s TWAP, not spot)")
        for cc in close_calls[:15]:
            print(f"    {cc['ticker']}: API={cc['outcome']} spot_says={cc['spot_inferred']} "
                  f"dist={cc['dist_pct']:.4f}% kraken={cc['kraken']}")


# ============================================================
# PM last_trade_price DISTRIBUTION
# ============================================================
def verify_pm_trade_prices():
    """Analyze the distribution and behavior of PM last_trade_price."""
    print("\n" + "=" * 80)
    print("PM last_trade_price ANALYSIS")
    print("=" * 80)

    for dur in ["5m", "15m"]:
        csv_files = sorted(PM_DIR.glob(f"*-{dur}-*.csv"))
        if not csv_files:
            continue

        print(f"\n  Duration: {dur}")

        price_buckets = Counter()
        final_prices = []  # last_trade_price at round end
        price_changes_per_round = []
        rounds_with_trades = 0
        rounds_without_trades = 0

        for csv_file in csv_files:
            with open(csv_file) as f:
                reader = csv.DictReader(f)
                last_price = None
                n_changes = 0
                had_trade = False
                prev_price = None

                for row in reader:
                    if row.get("row_type") == "snapshot":
                        tp = row.get("last_trade_price", "")
                        if tp:
                            p = dec(tp)
                            if p is not None:
                                had_trade = True
                                if p != prev_price:
                                    n_changes += 1
                                prev_price = p
                                last_price = p
                                # Bucket
                                bucket = int(float(p) * 10) / 10
                                price_buckets[f"{bucket:.1f}"] += 1

                    elif row.get("row_type") == "round_end":
                        if had_trade:
                            rounds_with_trades += 1
                            if last_price is not None:
                                final_prices.append(float(last_price))
                            price_changes_per_round.append(n_changes)
                        else:
                            rounds_without_trades += 1
                        last_price = None
                        n_changes = 0
                        had_trade = False
                        prev_price = None

        print(f"    Rounds with trades: {rounds_with_trades}")
        print(f"    Rounds without trades: {rounds_without_trades}")

        if price_changes_per_round:
            avg_changes = sum(price_changes_per_round) / len(price_changes_per_round)
            print(f"    Avg unique price changes per round: {avg_changes:.1f}")

        if final_prices:
            import statistics
            print(f"    Final trade price distribution:")
            print(f"      Mean: {statistics.mean(final_prices):.3f}")
            print(f"      Median: {statistics.median(final_prices):.3f}")
            # Show how many settled near extremes
            near_0 = sum(1 for p in final_prices if p <= 0.10)
            near_1 = sum(1 for p in final_prices if p >= 0.90)
            mid = sum(1 for p in final_prices if 0.10 < p < 0.90)
            print(f"      <=0.10: {near_0} ({near_0/len(final_prices)*100:.1f}%)")
            print(f"      0.10-0.90: {mid} ({mid/len(final_prices)*100:.1f}%)")
            print(f"      >=0.90: {near_1} ({near_1/len(final_prices)*100:.1f}%)")

        print(f"\n    Price bucket distribution (snapshot-level):")
        for bucket in sorted(price_buckets.keys()):
            count = price_buckets[bucket]
            bar = "#" * min(count // 100, 50)
            print(f"      {bucket}: {count:>6} {bar}")


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    verify_kalshi()
    verify_kalshi_outcomes_vs_spot()
    verify_polymarket()
    verify_pm_best_bid_ask_bug()
    verify_pm_trade_tracking()
    verify_pm_outcomes()
    verify_pm_trade_prices()
    verify_cross_platform()
