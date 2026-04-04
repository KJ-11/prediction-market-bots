"""Cross-platform validation: Kalshi vs Polymarket 15-minute BTC rounds.

Matches rounds by time window, analyzes outcome agreement, pricing
discrepancies, and strangle opportunities.
"""
from __future__ import annotations

import glob
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

# ── Constants ──────────────────────────────────────────────────────────

DATA_DIR = "data/rounds"
KALSHI_GLOB = f"{DATA_DIR}/KXBTC15M-*.csv"
PM_GLOB = f"{DATA_DIR}/polymarket/BTC-15m-*.csv"

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 42


# ── Helpers ────────────────────────────────────────────────────────────

def parse_kalshi_end_time(ticker: str) -> datetime:
    """Parse Kalshi ticker to get round end time (UTC).

    Format: KXBTC15M-26MAR092015-15
    - 26 = year (2026)
    - MAR = month
    - 09 = day
    - 2015 = HHMM (20:15)
    - -15 = end minute (redundant with HHMM for 15m rounds)
    """
    parts = ticker.split("-")
    date_chunk = parts[1]  # 26MAR092015
    year = 2000 + int(date_chunk[:2])
    month_str = date_chunk[2:5]
    month = MONTH_MAP[month_str]
    day = int(date_chunk[5:7])
    hh = int(date_chunk[7:9])
    mm = int(date_chunk[9:11])
    return datetime(year, month, day, hh, mm, tzinfo=timezone.utc)


def parse_pm_end_time(slug: str) -> datetime:
    """Parse PM slug to get round end time (UTC).

    Format: btc-updown-15m-{unix_timestamp}
    """
    ts = int(slug.rsplit("-", 1)[1])
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def bootstrap_ci(values: np.ndarray, stat_fn=np.mean, n_boot: int = BOOTSTRAP_N,
                 seed: int = BOOTSTRAP_SEED, ci: float = 0.95) -> tuple[float, float, float]:
    """Return (point_estimate, ci_low, ci_high) via bootstrap."""
    rng = np.random.RandomState(seed)
    point = float(stat_fn(values))
    if len(values) < 3:
        return point, float("nan"), float("nan")
    boot_stats = []
    for _ in range(n_boot):
        sample = rng.choice(values, size=len(values), replace=True)
        boot_stats.append(float(stat_fn(sample)))
    alpha = (1 - ci) / 2
    lo = float(np.percentile(boot_stats, alpha * 100))
    hi = float(np.percentile(boot_stats, (1 - alpha) * 100))
    return point, lo, hi


def fmt_pct(val: float) -> str:
    return f"{val * 100:.1f}%"


def fmt_ci(point: float, lo: float, hi: float, as_pct: bool = True) -> str:
    if as_pct:
        return f"{point*100:.1f}% [{lo*100:.1f}%, {hi*100:.1f}%]"
    return f"{point:.4f} [{lo:.4f}, {hi:.4f}]"


# ── Load Data ──────────────────────────────────────────────────────────

