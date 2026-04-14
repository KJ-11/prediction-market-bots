"""Snapshot coverage & gap analysis for Kalshi and Polymarket round data."""
from __future__ import annotations

import glob
import os
import re
import sys
from pathlib import Path

import pandas as pd
import numpy as np

BASE = Path(__file__).resolve().parent.parent
KALSHI_DIR = BASE / "data" / "rounds" / "kalshi"
PM_DIR = BASE / "data" / "rounds" / "polymarket"

ZONES = [
    (0, 30, "0-30s"),
    (30, 60, "30-60s"),
    (60, 120, "60-120s"),
    (120, 300, "120-300s"),
    (300, 600, "300-600s"),
    (600, 900, "600-900s"),
]


def load_kalshi(coin: str = "BTC") -> pd.DataFrame:
    pattern = str(KALSHI_DIR / f"KX{coin}15M-*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame()
    dfs = []
    for f in files:
        df = pd.read_csv(f, usecols=["round_ticker", "seconds_remaining", "row_type", "timestamp"])
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)


def load_pm(coin: str = "BTC", duration: str = "15m") -> pd.DataFrame:
    pattern = str(PM_DIR / f"{coin}-{duration}-*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame()
    dfs = []
    for f in files:
        df = pd.read_csv(f, usecols=["slug", "seconds_remaining", "row_type", "timestamp"])
        df.rename(columns={"slug": "round_ticker"}, inplace=True)
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)


def snapshot_only(df: pd.DataFrame) -> pd.DataFrame:
    out = df[df["row_type"] == "snapshot"].copy()
    out["seconds_remaining"] = pd.to_numeric(out["seconds_remaining"], errors="coerce")
    out = out.dropna(subset=["seconds_remaining"])
    return out


def analyze_frequency(snaps: pd.DataFrame, label: str) -> dict:
    """Median seconds between consecutive snapshots within each round."""
    deltas = []
    for _, grp in snaps.groupby("round_ticker"):
        sr = grp["seconds_remaining"].sort_values(ascending=False).values
        if len(sr) < 2:
            continue
        diffs = np.abs(np.diff(sr))
        deltas.extend(diffs.tolist())
    deltas = np.array(deltas)
    if len(deltas) == 0:
        return {}
    return {
        "label": label,
        "median_gap_s": float(np.median(deltas)),
        "mean_gap_s": float(np.mean(deltas)),
        "p95_gap_s": float(np.percentile(deltas, 95)),
        "p99_gap_s": float(np.percentile(deltas, 99)),
        "max_gap_s": float(np.max(deltas)),
        "n_intervals": len(deltas),
    }


def analyze_zones(snaps: pd.DataFrame, label: str) -> pd.DataFrame:
    """Count snapshots per zone per round, then summarise across rounds."""
    rows = []
    for _, grp in snaps.groupby("round_ticker"):
        sr = grp["seconds_remaining"].values
        for lo, hi, zone_name in ZONES:
            cnt = int(np.sum((sr >= lo) & (sr < hi)))
            rows.append({"round": _, "zone": zone_name, "count": cnt})
    if not rows:
        return pd.DataFrame()
    zdf = pd.DataFrame(rows)
    agg = zdf.groupby("zone")["count"].agg(["median", "mean", "min", "max", "std"]).reset_index()
    agg["label"] = label
    # Ensure zone ordering
    zone_order = [z[2] for z in ZONES]
    agg["zone"] = pd.Categorical(agg["zone"], categories=zone_order, ordered=True)
    agg = agg.sort_values("zone")
    return agg


def analyze_gaps(snaps: pd.DataFrame, label: str, threshold: float = 5.0) -> dict:
    """Find rounds with >threshold second gaps in last 120s."""
    last120 = snaps[snaps["seconds_remaining"] < 120].copy()
    rounds_with_gaps = 0
    total_rounds = 0
    worst_gaps = []
    for rnd, grp in last120.groupby("round_ticker"):
        total_rounds += 1
        sr = grp["seconds_remaining"].sort_values(ascending=False).values
        if len(sr) < 2:
            rounds_with_gaps += 1
            worst_gaps.append((rnd, float("inf"), len(sr)))
            continue
        diffs = np.abs(np.diff(sr))
        max_gap = float(np.max(diffs))
        if max_gap > threshold:
            rounds_with_gaps += 1
            worst_gaps.append((rnd, max_gap, len(sr)))

    worst_gaps.sort(key=lambda x: -x[1])
    return {
        "label": label,
        "total_rounds_with_last120_data": total_rounds,
        "rounds_with_gap_gt_5s": rounds_with_gaps,
        "pct_affected": f"{100*rounds_with_gaps/max(total_rounds,1):.1f}%",
        "top10_worst": worst_gaps[:10],
    }


