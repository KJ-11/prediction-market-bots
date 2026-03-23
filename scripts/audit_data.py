"""Audit prediction market round data for completeness."""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path

import pandas as pd

pd.set_option("display.max_rows", 200)
pd.set_option("display.max_columns", 20)
pd.set_option("display.width", 140)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "rounds"
PM_DIR = DATA_DIR / "polymarket"


def load_kalshi() -> pd.DataFrame:
    files = sorted(glob.glob(str(DATA_DIR / "KX*.csv")))
    if not files:
        print("No Kalshi files found")
        return pd.DataFrame()
    dfs = []
    for f in files:
        fname = os.path.basename(f)
        # Extract coin from filename like KXBTC15M-2026-03-08.csv
        m = re.match(r"KX(\w+?)15M-", fname)
        coin = m.group(1) if m else "UNKNOWN"
        df = pd.read_csv(f, dtype=str)
        df["coin"] = coin
        df["file_date"] = re.search(r"\d{4}-\d{2}-\d{2}", fname).group()
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)


def load_polymarket() -> pd.DataFrame:
    files = sorted(glob.glob(str(PM_DIR / "*.csv")))
    if not files:
        print("No Polymarket files found")
        return pd.DataFrame()
    dfs = []
    for f in files:
        fname = os.path.basename(f)
        # Extract coin and duration from filename like BTC-15m-2026-03-10.csv
        m = re.match(r"(\w+)-(\w+)-(\d{4}-\d{2}-\d{2})\.csv", fname)
        if not m:
            continue
        coin, duration, file_date = m.groups()
        try:
            df = pd.read_csv(f, dtype=str, on_bad_lines="skip")
        except Exception as e:
            print(f"  WARNING: Could not read {fname}: {e}")
            continue
        df["_coin"] = coin
        df["_duration"] = duration
        df["_key"] = f"{coin}-{duration}"
        df["file_date"] = file_date
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)


def analyze_kalshi(df: pd.DataFrame) -> None:
    print("=" * 80)
    print("KALSHI DATA AUDIT")
    print("=" * 80)

    if df.empty:
        print("No data.\n")
        return

    results = []
    for coin, grp in df.groupby("coin"):
        total_rounds = grp["round_ticker"].nunique()
        round_end = grp[grp["row_type"] == "round_end"]
        complete = round_end[round_end["outcome"].notna() & (round_end["outcome"] != "")]["round_ticker"].nunique()
        incomplete = total_rounds - complete
        snapshots = (grp["row_type"] == "snapshot").sum()
        dates = sorted(grp["file_date"].unique())
        date_range = f"{dates[0]} to {dates[-1]}"
        results.append({
            "Coin": coin,
            "Total Rounds": total_rounds,
            "Complete": complete,
            "Incomplete": incomplete,
            "Completion %": f"{complete / total_rounds * 100:.1f}%" if total_rounds else "N/A",
            "Date Range": date_range,
            "Days": len(dates),
            "Snapshots": snapshots,
        })

    summary = pd.DataFrame(results)
    print("\nPer-Coin Summary:")
    print(summary.to_string(index=False))

    total_rounds = df["round_ticker"].nunique()
    round_end = df[df["row_type"] == "round_end"]
    complete = round_end[round_end["outcome"].notna() & (round_end["outcome"] != "")]["round_ticker"].nunique()
    print(f"\nKalshi Totals: {total_rounds} rounds, {complete} complete, "
          f"{total_rounds - complete} incomplete, {(df['row_type'] == 'snapshot').sum()} snapshots")

    # Gap analysis: rounds per day per coin
    print("\nRounds per day (by coin):")
    pivot = df.groupby(["file_date", "coin"])["round_ticker"].nunique().unstack(fill_value=0)
    print(pivot.to_string())

    # Flag low-count days
    print("\nDays with fewer than 20 rounds (potential gaps):")
    daily_total = df.groupby("file_date")["round_ticker"].nunique()
    low_days = daily_total[daily_total < 20]
    if low_days.empty:
        print("  None")
    else:
        for date, count in low_days.items():
            print(f"  {date}: {count} rounds")

    # Check for missing dates in the range
    all_dates = pd.date_range(start=min(df["file_date"]), end=max(df["file_date"]))
    present_dates = set(df["file_date"].unique())
    missing = sorted(set(d.strftime("%Y-%m-%d") for d in all_dates) - present_dates)
    if missing:
        print(f"\nMissing dates in range (no data at all): {', '.join(missing)}")
    else:
        print("\nNo missing dates in range.")
    print()


