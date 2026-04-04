"""Validate BTC Market Open Pump strategy on 15m data.

Claim: Buy UP in 15-min BTC market at 9:00-9:15 AM ET. 70% win rate.
Tests on both Polymarket and Kalshi data.
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "rounds"
PM_DIR = DATA_DIR / "polymarket"


def pm_fee(price: float) -> float:
    """PM crypto fee per contract."""
    return price * 0.25 * (price * (1 - price)) ** 2


def kalshi_fee(price: float) -> float:
    """Kalshi taker fee (rounded up to next cent)."""
    import math
    return math.ceil(0.07 * price * (1 - price) * 100) / 100


def bootstrap_ci(values: np.ndarray, n_iter: int = 1000, ci: float = 0.95):
    """Bootstrap confidence interval for mean."""
    if len(values) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(42)
    means = [np.mean(rng.choice(values, size=len(values), replace=True)) for _ in range(n_iter)]
    lo = np.percentile(means, (1 - ci) / 2 * 100)
    hi = np.percentile(means, (1 + ci) / 2 * 100)
    return lo, hi


def load_pm_15m() -> pd.DataFrame:
    """Load all BTC 15m PM round data."""
    files = sorted(glob.glob(str(PM_DIR / "BTC-15m-*.csv")))
    if not files:
        return pd.DataFrame()
    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f, on_bad_lines="skip")
            dfs.append(df)
        except Exception as e:
            print(f"  WARNING: {os.path.basename(f)}: {e}")
    return pd.concat(dfs, ignore_index=True)


def load_kalshi_15m() -> pd.DataFrame:
    """Load all Kalshi BTC 15m round data."""
    files = sorted(glob.glob(str(DATA_DIR / "KXBTC15M-*.csv")))
    if not files:
        return pd.DataFrame()
    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f, on_bad_lines="skip")
            dfs.append(df)
        except Exception as e:
            print(f"  WARNING: {os.path.basename(f)}: {e}")
    return pd.concat(dfs, ignore_index=True)


def get_pm_round_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """Extract per-round outcome and hour from PM 15m data."""
    df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce", utc=True)
    df["up_ask"] = pd.to_numeric(df["up_ask"], errors="coerce")

    # Get outcomes from round_end rows
    ends = df[df["row_type"] == "round_end"][["slug", "outcome", "end_date"]].drop_duplicates("slug")
    ends = ends[ends["outcome"].isin(["up", "down"])].copy()

    # Round start time = end_date - 15 minutes
    ends["start_utc"] = ends["end_date"] - pd.Timedelta(minutes=15)
    # Convert to ET (UTC-4 for EDT, UTC-5 for EST; Mar 2026 is EDT)
    ends["start_et"] = ends["start_utc"] - pd.Timedelta(hours=4)
    ends["hour_et"] = ends["start_et"].dt.hour
    ends["dow"] = ends["start_et"].dt.dayofweek  # 0=Mon, 6=Sun
    ends["is_weekday"] = ends["dow"] < 5
    ends["is_up"] = (ends["outcome"] == "up").astype(int)

    # Get entry price (up_ask from first snapshot of each round)
    snapshots = df[df["row_type"] == "snapshot"].copy()
    snapshots["seconds_remaining"] = pd.to_numeric(snapshots["seconds_remaining"], errors="coerce")
    first_snap = snapshots.sort_values("seconds_remaining", ascending=False).groupby("slug").first()
    ends = ends.merge(first_snap[["up_ask"]].rename(columns={"up_ask": "entry_up_ask"}),
                      left_on="slug", right_index=True, how="left")

    return ends


def get_kalshi_round_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """Extract per-round outcome and hour from Kalshi 15m data."""
    df["yes_ask"] = pd.to_numeric(df["yes_ask"], errors="coerce")

    # Get outcomes from round_end rows
    ends = df[df["row_type"] == "round_end"][["round_ticker", "outcome", "timestamp"]].drop_duplicates("round_ticker")
    ends = ends[ends["outcome"].isin(["yes", "no"])].copy()

    ends["timestamp"] = pd.to_datetime(ends["timestamp"], errors="coerce", utc=True)
    # Round start = close time - 15 minutes
    ends["start_utc"] = ends["timestamp"] - pd.Timedelta(minutes=15)
    ends["start_et"] = ends["start_utc"] - pd.Timedelta(hours=4)
    ends["hour_et"] = ends["start_et"].dt.hour
    ends["dow"] = ends["start_et"].dt.dayofweek
    ends["is_weekday"] = ends["dow"] < 5
    ends["is_up"] = (ends["outcome"] == "yes").astype(int)

    # Get entry price (yes_ask from first snapshot)
    snapshots = df[df["row_type"] == "snapshot"].copy()
    snapshots["seconds_remaining"] = pd.to_numeric(snapshots["seconds_remaining"], errors="coerce")
    first_snap = snapshots.sort_values("seconds_remaining", ascending=False).groupby("round_ticker").first()
    ends = ends.merge(first_snap[["yes_ask"]].rename(columns={"yes_ask": "entry_ask"}),
                      left_on="round_ticker", right_index=True, how="left")

    return ends


def print_platform_analysis(rounds: pd.DataFrame, platform: str, entry_col: str, fee_fn) -> None:
    """Analyze and print results for one platform."""
    total = len(rounds)
    up_rate = rounds["is_up"].mean()
    baseline = max(up_rate, 1 - up_rate)
    ci_lo, ci_hi = bootstrap_ci(rounds["is_up"].values)

    print(f"\n  {platform}: {total} rounds, up rate = {up_rate:.1%} [{ci_lo:.1%}, {ci_hi:.1%}]")

    # Market open rounds (9:00 AM ET, weekdays)
    mkt_open = rounds[(rounds["hour_et"] == 9) & (rounds["is_weekday"])]
    n_open = len(mkt_open)

    if n_open == 0:
        print(f"  9:00 AM ET (weekdays): No rounds found")
    else:
        open_rate = mkt_open["is_up"].mean()
        open_ci_lo, open_ci_hi = bootstrap_ci(mkt_open["is_up"].values)
        vs_base = open_rate - up_rate
        print(f"  9:00 AM ET (weekdays): {n_open} rounds, up rate = {open_rate:.1%} "
              f"[{open_ci_lo:.1%}, {open_ci_hi:.1%}], vs baseline: {vs_base:+.1%}")

        if n_open < 30:
            print(f"  *** WARNING: {n_open} rounds is far below 96-round minimum. "
                  f"Need ~{max(0, 96 - n_open) * 2} more days of collection. ***")

        # EV calculation for market open
        entry_prices = mkt_open[entry_col].dropna()
        if len(entry_prices) > 0:
            valid = mkt_open[mkt_open[entry_col].notna() & (mkt_open[entry_col] < 0.90)]
            if len(valid) > 0:
                med_entry = valid[entry_col].median()
                gross_ev = valid["is_up"].values * 1.0 - valid[entry_col].values
                fees = np.array([fee_fn(p) for p in valid[entry_col].values])
                net_ev = gross_ev - fees
                print(f"  Executable (ask < $0.90): {len(valid)} trades, "
                      f"med entry ${med_entry:.2f}, gross EV ${np.mean(gross_ev):+.3f}, "
                      f"net EV ${np.mean(net_ev):+.3f}")

    # Hourly breakdown
    print(f"\n  HOURLY UP RATE ({platform}, all days):")
    print(f"  {'Hour ET':<10} {'Rounds':>7} {'Up Rate':>8} {'vs Avg':>8}")
    print(f"  {'-'*35}")

    hourly = rounds.groupby("hour_et").agg(
        n=("is_up", "count"),
        rate=("is_up", "mean"),
    ).reset_index()

    for _, row in hourly.iterrows():
        marker = "  <-- market open" if row["hour_et"] == 9 else ""
        vs_avg = row["rate"] - up_rate
        print(f"  {int(row['hour_et']):02d}:00     {int(row['n']):>7} {row['rate']:>7.1%} {vs_avg:>+7.1%}{marker}")


def main():
    print("=" * 85)
    print("BTC MARKET OPEN PUMP — 15m Markets")
    print("=" * 85)
    print("Claim: Buy UP at 9:00-9:15 AM ET = 70% win rate")
    print()

    # ── Polymarket ──
    print("Loading PM 15m data...")
    pm_df = load_pm_15m()
    if not pm_df.empty:
        pm_rounds = get_pm_round_outcomes(pm_df)
        print(f"  {len(pm_rounds)} complete rounds")
        print_platform_analysis(pm_rounds, "Polymarket", "entry_up_ask", pm_fee)
    else:
        print("  No PM data found")

    print()

    # ── Kalshi ──
    print("Loading Kalshi 15m data...")
    kalshi_df = load_kalshi_15m()
    if not kalshi_df.empty:
        kalshi_rounds = get_kalshi_round_outcomes(kalshi_df)
        print(f"  {len(kalshi_rounds)} complete rounds")
        print_platform_analysis(kalshi_rounds, "Kalshi", "entry_ask", kalshi_fee)
    else:
        print("  No Kalshi data found")

    print()

    # ── Weekend vs Weekday ──
    if not pm_df.empty:
        print("WEEKEND vs WEEKDAY (PM 15m):")
        print("-" * 50)
        weekday = pm_rounds[pm_rounds["is_weekday"]]
        weekend = pm_rounds[~pm_rounds["is_weekday"]]
        if len(weekday) > 0:
            print(f"  Weekday: {len(weekday)} rounds, up rate = {weekday['is_up'].mean():.1%}")
        if len(weekend) > 0:
            print(f"  Weekend: {len(weekend)} rounds, up rate = {weekend['is_up'].mean():.1%}")
        print()


if __name__ == "__main__":
    main()