def analyze_duration_coverage(snaps: pd.DataFrame, label: str) -> pd.DataFrame:
    """Earliest and latest seconds_remaining per round."""
    rows = []
    for rnd, grp in snaps.groupby("round_ticker"):
        sr = grp["seconds_remaining"]
        rows.append({
            "round": rnd,
            "earliest_sr": float(sr.max()),  # highest seconds_remaining = earliest capture
            "latest_sr": float(sr.min()),    # lowest seconds_remaining = latest capture
            "n_snapshots": len(grp),
        })
    if not rows:
        return pd.DataFrame()
    cdf = pd.DataFrame(rows)
    return cdf


def print_section(title: str):
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")


def main():
    # ── Datasets to analyze ──────────────────────────────────────────────────
    datasets = {}

    # Kalshi coins
    for coin in ["BTC", "ETH", "SOL", "XRP"]:
        df = load_kalshi(coin)
        if len(df):
            datasets[f"Kalshi-{coin}"] = df

    # Polymarket coin/duration combos
    for coin in ["BTC", "ETH", "SOL", "XRP"]:
        for dur in ["5m", "15m", "1h", "4h"]:
            df = load_pm(coin, dur)
            if len(df):
                datasets[f"PM-{coin}-{dur}"] = df

    print(f"Loaded {len(datasets)} series")
    for k, v in datasets.items():
        snaps = snapshot_only(v)
        n_rounds = snaps["round_ticker"].nunique()
        print(f"  {k:20s}: {len(v):>8,} rows, {len(snaps):>8,} snapshots, {n_rounds:>5} rounds")

    # ═══════════════════════════════════════════════════════════════════════
    # 1. SNAPSHOT FREQUENCY
    # ═══════════════════════════════════════════════════════════════════════
    print_section("1. SNAPSHOT FREQUENCY (seconds between consecutive snapshots)")

    freq_rows = []
    for label, df in datasets.items():
        snaps = snapshot_only(df)
        result = analyze_frequency(snaps, label)
        if result:
            freq_rows.append(result)

    freq_df = pd.DataFrame(freq_rows)
    if len(freq_df):
        print(freq_df.to_string(index=False, float_format="{:.2f}".format))

    # ═══════════════════════════════════════════════════════════════════════
    # 2. COVERAGE BY TIME-TO-EXPIRY ZONES
    # ═══════════════════════════════════════════════════════════════════════
    print_section("2. COVERAGE BY TIME-TO-EXPIRY ZONES (snapshots per zone per round)")

    # Primary series first
    for primary in ["Kalshi-BTC", "PM-BTC-15m"]:
        if primary in datasets:
            snaps = snapshot_only(datasets[primary])
            zdf = analyze_zones(snaps, primary)
            if len(zdf):
                print(f"\n  --- {primary} (detailed) ---")
                print(zdf[["zone", "median", "mean", "min", "max", "std"]].to_string(
                    index=False, float_format="{:.1f}".format))

    # Summary for all series
    print(f"\n  --- ALL SERIES: median snapshots per zone ---")
    all_zone_rows = []
    for label, df in datasets.items():
        snaps = snapshot_only(df)
        zdf = analyze_zones(snaps, label)
        if len(zdf):
            for _, row in zdf.iterrows():
                all_zone_rows.append({
                    "series": label,
                    "zone": row["zone"],
                    "median": row["median"],
                    "min": row["min"],
                })
    if all_zone_rows:
        azdf = pd.DataFrame(all_zone_rows)
        pivot_med = azdf.pivot_table(index="series", columns="zone", values="median")
        pivot_min = azdf.pivot_table(index="series", columns="zone", values="min")
        zone_order = [z[2] for z in ZONES]
        pivot_med = pivot_med.reindex(columns=zone_order)
        pivot_min = pivot_min.reindex(columns=zone_order)
        print("\n  Median snapshots per zone:")
        print(pivot_med.to_string(float_format="{:.0f}".format))
        print("\n  Minimum snapshots per zone (worst round):")
        print(pivot_min.to_string(float_format="{:.0f}".format))

    # ═══════════════════════════════════════════════════════════════════════
    # 3. GAP DETECTION (>5s gaps in last 120s)
    # ═══════════════════════════════════════════════════════════════════════
    print_section("3. GAP DETECTION (>5s gaps in last 120 seconds)")

    for primary in ["Kalshi-BTC", "PM-BTC-15m"]:
        if primary in datasets:
            snaps = snapshot_only(datasets[primary])
            result = analyze_gaps(snaps, primary)
            print(f"\n  --- {primary} ---")
            print(f"  Rounds with last-120s data: {result['total_rounds_with_last120_data']}")
            print(f"  Rounds with gap >5s:        {result['rounds_with_gap_gt_5s']} ({result['pct_affected']})")
            if result["top10_worst"]:
                print(f"  Top 10 worst gaps:")
                for rnd, gap, n in result["top10_worst"]:
                    gap_str = f"{gap:.1f}s" if gap != float("inf") else "NO DATA (<=1 snap)"
                    print(f"    {rnd:60s}  gap={gap_str:>10s}  snaps_in_last120={n}")

    # Summary table for all series
    print(f"\n  --- ALL SERIES summary ---")
    gap_summary = []
    for label, df in datasets.items():
        snaps = snapshot_only(df)
        result = analyze_gaps(snaps, label)
        gap_summary.append({
            "series": label,
            "rounds": result["total_rounds_with_last120_data"],
            "with_gap_gt5s": result["rounds_with_gap_gt_5s"],
            "pct": result["pct_affected"],
        })
    gdf = pd.DataFrame(gap_summary)
    print(gdf.to_string(index=False))

    # ═══════════════════════════════════════════════════════════════════════
    # 4. ROUND DURATION COVERAGE
    # ═══════════════════════════════════════════════════════════════════════
    print_section("4. ROUND DURATION COVERAGE (earliest/latest seconds_remaining)")

    for primary in ["Kalshi-BTC", "PM-BTC-15m"]:
        if primary in datasets:
            snaps = snapshot_only(datasets[primary])
            cdf = analyze_duration_coverage(snaps, primary)
            if len(cdf):
                print(f"\n  --- {primary} ---")
                print(f"  Rounds analysed: {len(cdf)}")
                print(f"  Earliest capture (seconds_remaining):")
                print(f"    median: {cdf['earliest_sr'].median():.0f}s  min: {cdf['earliest_sr'].min():.0f}s  max: {cdf['earliest_sr'].max():.0f}s")
                print(f"  Latest capture (seconds_remaining):")
                print(f"    median: {cdf['latest_sr'].median():.1f}s  min: {cdf['latest_sr'].min():.1f}s  max: {cdf['latest_sr'].max():.1f}s")
                print(f"  Snapshots per round:")
                print(f"    median: {cdf['n_snapshots'].median():.0f}  min: {cdf['n_snapshots'].min()}  max: {cdf['n_snapshots'].max()}")

                # Worst coverage rounds
                worst = cdf.nsmallest(5, "n_snapshots")
                print(f"  5 rounds with fewest snapshots:")
                for _, r in worst.iterrows():
                    print(f"    {r['round']:60s}  snaps={r['n_snapshots']:>4}  range=[{r['latest_sr']:.0f}s - {r['earliest_sr']:.0f}s]")

    # All series summary
    print(f"\n  --- ALL SERIES summary ---")
    dur_rows = []
    for label, df in datasets.items():
        snaps = snapshot_only(df)
        cdf = analyze_duration_coverage(snaps, label)
        if len(cdf):
            dur_rows.append({
                "series": label,
                "rounds": len(cdf),
                "med_earliest_sr": f"{cdf['earliest_sr'].median():.0f}",
                "med_latest_sr": f"{cdf['latest_sr'].median():.1f}",
                "med_snaps": f"{cdf['n_snapshots'].median():.0f}",
                "min_snaps": cdf["n_snapshots"].min(),
            })
    ddf = pd.DataFrame(dur_rows)
    print(ddf.to_string(index=False))

    print("\n" + "="*80)
    print("  DONE")
    print("="*80)


if __name__ == "__main__":
    main()