def analyze_polymarket(df: pd.DataFrame) -> None:
    print("=" * 80)
    print("POLYMARKET DATA AUDIT")
    print("=" * 80)

    if df.empty:
        print("No data.\n")
        return

    results = []
    for key, grp in sorted(df.groupby("_key")):
        total_rounds = grp["slug"].nunique()
        round_end = grp[grp["row_type"] == "round_end"]
        complete = round_end[round_end["outcome"].notna() & (round_end["outcome"] != "")]["slug"].nunique()
        incomplete = total_rounds - complete
        snapshots = (grp["row_type"] == "snapshot").sum()
        dates = sorted(grp["file_date"].unique())
        date_range = f"{dates[0]} to {dates[-1]}"
        results.append({
            "Coin-Duration": key,
            "Total Rounds": total_rounds,
            "Complete": complete,
            "Incomplete": incomplete,
            "Completion %": f"{complete / total_rounds * 100:.1f}%" if total_rounds else "N/A",
            "Date Range": date_range,
            "Days": len(dates),
            "Snapshots": snapshots,
        })

    summary = pd.DataFrame(results)
    print("\nPer Coin-Duration Summary:")
    print(summary.to_string(index=False))

    total_rounds = df["slug"].nunique()
    round_end = df[df["row_type"] == "round_end"]
    complete = round_end[round_end["outcome"].notna() & (round_end["outcome"] != "")]["slug"].nunique()
    print(f"\nPolymarket Totals: {total_rounds} rounds, {complete} complete, "
          f"{total_rounds - complete} incomplete, {(df['row_type'] == 'snapshot').sum()} snapshots")

    # Rounds per day by coin-duration
    print("\nRounds per day (by coin-duration):")
    pivot = df.groupby(["file_date", "_key"])["slug"].nunique().unstack(fill_value=0)
    print(pivot.to_string())

    # Flag low-count days per key
    print("\nDays with unusually few rounds per coin-duration:")
    found_low = False
    for key, grp in sorted(df.groupby("_key")):
        daily = grp.groupby("file_date")["slug"].nunique()
        # For 5m rounds expect ~288/day, 15m ~96, 1h ~24, 4h ~6
        duration = key.split("-")[1]
        thresholds = {"5m": 50, "15m": 15, "1h": 5, "4h": 1}
        threshold = thresholds.get(duration, 5)
        low = daily[daily < threshold]
        if not low.empty:
            found_low = True
            for date, count in low.items():
                print(f"  {key} on {date}: {count} rounds (threshold: {threshold})")
    if not found_low:
        print("  None")

    # Missing dates
    all_dates = pd.date_range(start=min(df["file_date"]), end=max(df["file_date"]))
    present_dates = set(df["file_date"].unique())
    missing = sorted(set(d.strftime("%Y-%m-%d") for d in all_dates) - present_dates)
    if missing:
        print(f"\nMissing dates in range (no PM data at all): {', '.join(missing)}")
    else:
        print("\nNo missing dates in range.")
    print()


def main():
    print("Loading Kalshi data...")
    kalshi_df = load_kalshi()
    print(f"  {len(kalshi_df)} rows from {kalshi_df['round_ticker'].nunique() if not kalshi_df.empty else 0} rounds\n")

    print("Loading Polymarket data...")
    pm_df = load_polymarket()
    print(f"  {len(pm_df)} rows from {pm_df['slug'].nunique() if not pm_df.empty else 0} rounds\n")

    analyze_kalshi(kalshi_df)
    analyze_polymarket(pm_df)


if __name__ == "__main__":
    main()