def load_kalshi() -> pd.DataFrame:
    files = sorted(glob.glob(KALSHI_GLOB))
    if not files:
        raise FileNotFoundError(f"No Kalshi files matching {KALSHI_GLOB}")
    frames = []
    for f in files:
        df = pd.read_csv(f, dtype=str)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)

    num_cols = ["strike", "seconds_remaining", "seconds_elapsed", "spot_price",
                "yes_bid", "yes_ask", "no_bid", "no_ask", "volume",
                "spot_minus_strike", "spot_move_pct", "kraken_spot"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df


def load_pm() -> pd.DataFrame:
    files = sorted(glob.glob(PM_GLOB))
    if not files:
        raise FileNotFoundError(f"No PM files matching {PM_GLOB}")
    frames = []
    for f in files:
        df = pd.read_csv(f, on_bad_lines="skip")
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)

    num_cols = ["seconds_remaining", "up_bid", "up_ask", "down_bid", "down_ask",
                "up_midpoint", "spread", "last_trade_price", "spot_price",
                "kraken_price", "rtds_price", "volume"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df


# ── Section 1: Match Rounds ───────────────────────────────────────────

def build_round_summaries(kalshi_df: pd.DataFrame, pm_df: pd.DataFrame):
    """Build per-round summary for each platform, then match by end time."""

    # Kalshi: group by round_ticker
    k_rounds = []
    for ticker, grp in kalshi_df.groupby("round_ticker"):
        end_time = parse_kalshi_end_time(ticker)
        # Get outcome from round_end row
        end_rows = grp[grp["row_type"] == "round_end"]
        outcome = end_rows["outcome"].iloc[0] if len(end_rows) > 0 else None
        strike = grp["strike"].dropna().iloc[0] if grp["strike"].notna().any() else None
        # First snapshot spot price (approximation of round start reference)
        snapshots = grp[grp["row_type"] == "snapshot"].sort_values("ts")
        first_spot = snapshots["spot_price"].iloc[0] if len(snapshots) > 0 else None
        k_rounds.append({
            "ticker": ticker,
            "end_time": end_time,
            "outcome": outcome,
            "strike": strike,
            "first_spot": first_spot,
            "data": grp,
        })

    # PM: group by slug
    p_rounds = []
    for slug, grp in pm_df.groupby("slug"):
        try:
            end_time = parse_pm_end_time(slug)
        except (ValueError, IndexError):
            continue
        end_rows = grp[grp["row_type"] == "round_end"]
        outcome = end_rows["outcome"].iloc[0] if len(end_rows) > 0 else None
        if outcome == "unknown":
            outcome = None
        # First snapshot with rtds_price as reference price
        snapshots = grp[grp["row_type"] == "snapshot"].sort_values("ts")
        rtds_vals = snapshots["rtds_price"].dropna()
        first_rtds = rtds_vals.iloc[0] if len(rtds_vals) > 0 else None
        first_spot = snapshots["spot_price"].dropna().iloc[0] if snapshots["spot_price"].notna().any() else None
        p_rounds.append({
            "slug": slug,
            "end_time": end_time,
            "outcome": outcome,
            "first_rtds": first_rtds,
            "first_spot": first_spot,
            "data": grp,
        })

    return k_rounds, p_rounds


def match_rounds(k_rounds, p_rounds, max_delta_s=60):
    """Match Kalshi and PM rounds whose end times are within max_delta_s seconds."""
    matched = []
    # Build index by end_time for PM
    pm_by_end = {}
    for pr in p_rounds:
        pm_by_end.setdefault(pr["end_time"], []).append(pr)

    for kr in k_rounds:
        k_end = kr["end_time"]
        # Check exact match and neighbors
        for offset in range(-1, 2):  # -60s, 0, +60s
            candidate_time = k_end + timedelta(seconds=offset * 60)
            if candidate_time in pm_by_end:
                for pr in pm_by_end[candidate_time]:
                    delta = abs((k_end - pr["end_time"]).total_seconds())
                    if delta <= max_delta_s:
                        matched.append((kr, pr, delta))
                        break
                break  # only match one PM round per Kalshi round

    return matched


# ── Analysis ───────────────────────────────────────────────────────────

def section_1_match_summary(matched, k_rounds, p_rounds):
    print("=" * 70)
    print("SECTION 1: ROUND MATCHING")
    print("=" * 70)
    print(f"  Kalshi rounds total:       {len(k_rounds):>6}")
    print(f"  Polymarket rounds total:   {len(p_rounds):>6}")
    print(f"  Matched pairs:             {len(matched):>6}")
    if matched:
        deltas = [m[2] for m in matched]
        print(f"  Match delta (seconds):     mean={np.mean(deltas):.1f}  max={max(deltas):.1f}")

    # Date coverage
    k_dates = sorted(set(kr["end_time"].date() for kr in k_rounds))
    p_dates = sorted(set(pr["end_time"].date() for pr in p_rounds))
    overlap_dates = sorted(set(k_dates) & set(p_dates))
    print(f"  Kalshi date range:         {k_dates[0]} to {k_dates[-1]} ({len(k_dates)} days)")
    print(f"  PM date range:             {p_dates[0]} to {p_dates[-1]} ({len(p_dates)} days)")
    print(f"  Overlapping dates:         {len(overlap_dates)} days")
    print()


def section_2_outcome_agreement(matched):
    print("=" * 70)
    print("SECTION 2: OUTCOME AGREEMENT (Kalshi vs Polymarket)")
    print("=" * 70)

    # Map outcomes to a common direction
    pairs_with_outcome = []
    for kr, pr, delta in matched:
        k_out = kr["outcome"]
        p_out = pr["outcome"]
        if k_out in ("yes", "no") and p_out in ("up", "down"):
            # Kalshi yes = above strike, PM up = above reference
            k_direction = "above" if k_out == "yes" else "below"
            p_direction = "above" if p_out == "up" else "below"
            pairs_with_outcome.append((k_direction, p_direction, kr, pr))

    if not pairs_with_outcome:
        print("  No matched pairs with known outcomes on both sides.")
        print()
        return

    agree = sum(1 for k, p, _, _ in pairs_with_outcome if k == p)
    total = len(pairs_with_outcome)
    agreement_arr = np.array([1 if k == p else 0 for k, p, _, _ in pairs_with_outcome])

    point, lo, hi = bootstrap_ci(agreement_arr)
    print(f"  Pairs with both outcomes:  {total}")
    print(f"  Agreement rate:            {fmt_ci(point, lo, hi)}")
    print()

    # Disagreement breakdown
    disagree = [(k, p, kr, pr) for k, p, kr, pr in pairs_with_outcome if k != p]
    if disagree:
        print(f"  Disagreements ({len(disagree)} rounds):")
        print(f"  {'Kalshi End':<22} {'Kalshi':<10} {'PM':<10} {'K-Strike':>10} {'PM-Ref':>10} {'Gap':>10}")
        for k_dir, p_dir, kr, pr in disagree[:20]:
            k_strike = kr["strike"]
            p_ref = pr["first_rtds"]
            gap = (k_strike - p_ref) if (k_strike and p_ref) else None
            gap_str = f"${gap:+.2f}" if gap is not None else "N/A"
            k_str = f"${k_strike:,.2f}" if k_strike else "N/A"
            p_str = f"${p_ref:,.2f}" if p_ref else "N/A"
            print(f"  {str(kr['end_time']):<22} {k_dir:<10} {p_dir:<10} {k_str:>10} {p_str:>10} {gap_str:>10}")
        if len(disagree) > 20:
            print(f"  ... and {len(disagree) - 20} more")
    print()


def section_3_strike_comparison(matched):
    print("=" * 70)
    print("SECTION 3: STRIKE / REFERENCE PRICE COMPARISON")
    print("=" * 70)

    gaps = []
    for kr, pr, delta in matched:
        k_strike = kr["strike"]
        p_ref = pr["first_rtds"]
        if k_strike is not None and p_ref is not None and not np.isnan(k_strike) and not np.isnan(p_ref):
            gaps.append({
                "k_strike": k_strike,
                "p_ref": p_ref,
                "gap": k_strike - p_ref,
                "gap_pct": (k_strike - p_ref) / k_strike * 100,
                "k_outcome": kr["outcome"],
                "p_outcome": pr["outcome"],
                "end_time": kr["end_time"],
            })

    if not gaps:
        print("  No pairs with both strike and reference price available.")
        print()
        return

    gap_df = pd.DataFrame(gaps)
    gap_arr = gap_df["gap"].values
    gap_pct_arr = gap_df["gap_pct"].values
    abs_gap = np.abs(gap_arr)

    print(f"  Pairs with both prices:    {len(gaps)}")
    print()
    print("  Gap (Kalshi strike - PM reference):")
    print(f"    Mean:       ${np.mean(gap_arr):>+10.2f}  ({np.mean(gap_pct_arr):>+.4f}%)")
    print(f"    Median:     ${np.median(gap_arr):>+10.2f}  ({np.median(gap_pct_arr):>+.4f}%)")
    print(f"    Std:        ${np.std(gap_arr):>10.2f}  ({np.std(gap_pct_arr):>.4f}%)")
    print(f"    Min:        ${np.min(gap_arr):>+10.2f}")
    print(f"    Max:        ${np.max(gap_arr):>+10.2f}")
    print()

    # Distribution of absolute gap
    thresholds = [0, 5, 10, 25, 50, 100, 200, 500]
    print("  Absolute gap distribution:")
    for i in range(len(thresholds) - 1):
        lo_t, hi_t = thresholds[i], thresholds[i + 1]
        count = np.sum((abs_gap >= lo_t) & (abs_gap < hi_t))
        pct = count / len(abs_gap) * 100
        print(f"    ${lo_t:>4} - ${hi_t:<4}:  {count:>5} ({pct:>5.1f}%)")
    count_big = np.sum(abs_gap >= thresholds[-1])
    print(f"    ${thresholds[-1]:>4}+:       {count_big:>5} ({count_big/len(abs_gap)*100:>5.1f}%)")
    print()

    # When gap is large, who "wins"?
    # If Kalshi strike > PM ref (gap > 0):
    #   Price in gap (between PM ref and Kalshi strike) → Kalshi NO + PM UP
    #   Price above both → Kalshi YES + PM UP (agree)
    #   Price below both → Kalshi NO + PM DOWN (agree)
    valid_outcomes = gap_df[
        gap_df["k_outcome"].isin(["yes", "no"]) & gap_df["p_outcome"].isin(["up", "down"])
    ].copy()

    if len(valid_outcomes) > 0:
        valid_outcomes["in_gap"] = False
        # Approximate: if they disagree, price likely landed in the gap
        valid_outcomes["k_dir"] = valid_outcomes["k_outcome"].map({"yes": "above", "no": "below"})
        valid_outcomes["p_dir"] = valid_outcomes["p_outcome"].map({"up": "above", "down": "below"})
        valid_outcomes["disagree"] = valid_outcomes["k_dir"] != valid_outcomes["p_dir"]

        # For large gaps (>$25), check disagreement rate
        for threshold in [10, 25, 50, 100]:
            subset = valid_outcomes[valid_outcomes["gap"].abs() >= threshold]
            if len(subset) > 0:
                disagree_rate = subset["disagree"].mean()
                print(f"  Gap >= ${threshold}: {len(subset)} rounds, disagree rate = {fmt_pct(disagree_rate)}")
        print()


def section_4_pricing_discrepancies(matched):
    print("=" * 70)
    print("SECTION 4: PRICING DISCREPANCIES (Strangle Detection)")
    print("=" * 70)

    # For each matched pair, find snapshots at similar seconds_remaining
    # and check if Kalshi yes_ask + PM down_ask < 1.00 or Kalshi no_ask + PM up_ask < 1.00

    strangle_opps = []
    all_combos = []

    for kr, pr, delta in matched:
        k_data = kr["data"]
        p_data = pr["data"]

        k_snaps = k_data[k_data["row_type"] == "snapshot"].copy()
        p_snaps = p_data[p_data["row_type"] == "snapshot"].copy()

        if len(k_snaps) == 0 or len(p_snaps) == 0:
            continue

        # Bucket by seconds_remaining (10-second buckets)
        k_snaps = k_snaps.dropna(subset=["seconds_remaining", "yes_ask", "no_ask"])
        p_snaps = p_snaps.dropna(subset=["seconds_remaining", "up_ask", "down_ask"])

        if len(k_snaps) == 0 or len(p_snaps) == 0:
            continue

        k_snaps["sr_bucket"] = (k_snaps["seconds_remaining"] // 30).astype(int)
        p_snaps["sr_bucket"] = (p_snaps["seconds_remaining"] // 30).astype(int)

        # For each overlapping bucket, take median prices
        k_med = k_snaps.groupby("sr_bucket").agg(
            k_yes_ask=("yes_ask", "median"),
            k_no_ask=("no_ask", "median"),
            k_sr=("seconds_remaining", "median"),
        )
        p_med = p_snaps.groupby("sr_bucket").agg(
            p_up_ask=("up_ask", "median"),
            p_down_ask=("down_ask", "median"),
            p_sr=("seconds_remaining", "median"),
        )

        merged = k_med.join(p_med, how="inner")
        if len(merged) == 0:
            continue

        for bucket, row in merged.iterrows():
            # Strangle 1: Kalshi YES + PM DOWN
            combo1 = row["k_yes_ask"] + row["p_down_ask"]
            # Strangle 2: Kalshi NO + PM UP
            combo2 = row["k_no_ask"] + row["p_up_ask"]

            sr = row["k_sr"]

            all_combos.append({
                "end_time": kr["end_time"],
                "sr_bucket": bucket,
                "sr": sr,
                "k_yes_ask": row["k_yes_ask"],
                "k_no_ask": row["k_no_ask"],
                "p_up_ask": row["p_up_ask"],
                "p_down_ask": row["p_down_ask"],
                "combo_yes_down": combo1,
                "combo_no_up": combo2,
                "min_combo": min(combo1, combo2),
            })

            if combo1 < 1.0 or combo2 < 1.0:
                strangle_opps.append({
                    "end_time": kr["end_time"],
                    "sr": sr,
                    "type": "YES+DOWN" if combo1 < combo2 else "NO+UP",
                    "cost": min(combo1, combo2),
                    "k_yes_ask": row["k_yes_ask"],
                    "k_no_ask": row["k_no_ask"],
                    "p_up_ask": row["p_up_ask"],
                    "p_down_ask": row["p_down_ask"],
                })

    if not all_combos:
        print("  No overlapping price data found.")
        print()
        return all_combos

    combo_df = pd.DataFrame(all_combos)

    print(f"  Total price comparison points: {len(combo_df)}")
    print()

    # Summary of combined costs
    print("  Combined cost (Kalshi YES ask + PM DOWN ask):")
    arr = combo_df["combo_yes_down"].dropna().values
    if len(arr) > 0:
        point, lo, hi = bootstrap_ci(arr, stat_fn=np.mean)
        print(f"    Mean:   {fmt_ci(point, lo, hi, as_pct=False)}")
        print(f"    Median: {np.median(arr):.4f}")
        print(f"    Min:    {np.min(arr):.4f}")
        for t in [1.00, 1.02, 1.05]:
            below = np.sum(arr < t) / len(arr) * 100
            print(f"    Below {t:.2f}: {below:.1f}%")

    print()
    print("  Combined cost (Kalshi NO ask + PM UP ask):")
    arr2 = combo_df["combo_no_up"].dropna().values
    if len(arr2) > 0:
        point, lo, hi = bootstrap_ci(arr2, stat_fn=np.mean)
        print(f"    Mean:   {fmt_ci(point, lo, hi, as_pct=False)}")
        print(f"    Median: {np.median(arr2):.4f}")
        print(f"    Min:    {np.min(arr2):.4f}")
        for t in [1.00, 1.02, 1.05]:
            below = np.sum(arr2 < t) / len(arr2) * 100
            print(f"    Below {t:.2f}: {below:.1f}%")

    print()
    print("  Best combined cost (min of both strangles):")
    arr_min = combo_df["min_combo"].dropna().values
    print(f"    Mean:   {np.mean(arr_min):.4f}")
    print(f"    Median: {np.median(arr_min):.4f}")
    print(f"    Min:    {np.min(arr_min):.4f}")
    for t in [1.00, 1.02, 1.05]:
        below = np.sum(arr_min < t) / len(arr_min) * 100
        print(f"    Below {t:.2f}: {below:.1f}%")

    print()

    if strangle_opps:
        print(f"  *** ARBITRAGE OPPORTUNITIES FOUND: {len(strangle_opps)} ***")
        opp_df = pd.DataFrame(strangle_opps).sort_values("cost")
        print(f"  {'End Time':<22} {'SR':>6} {'Type':<10} {'Cost':>6} {'K_Yes':>6} {'K_No':>6} {'P_Up':>6} {'P_Dn':>6}")
        for _, row in opp_df.head(20).iterrows():
            print(f"  {str(row['end_time']):<22} {row['sr']:>6.0f} {row['type']:<10} {row['cost']:>6.4f} "
                  f"{row['k_yes_ask']:>6.4f} {row['k_no_ask']:>6.4f} {row['p_up_ask']:>6.4f} {row['p_down_ask']:>6.4f}")
        if len(opp_df) > 20:
            print(f"  ... and {len(opp_df) - 20} more")
    else:
        print("  No pure arbitrage opportunities (combined cost < $1.00) found.")

    # By time bucket
    print()
    print("  Min combined cost by seconds_remaining bucket:")
    time_buckets = [(0, 120), (120, 300), (300, 600), (600, 900)]
    for lo_s, hi_s in time_buckets:
        subset = combo_df[(combo_df["sr"] >= lo_s) & (combo_df["sr"] < hi_s)]
        if len(subset) > 0:
            print(f"    {lo_s:>4}-{hi_s:<4}s:  n={len(subset):>5}  "
                  f"min_combo mean={subset['min_combo'].mean():.4f}  "
                  f"min={subset['min_combo'].min():.4f}  "
                  f"below_1.05={fmt_pct((subset['min_combo'] < 1.05).mean())}")
    print()

    return all_combos


def section_5_strangle_gap(matched):
    print("=" * 70)
    print("SECTION 5: STRANGLE GAP ANALYSIS")
    print("=" * 70)

    gap_rounds = []
    for kr, pr, delta in matched:
        k_strike = kr["strike"]
        p_ref = pr["first_rtds"]
        k_out = kr["outcome"]
        p_out = pr["outcome"]

        if k_strike is None or p_ref is None or np.isnan(k_strike) or np.isnan(p_ref):
            continue

        gap = k_strike - p_ref  # positive = Kalshi strike above PM ref
        abs_gap = abs(gap)

        # Determine if price landed in the gap
        # If gap > 0 (K strike > PM ref): price in [PM_ref, K_strike] → K=NO, PM=UP
        # If gap < 0 (K strike < PM ref): price in [K_strike, PM_ref] → K=YES, PM=DOWN
        in_gap = False
        if k_out in ("yes", "no") and p_out in ("up", "down"):
            if gap > 0 and k_out == "no" and p_out == "up":
                in_gap = True  # price between PM_ref and K_strike
            elif gap < 0 and k_out == "yes" and p_out == "down":
                in_gap = True  # price between K_strike and PM_ref

        gap_rounds.append({
            "end_time": kr["end_time"],
            "k_strike": k_strike,
            "p_ref": p_ref,
            "gap": gap,
            "abs_gap": abs_gap,
            "k_outcome": k_out,
            "p_outcome": p_out,
            "in_gap": in_gap,
            "has_outcomes": k_out in ("yes", "no") and p_out in ("up", "down"),
        })

    if not gap_rounds:
        print("  No rounds with both strike and reference price.")
        print()
        return

    gap_df = pd.DataFrame(gap_rounds)
    print(f"  Total rounds with gap data:  {len(gap_df)}")
    print(f"  Mean absolute gap:           ${gap_df['abs_gap'].mean():.2f}")
    print(f"  Median absolute gap:         ${gap_df['abs_gap'].median():.2f}")
    print()

    with_outcomes = gap_df[gap_df["has_outcomes"]]
    if len(with_outcomes) == 0:
        print("  No rounds with outcomes on both sides for gap analysis.")
        print()
        return

    in_gap_count = with_outcomes["in_gap"].sum()
    in_gap_rate = in_gap_count / len(with_outcomes)
    print(f"  Rounds with both outcomes:   {len(with_outcomes)}")
    print(f"  Price landed in gap:         {in_gap_count} ({fmt_pct(in_gap_rate)})")

    if in_gap_count > 0:
        ig_arr = with_outcomes["in_gap"].astype(int).values
        point, lo, hi = bootstrap_ci(ig_arr)
        print(f"  In-gap rate (bootstrap):     {fmt_ci(point, lo, hi)}")
    print()

    # Simulate strangle: buy the side that wins if price is in the gap
    # If gap > 0: buy Kalshi NO + PM UP → both win if price in gap
    # If gap < 0: buy Kalshi YES + PM DOWN → both win if price in gap
    # Need prices from matched data at a specific time point

    print("  Strangle simulation (buying at ~300-600s remaining):")
    strangle_results = []
    for kr, pr, delta in matched:
        k_strike = kr["strike"]
        p_ref = pr["first_rtds"]
        k_out = kr["outcome"]
        p_out = pr["outcome"]

        if k_strike is None or p_ref is None or np.isnan(k_strike) or np.isnan(p_ref):
            continue
        if k_out not in ("yes", "no") or p_out not in ("up", "down"):
            continue

        gap = k_strike - p_ref

        k_data = kr["data"]
        p_data = pr["data"]

        k_snaps = k_data[(k_data["row_type"] == "snapshot") &
                         (k_data["seconds_remaining"] >= 300) &
                         (k_data["seconds_remaining"] <= 600)]
        p_snaps = p_data[(p_data["row_type"] == "snapshot") &
                         (p_data["seconds_remaining"] >= 300) &
                         (p_data["seconds_remaining"] <= 600)]

        if len(k_snaps) == 0 or len(p_snaps) == 0:
            continue

        k_snaps = k_snaps.dropna(subset=["yes_ask", "no_ask"])
        p_snaps = p_snaps.dropna(subset=["up_ask", "down_ask"])

        if len(k_snaps) == 0 or len(p_snaps) == 0:
            continue

        # Take median prices in the window
        if gap > 0:
            # Buy Kalshi NO + PM UP
            cost = k_snaps["no_ask"].median() + p_snaps["up_ask"].median()
            # Win if price in gap (K=NO, PM=UP) or below both (K=NO, PM=DOWN partial)
            # Full win (both pay $1): K=NO and PM=UP → price in gap
            k_win = 1 if k_out == "no" else 0
            p_win = 1 if p_out == "up" else 0
            payout = k_win + p_win  # each contract pays $1 if it wins
        else:
            # Buy Kalshi YES + PM DOWN
            cost = k_snaps["yes_ask"].median() + p_snaps["down_ask"].median()
            k_win = 1 if k_out == "yes" else 0
            p_win = 1 if p_out == "down" else 0
            payout = k_win + p_win

        pnl = payout - cost
        strangle_results.append({
            "end_time": kr["end_time"],
            "gap": gap,
            "cost": cost,
            "payout": payout,
            "pnl": pnl,
            "k_win": k_win,
            "p_win": p_win,
            "both_win": k_win and p_win,
        })

    if strangle_results:
        sr_df = pd.DataFrame(strangle_results)
        print(f"    Rounds simulated:        {len(sr_df)}")
        print(f"    Mean cost:               ${sr_df['cost'].mean():.4f}")
        print(f"    Mean payout:             ${sr_df['payout'].mean():.4f}")
        pnl_arr = sr_df["pnl"].values
        point, lo, hi = bootstrap_ci(pnl_arr)
        print(f"    Mean PnL:                {fmt_ci(point, lo, hi, as_pct=False)}")
        print(f"    Win rate (both win):     {fmt_pct(sr_df['both_win'].mean())}")
        print(f"    K-leg win rate:          {fmt_pct(sr_df['k_win'].mean())}")
        print(f"    P-leg win rate:          {fmt_pct(sr_df['p_win'].mean())}")
        print(f"    At least 1 leg wins:     {fmt_pct((sr_df['payout'] >= 1).mean())}")

        # PnL distribution
        print()
        print(f"    PnL distribution:")
        for bucket_label, lo_v, hi_v in [
            ("< -$0.50", -999, -0.50), ("-$0.50 to -$0.10", -0.50, -0.10),
            ("-$0.10 to $0.00", -0.10, 0.0), ("$0.00 to $0.10", 0.0, 0.10),
            ("$0.10 to $0.50", 0.10, 0.50), ("> $0.50", 0.50, 999),
        ]:
            count = ((sr_df["pnl"] >= lo_v) & (sr_df["pnl"] < hi_v)).sum()
            print(f"      {bucket_label:<20}: {count:>5} ({count/len(sr_df)*100:>5.1f}%)")

        print(f"\n    Total PnL (sum):         ${sr_df['pnl'].sum():.2f}")
    else:
        print("    No rounds with sufficient price data for simulation.")
    print()


def section_6_directional_pricing(matched):
    print("=" * 70)
    print("SECTION 6: DIRECTIONAL PRICING COMPARISON")
    print("=" * 70)

    # Compare: same directional bet, which platform is cheaper?
    price_diffs = []

    for kr, pr, delta in matched:
        k_data = kr["data"]
        p_data = pr["data"]

        k_snaps = k_data[k_data["row_type"] == "snapshot"].copy()
        p_snaps = p_data[p_data["row_type"] == "snapshot"].copy()

        k_snaps = k_snaps.dropna(subset=["seconds_remaining", "yes_ask", "no_ask"])
        p_snaps = p_snaps.dropna(subset=["seconds_remaining", "up_ask", "down_ask"])

        if len(k_snaps) == 0 or len(p_snaps) == 0:
            continue

        # Bucket by 30s intervals
        k_snaps["sr_bucket"] = (k_snaps["seconds_remaining"] // 30).astype(int)
        p_snaps["sr_bucket"] = (p_snaps["seconds_remaining"] // 30).astype(int)

        k_med = k_snaps.groupby("sr_bucket").agg(
            k_yes_ask=("yes_ask", "median"),
            k_no_ask=("no_ask", "median"),
        )
        p_med = p_snaps.groupby("sr_bucket").agg(
            p_up_ask=("up_ask", "median"),
            p_down_ask=("down_ask", "median"),
        )

        merged = k_med.join(p_med, how="inner")
        for bucket, row in merged.iterrows():
            # "Above" bet: Kalshi YES vs PM UP
            above_diff = row["k_yes_ask"] - row["p_up_ask"]
            # "Below" bet: Kalshi NO vs PM DOWN
            below_diff = row["k_no_ask"] - row["p_down_ask"]

            price_diffs.append({
                "end_time": kr["end_time"],
                "sr_bucket": bucket,
                "above_diff": above_diff,  # negative = Kalshi cheaper
                "below_diff": below_diff,
            })

    if not price_diffs:
        print("  No overlapping price data for directional comparison.")
        print()
        return

    diff_df = pd.DataFrame(price_diffs)

    above_arr = diff_df["above_diff"].dropna().values
    below_arr = diff_df["below_diff"].dropna().values

    print(f"  Price comparison points:   {len(diff_df)}")
    print()

    print("  'Above' bet (Kalshi YES ask - PM UP ask):")
    print(f"    Negative = Kalshi cheaper, Positive = PM cheaper")
    point, lo, hi = bootstrap_ci(above_arr)
    print(f"    Mean diff:       {fmt_ci(point, lo, hi, as_pct=False)}")
    print(f"    Median diff:     {np.median(above_arr):.4f}")
    kalshi_cheaper_above = np.sum(above_arr < 0) / len(above_arr)
    print(f"    Kalshi cheaper:  {fmt_pct(kalshi_cheaper_above)}")
    print(f"    PM cheaper:      {fmt_pct(1 - kalshi_cheaper_above)}")
    print()

    print("  'Below' bet (Kalshi NO ask - PM DOWN ask):")
    print(f"    Negative = Kalshi cheaper, Positive = PM cheaper")
    point, lo, hi = bootstrap_ci(below_arr)
    print(f"    Mean diff:       {fmt_ci(point, lo, hi, as_pct=False)}")
    print(f"    Median diff:     {np.median(below_arr):.4f}")
    kalshi_cheaper_below = np.sum(below_arr < 0) / len(below_arr)
    print(f"    Kalshi cheaper:  {fmt_pct(kalshi_cheaper_below)}")
    print(f"    PM cheaper:      {fmt_pct(1 - kalshi_cheaper_below)}")
    print()

    # By time bucket
    print("  Mean price diff by seconds_remaining:")
    print(f"  {'SR Range':<12} {'Above Diff':>12} {'Below Diff':>12} {'K Cheaper(Above)':>18} {'K Cheaper(Below)':>18}")
    for lo_s, hi_s in [(0, 120), (120, 300), (300, 600), (600, 900)]:
        subset = diff_df[(diff_df["sr_bucket"] * 30 >= lo_s) & (diff_df["sr_bucket"] * 30 < hi_s)]
        if len(subset) > 0:
            a_mean = subset["above_diff"].mean()
            b_mean = subset["below_diff"].mean()
            a_k = (subset["above_diff"] < 0).mean()
            b_k = (subset["below_diff"] < 0).mean()
            print(f"  {lo_s:>4}-{hi_s:<4}s   {a_mean:>+12.4f} {b_mean:>+12.4f} {fmt_pct(a_k):>18} {fmt_pct(b_k):>18}")
    print()

    # Largest discrepancies
    diff_df["max_abs_diff"] = diff_df[["above_diff", "below_diff"]].abs().max(axis=1)
    top = diff_df.nlargest(10, "max_abs_diff")
    print("  Top 10 largest pricing discrepancies:")
    print(f"  {'End Time':<22} {'SR':>4} {'Above Diff':>12} {'Below Diff':>12}")
    for _, row in top.iterrows():
        sr_approx = int(row["sr_bucket"] * 30)
        print(f"  {str(row['end_time']):<22} {sr_approx:>4} {row['above_diff']:>+12.4f} {row['below_diff']:>+12.4f}")
    print()


# ── Main ───────────────────────────────────────────────────────────────

def main():
    print()
    print("*" * 70)
    print("  CROSS-PLATFORM VALIDATION: Kalshi vs Polymarket (BTC 15m)")
    print("*" * 70)
    print()

    print("Loading data...")
    kalshi_df = load_kalshi()
    pm_df = load_pm()
    print(f"  Kalshi rows: {len(kalshi_df):,}")
    print(f"  PM rows:     {len(pm_df):,}")
    print()

    print("Building round summaries...")
    k_rounds, p_rounds = build_round_summaries(kalshi_df, pm_df)
    print(f"  Kalshi rounds: {len(k_rounds)}")
    print(f"  PM rounds:     {len(p_rounds)}")
    print()

    print("Matching rounds by end time...")
    matched = match_rounds(k_rounds, p_rounds)
    print(f"  Matched: {len(matched)}")
    print()

    section_1_match_summary(matched, k_rounds, p_rounds)
    section_2_outcome_agreement(matched)
    section_3_strike_comparison(matched)
    section_4_pricing_discrepancies(matched)
    section_5_strangle_gap(matched)
    section_6_directional_pricing(matched)

    print("=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
