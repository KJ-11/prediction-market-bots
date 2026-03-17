"""
V3 Data Analysis — Comprehensive search for exploitable edge in crypto binary options.

Covers:
  Phase 1: Data understanding (rounds, base rates, spreads, liquidity)
  Phase 2: Pattern discovery (distance, momentum, book imbalance, volume, cross-coin,
           cross-platform, time-of-day, volatility, sequential, spread dynamics)
  Phase 3: Exploitable edge quantification (EV, frequency, realistic prices, stability)
  Phase 4: Strategy recommendations

Usage:
  python scripts/v3_analysis.py
"""
from __future__ import annotations

import os
import sys
import warnings
from decimal import Decimal
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

PROJECT = Path(__file__).resolve().parent.parent
KALSHI_DIR = PROJECT / "data" / "rounds"
PM_DIR = PROJECT / "data" / "rounds" / "polymarket"
TRADES_DIR = PROJECT / "data" / "trades"
OUT_FILE = PROJECT / "research" / "v3-data-analysis.md"

FEE_COEFF = 0.07


def kalshi_fee(price: float) -> float:
    """Kalshi fee per contract."""
    return np.ceil(FEE_COEFF * price * (1 - price) * 100) / 100


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def load_kalshi_rounds() -> pd.DataFrame:
    """Load all Kalshi round snapshot CSVs into one DataFrame."""
    frames = []
    for f in sorted(KALSHI_DIR.glob("KX*15M-*.csv")):
        coin = f.name.split("-")[0].replace("KX", "").replace("15M", "")
        df = pd.read_csv(f, engine="python", on_bad_lines="skip")
        df["coin"] = coin
        df["file_date"] = f.name.split("-", 1)[1].replace(".csv", "")
        frames.append(df)
    if not frames:
        print("ERROR: No Kalshi data found")
        sys.exit(1)
    all_df = pd.concat(frames, ignore_index=True)
    all_df["timestamp"] = pd.to_datetime(all_df["timestamp"], format="ISO8601", utc=True)
    for col in ["strike", "spot_price", "yes_bid", "yes_ask", "no_bid", "no_ask",
                 "volume", "seconds_remaining", "seconds_elapsed",
                 "spot_minus_strike", "spot_move_pct", "kraken_spot"]:
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce")
    return all_df


def load_pm_rounds() -> pd.DataFrame:
    """Load all Polymarket round snapshot CSVs."""
    frames = []
    for f in sorted(PM_DIR.glob("*.csv")):
        parts = f.stem.split("-")
        coin = parts[0]
        duration = parts[1]
        df = pd.read_csv(f, engine="python", on_bad_lines="skip")
        df["coin"] = coin
        df["file_duration"] = duration
        frames.append(df)
    if not frames:
        print("WARNING: No Polymarket data found")
        return pd.DataFrame()
    all_df = pd.concat(frames, ignore_index=True)
    all_df["timestamp"] = pd.to_datetime(all_df["timestamp"], format="ISO8601", utc=True)
    for col in ["up_bid", "up_ask", "down_bid", "down_ask", "up_midpoint",
                 "spread", "last_trade_price", "spot_price", "kraken_price",
                 "rtds_price", "volume", "seconds_remaining"]:
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce")
    return all_df


# ---------------------------------------------------------------------------
# Phase 1: Data Understanding
# ---------------------------------------------------------------------------

def phase1_understand(kalshi: pd.DataFrame, pm: pd.DataFrame) -> str:
    out = []
    out.append("# Phase 1: Data Understanding\n")

    # --- Round counts ---
    ends = kalshi[kalshi["row_type"] == "round_end"].copy()
    out.append("## 1.1 Round Counts (Kalshi)\n")
    counts = ends.groupby(["coin", "file_date"]).size().unstack(fill_value=0)
    out.append(f"Total rounds with outcomes: {len(ends)}\n")
    out.append("Rounds per coin per day:\n")
    out.append(counts.to_markdown() + "\n")
    out.append(f"**Total rounds per coin:** {ends.groupby('coin').size().to_dict()}\n")

    # --- Base rates ---
    out.append("\n## 1.2 Base Rates (Kalshi)\n")
    for coin in sorted(ends["coin"].unique()):
        c = ends[ends["coin"] == coin]
        yes_pct = (c["outcome"] == "yes").mean() * 100
        out.append(f"- **{coin}**: {len(c)} rounds, {yes_pct:.1f}% yes, {100-yes_pct:.1f}% no")
    out.append("")
    overall_yes = (ends["outcome"] == "yes").mean() * 100
    out.append(f"- **Overall**: {overall_yes:.1f}% yes (should be ~50% for fair coin)\n")

    # --- Snapshot density ---
    out.append("## 1.3 Snapshot Density\n")
    snaps = kalshi[kalshi["row_type"] == "snapshot"]
    sample_rounds = snaps.groupby("round_ticker").size()
    out.append(f"Snapshots per round: mean={sample_rounds.mean():.0f}, "
               f"median={sample_rounds.median():.0f}, "
               f"min={sample_rounds.min()}, max={sample_rounds.max()}\n")

    # --- Spreads ---
    out.append("## 1.4 Spreads (Kalshi)\n")
    snaps = snaps.copy()
    snaps["yes_spread"] = snaps["yes_ask"] - snaps["yes_bid"]
    snaps["yes_spread"] = snaps["yes_spread"].clip(lower=0)
    # Filter to reasonable mid-range where both sides are quoted
    mid = snaps[(snaps["yes_bid"] > 0.05) & (snaps["yes_ask"] < 0.95) &
                (snaps["yes_spread"] > 0) & (snaps["yes_spread"] < 0.5)]
    if len(mid) > 0:
        for coin in sorted(mid["coin"].unique()):
            c = mid[mid["coin"] == coin]
            out.append(f"- **{coin}**: median spread ${c['yes_spread'].median():.3f}, "
                       f"mean ${c['yes_spread'].mean():.3f} "
                       f"(n={len(c):,} snapshots with quoted book)")
    else:
        out.append("- No mid-range spread data available")
    out.append("")

    # --- Spread by time bucket ---
    out.append("## 1.5 Spread by Time into Round (Kalshi)\n")
    if len(mid) > 0:
        mid = mid.copy()
        mid["time_bucket"] = pd.cut(mid["seconds_elapsed"], bins=[0, 60, 180, 300, 450, 600, 750, 900],
                                     labels=["0-60", "60-180", "180-300", "300-450", "450-600", "600-750", "750-900"])
        spread_by_time = mid.groupby(["coin", "time_bucket"])["yes_spread"].agg(["median", "mean", "count"])
        out.append(spread_by_time.to_markdown() + "\n")

    # --- Volume ---
    out.append("## 1.6 Volume per Round (Kalshi)\n")
    # Volume in the data is cumulative — take the max per round
    round_vol = snaps.groupby(["coin", "round_ticker"])["volume"].max()
    vol_by_coin = round_vol.groupby(level=0).agg(["mean", "median", "min", "max"])
    out.append(vol_by_coin.to_markdown() + "\n")

    # --- PM data overview ---
    if len(pm) > 0:
        out.append("## 1.7 Polymarket Data Overview\n")
        pm_ends = pm[pm["row_type"].str.contains("end|resolved", case=False, na=False)]
        out.append(f"Total PM snapshots: {len(pm):,}")
        out.append(f"Total PM round_end/resolved rows: {len(pm_ends)}")

        if len(pm_ends) > 0:
            pm_counts = pm_ends.groupby(["coin", "file_duration"]).size().unstack(fill_value=0)
            out.append(f"\nPM rounds with outcomes:\n{pm_counts.to_markdown()}\n")
            for dur in sorted(pm_ends["file_duration"].unique()):
                d = pm_ends[pm_ends["file_duration"] == dur]
                up_pct = (d["outcome"] == "up").mean() * 100
                out.append(f"- **{dur}**: {len(d)} rounds, {up_pct:.1f}% up")
        else:
            out.append("No resolved PM rounds found in data — checking row_type values...")
            out.append(f"Unique row_type values: {pm['row_type'].unique().tolist()}")

        # PM spreads
        out.append("\n### PM Spreads\n")
        pm_snaps = pm[pm["row_type"] == "snapshot"].copy()
        pm_snaps["up_spread"] = pm_snaps["up_ask"] - pm_snaps["up_bid"]
        # Check if PM books are actually quoted
        pm_quoted = pm_snaps[(pm_snaps["up_bid"] > 0.05) & (pm_snaps["up_ask"] < 0.95)]
        out.append(f"PM snapshots with quoted book (up_bid>0.05, up_ask<0.95): "
                   f"{len(pm_quoted):,} / {len(pm_snaps):,} ({100*len(pm_quoted)/max(1,len(pm_snaps)):.1f}%)\n")
        if len(pm_quoted) > 0:
            for dur in sorted(pm_quoted["file_duration"].unique()):
                d = pm_quoted[pm_quoted["file_duration"] == dur]
                out.append(f"- **{dur}**: median spread ${d['up_spread'].median():.3f}, n={len(d):,}")
        else:
            out.append("PM books are mostly unquoted (wide 0.01/0.99 spreads) — "
                       "limited tradeable liquidity.")

    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Phase 2: Pattern Discovery (Kalshi)
# ---------------------------------------------------------------------------

def phase2_patterns(kalshi: pd.DataFrame, pm: pd.DataFrame) -> str:
    out = []
    out.append("\n# Phase 2: Pattern Discovery\n")

    # Prepare: merge outcome onto snapshots
    ends = kalshi[kalshi["row_type"] == "round_end"][["round_ticker", "outcome"]].copy()
    ends = ends.drop_duplicates("round_ticker")
    ends = ends.rename(columns={"outcome": "round_outcome"})
    snaps = kalshi[kalshi["row_type"] == "snapshot"].copy()
    snaps = snaps.merge(ends, on="round_ticker", how="inner")

    # Derived columns
    snaps["abs_dist"] = (snaps["spot_price"] - snaps["strike"]).abs()
    snaps["pct_dist"] = snaps["abs_dist"] / snaps["strike"]
    snaps["spot_above"] = snaps["spot_price"] > snaps["strike"]
    snaps["outcome"] = snaps["round_outcome"]
    snaps["correct_side"] = ((snaps["spot_above"] & (snaps["outcome"] == "yes")) |
                              (~snaps["spot_above"] & (snaps["outcome"] == "no")))

    # --- 2.1 Spot Distance → Outcome by time ---
    out.append("## 2.1 Spot Distance → Outcome Accuracy by Time Window\n")
    out.append("Does being further from strike predict the outcome? Broken by time and distance.\n")

    time_bins = [(0, 60), (60, 180), (180, 300), (250, 500), (300, 540),
                 (450, 600), (600, 750), (750, 900)]
    dist_bins = [(0, 0.0005), (0.0005, 0.001), (0.001, 0.0015), (0.0015, 0.002),
                 (0.002, 0.003), (0.003, 0.005), (0.005, 0.01), (0.01, 1.0)]

    results = []
    for t_start, t_end in time_bins:
        t_snaps = snaps[(snaps["seconds_elapsed"] >= t_start) & (snaps["seconds_elapsed"] <= t_end)]
        # Take one snapshot per round (first in window)
        first_per_round = t_snaps.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()
        for d_lo, d_hi in dist_bins:
            sel = first_per_round[(first_per_round["pct_dist"] >= d_lo) & (first_per_round["pct_dist"] < d_hi)]
            if len(sel) >= 5:
                acc = sel["correct_side"].mean()
                results.append({
                    "time_window": f"T+{t_start}-{t_end}",
                    "dist_range": f"{d_lo*100:.2f}-{d_hi*100:.2f}%",
                    "n": len(sel),
                    "accuracy": f"{acc*100:.1f}%",
                    "acc_raw": acc,
                })
    res_df = pd.DataFrame(results)
    if len(res_df) > 0:
        pivot = res_df.pivot_table(index="dist_range", columns="time_window",
                                    values="accuracy", aggfunc="first")
        out.append(pivot.to_markdown() + "\n")
        # Also show sample sizes
        pivot_n = res_df.pivot_table(index="dist_range", columns="time_window",
                                      values="n", aggfunc="first")
        out.append("Sample sizes:\n" + pivot_n.to_markdown() + "\n")

    # --- 2.2 Per-coin accuracy ---
    out.append("## 2.2 Per-Coin Accuracy (v2 window: T+250-500)\n")
    for d_lo, d_hi in [(0.0015, 0.003), (0.003, 0.005), (0.005, 0.01), (0.002, 0.005)]:
        out.append(f"\n### Distance {d_lo*100:.2f}-{d_hi*100:.2f}%\n")
        t_snaps = snaps[(snaps["seconds_elapsed"] >= 250) & (snaps["seconds_elapsed"] <= 500)]
        first = t_snaps.sort_values("seconds_elapsed").groupby(["coin", "round_ticker"]).first().reset_index()
        sel = first[(first["pct_dist"] >= d_lo) & (first["pct_dist"] < d_hi)]
        for coin in sorted(sel["coin"].unique()):
            c = sel[sel["coin"] == coin]
            if len(c) >= 5:
                acc = c["correct_side"].mean()
                out.append(f"- **{coin}**: {acc*100:.1f}% accuracy, n={len(c)}")

    # --- 2.3 What price would we actually pay? ---
    out.append("\n## 2.3 Realistic Entry Prices (ask price at signal time)\n")
    out.append("When distance > threshold, what does the ask price look like?\n")

    for t_start, t_end in [(250, 500), (300, 540), (450, 600), (600, 800)]:
        out.append(f"\n### Window T+{t_start}-{t_end}\n")
        t_snaps = snaps[(snaps["seconds_elapsed"] >= t_start) & (snaps["seconds_elapsed"] <= t_end)]
        first = t_snaps.sort_values("seconds_elapsed").groupby(["coin", "round_ticker"]).first().reset_index()

        for d_thresh in [0.0015, 0.002, 0.003]:
            sel = first[first["pct_dist"] >= d_thresh].copy()
            if len(sel) < 10:
                continue
            # The price you'd pay depends on which side
            sel = sel.copy()
            sel["entry_price"] = np.where(sel["spot_above"],
                                           sel["yes_ask"],
                                           sel["no_ask"].fillna(1 - sel["yes_bid"]))
            sel = sel[sel["entry_price"] > 0.01]  # filter unquoted
            acc = sel["correct_side"].mean()
            avg_price = sel["entry_price"].mean()
            med_price = sel["entry_price"].median()
            fee = kalshi_fee(med_price)
            be_wr = (med_price + fee) / 1.0  # break-even win rate
            ev = acc * (1 - med_price - fee) - (1 - acc) * (med_price + fee)
            out.append(f"- dist>{d_thresh*100:.2f}%: n={len(sel)}, acc={acc*100:.1f}%, "
                       f"med_price=${med_price:.3f}, avg_price=${avg_price:.3f}, "
                       f"fee=${fee:.4f}, BE_WR={be_wr*100:.1f}%, **EV=${ev:.4f}**")

    # --- 2.4 Momentum (rate of price change) → outcome ---
    out.append("\n## 2.4 Momentum Signal\n")
    out.append("Does the rate of spot price change predict the outcome?\n")

    # For each round, compute momentum over a lookback window
    # Use snapshots around T+250 and compare to T+200
    momentum_results = []
    for coin in sorted(snaps["coin"].unique()):
        c_snaps = snaps[snaps["coin"] == coin].copy()
        for rt in c_snaps["round_ticker"].unique():
            r = c_snaps[c_snaps["round_ticker"] == rt].sort_values("seconds_elapsed")
            # Get snapshot near T+250 and T+150
            late = r[(r["seconds_elapsed"] >= 230) & (r["seconds_elapsed"] <= 270)]
            early = r[(r["seconds_elapsed"] >= 130) & (r["seconds_elapsed"] <= 170)]
            if len(late) == 0 or len(early) == 0:
                continue
            late_row = late.iloc[0]
            early_row = early.iloc[0]
            momentum = (late_row["spot_price"] - early_row["spot_price"]) / early_row["spot_price"]
            outcome = late_row["outcome"]
            momentum_results.append({
                "coin": coin,
                "round_ticker": rt,
                "momentum": momentum,
                "outcome": outcome,
                "abs_momentum": abs(momentum),
            })

    if momentum_results:
        mom_df = pd.DataFrame(momentum_results)
        # Momentum direction predicts outcome?
        mom_df["mom_predicts_yes"] = mom_df["momentum"] > 0
        mom_df["actual_yes"] = mom_df["outcome"] == "yes"
        mom_df["correct"] = mom_df["mom_predicts_yes"] == mom_df["actual_yes"]
        overall_acc = mom_df["correct"].mean()
        out.append(f"Overall momentum-direction accuracy: {overall_acc*100:.1f}% (n={len(mom_df)})\n")

        # By momentum magnitude
        for lo, hi in [(0, 0.0005), (0.0005, 0.001), (0.001, 0.002), (0.002, 0.005), (0.005, 1.0)]:
            sel = mom_df[(mom_df["abs_momentum"] >= lo) & (mom_df["abs_momentum"] < hi)]
            if len(sel) >= 10:
                acc = sel["correct"].mean()
                out.append(f"- |momentum| {lo*100:.2f}-{hi*100:.2f}%: {acc*100:.1f}% accuracy, n={len(sel)}")
    out.append("")

    # --- 2.5 Book Imbalance → Outcome ---
    out.append("## 2.5 Book Imbalance Signal\n")
    out.append("Does the bid-ask midpoint deviating from 0.50 predict the outcome?\n")

    t_snaps = snaps[(snaps["seconds_elapsed"] >= 250) & (snaps["seconds_elapsed"] <= 500)].copy()
    first = t_snaps.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()
    first["yes_mid"] = (first["yes_bid"] + first["yes_ask"]) / 2
    valid = first[(first["yes_bid"] > 0) & (first["yes_ask"] > 0) & (first["yes_ask"] < 1)]
    if len(valid) > 0:
        valid = valid.copy()
        valid["book_says_yes"] = valid["yes_mid"] > 0.5
        valid["actual_yes"] = valid["outcome"] == "yes"
        valid["book_correct"] = valid["book_says_yes"] == valid["actual_yes"]
        out.append(f"Book midpoint predicts outcome: {valid['book_correct'].mean()*100:.1f}% "
                   f"(n={len(valid)})\n")
        # By confidence level
        valid["book_confidence"] = (valid["yes_mid"] - 0.5).abs()
        for lo, hi in [(0, 0.05), (0.05, 0.15), (0.15, 0.25), (0.25, 0.40), (0.40, 0.50)]:
            sel = valid[(valid["book_confidence"] >= lo) & (valid["book_confidence"] < hi)]
            if len(sel) >= 10:
                acc = sel["book_correct"].mean()
                out.append(f"- Book confidence {lo:.2f}-{hi:.2f}: {acc*100:.1f}% accuracy, n={len(sel)}")
    out.append("")

    # --- 2.6 Spot vs Book Disagreement ---
    out.append("## 2.6 Spot-Book Disagreement\n")
    out.append("When spot says one thing and the book says another, who's right?\n")

    if len(valid) > 0:
        valid["spot_says_yes"] = valid["spot_price"] > valid["strike"]
        valid["spot_says_no"] = ~valid["spot_says_yes"]
        agree = valid[valid["spot_says_yes"] == valid["book_says_yes"]]
        disagree = valid[valid["spot_says_yes"] != valid["book_says_yes"]]
        if len(agree) > 10:
            agree_acc_spot = ((agree["spot_says_yes"] & (agree["outcome"] == "yes")) |
                              (agree["spot_says_no"] & (agree["outcome"] == "no"))).mean()
            out.append(f"When spot and book agree: {agree_acc_spot*100:.1f}% accuracy (n={len(agree)})")
        if len(disagree) > 10:
            dis_spot_acc = ((disagree["spot_says_yes"] & (disagree["outcome"] == "yes")) |
                            (disagree["spot_says_no"] & (disagree["outcome"] == "no"))).mean()
            dis_book_acc = disagree["book_correct"].mean()
            out.append(f"When they disagree: spot is right {dis_spot_acc*100:.1f}%, "
                       f"book is right {dis_book_acc*100:.1f}% (n={len(disagree)})")
    out.append("")

    # --- 2.7 Volume → Outcome ---
    out.append("## 2.7 Volume Patterns\n")
    t_snaps2 = snaps[(snaps["seconds_elapsed"] >= 250) & (snaps["seconds_elapsed"] <= 500)]
    first = t_snaps2.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()
    if "volume" in first.columns:
        first_v = first[first["volume"] > 0].copy()
        first_v["vol_bucket"] = pd.qcut(first_v["volume"], 4, labels=["Q1_low", "Q2", "Q3", "Q4_high"],
                                          duplicates="drop")
        for bucket in first_v["vol_bucket"].unique():
            sel = first_v[first_v["vol_bucket"] == bucket]
            # Check if correct_side accuracy varies
            acc = sel["correct_side"].mean()
            out.append(f"- Volume {bucket}: accuracy {acc*100:.1f}%, n={len(sel)}")
    out.append("")

    # --- 2.8 Time of Day Effects ---
    out.append("## 2.8 Time of Day Effects\n")
    ends_data = kalshi[kalshi["row_type"] == "round_end"].copy()
    ends_data["hour"] = ends_data["timestamp"].dt.hour
    # Check if certain hours have different yes rates or different accuracy patterns
    hour_stats = ends_data.groupby(["coin", "hour"]).apply(
        lambda g: pd.Series({"n": len(g), "yes_pct": (g["outcome"] == "yes").mean()})
    ).reset_index()

    # Instead of per-coin per-hour, aggregate
    hour_agg = ends_data.groupby("hour").apply(
        lambda g: pd.Series({"n": len(g), "yes_pct": (g["outcome"] == "yes").mean()})
    ).reset_index()
    out.append("Yes rate by hour (UTC):\n")
    for _, row in hour_agg.iterrows():
        bar = "█" * int(row["yes_pct"] * 20)
        out.append(f"  {int(row['hour']):02d}:00  {row['yes_pct']*100:5.1f}%  n={int(row['n']):3d}  {bar}")
    out.append("")

    # Now check if our strategy (spot distance) has different accuracy at different hours
    out.append("### Signal accuracy by hour (T+250-500, dist>0.15%)\n")
    t_snaps3 = snaps[(snaps["seconds_elapsed"] >= 250) & (snaps["seconds_elapsed"] <= 500)].copy()
    first = t_snaps3.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()
    sig = first[first["pct_dist"] >= 0.0015].copy()
    sig["hour"] = sig["timestamp"].dt.hour
    hour_acc = sig.groupby("hour").apply(
        lambda g: pd.Series({"n": len(g), "accuracy": g["correct_side"].mean()})
    ).reset_index()
    for _, row in hour_acc.iterrows():
        out.append(f"  {int(row['hour']):02d}:00  {row['accuracy']*100:5.1f}%  n={int(row['n']):3d}")
    out.append("")

    # --- 2.9 Sequential Round Patterns ---
    out.append("## 2.9 Sequential Round Patterns\n")
    out.append("Does the previous round outcome predict the next?\n")
    for coin in sorted(ends_data["coin"].unique()):
        c = ends_data[ends_data["coin"] == coin].sort_values("timestamp").reset_index(drop=True)
        if len(c) < 20:
            continue
        c["prev_outcome"] = c["outcome"].shift(1)
        c = c.dropna(subset=["prev_outcome"])
        c["same_as_prev"] = c["outcome"] == c["prev_outcome"]
        streak_rate = c["same_as_prev"].mean()
        out.append(f"- **{coin}**: same as previous {streak_rate*100:.1f}% (n={len(c)}), "
                   f"expect ~50% if random")
    out.append("")

    # --- 2.10 Cross-coin correlation ---
    out.append("## 2.10 Cross-Coin Correlation\n")
    out.append("Do outcomes of different coins in the same time window correlate?\n")

    # Align rounds by approximate time (within 15 min of each other)
    ends_pivot = ends_data.copy()
    ends_pivot["round_time"] = ends_pivot["timestamp"].dt.floor("15min")
    pivot = ends_pivot.pivot_table(index="round_time", columns="coin", values="outcome", aggfunc="first")
    if len(pivot) > 20:
        # Convert to numeric
        for col in pivot.columns:
            pivot[col] = (pivot[col] == "yes").astype(float)
        corr = pivot.corr()
        out.append(f"Outcome correlation matrix (n={len(pivot)} time windows):\n")
        out.append(corr.to_markdown() + "\n")

    # --- 2.11 Volatility regime ---
    out.append("## 2.11 Volatility Regime Effects\n")
    out.append("Does intra-round volatility affect strategy accuracy?\n")

    # Compute per-round volatility as std of spot_move_pct
    round_vol = snaps.groupby("round_ticker").agg(
        vol=("spot_move_pct", "std"),
        outcome=("outcome", "first"),
        coin=("coin", "first"),
    ).dropna()

    # Check accuracy of spot-distance signal in high vs low vol regimes
    # Merge with first-in-window snapshots
    t_snaps4 = snaps[(snaps["seconds_elapsed"] >= 250) & (snaps["seconds_elapsed"] <= 500)]
    first = t_snaps4.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()
    sig = first[first["pct_dist"] >= 0.0015].copy()
    sig = sig.merge(round_vol[["vol"]], left_on="round_ticker", right_index=True, how="inner")
    if len(sig) > 20:
        sig["vol_bucket"] = pd.qcut(sig["vol"], 3, labels=["low_vol", "mid_vol", "high_vol"],
                                      duplicates="drop")
        for bucket in ["low_vol", "mid_vol", "high_vol"]:
            sel = sig[sig["vol_bucket"] == bucket]
            if len(sel) >= 5:
                acc = sel["correct_side"].mean()
                avg_dist = sel["pct_dist"].mean()
                out.append(f"- **{bucket}**: accuracy {acc*100:.1f}%, avg_dist={avg_dist*100:.3f}%, n={len(sel)}")
    out.append("")

    # --- 2.12 Kraken vs Coinbase divergence ---
    out.append("## 2.12 Kraken-Coinbase Divergence\n")
    out.append("Does a divergence between Coinbase and Kraken spot predict anything?\n")

    kc_snaps = snaps[(snaps["kraken_spot"] > 0) & (snaps["seconds_elapsed"] >= 250) &
                      (snaps["seconds_elapsed"] <= 500)].copy()
    kc_snaps["kc_diff_pct"] = (kc_snaps["spot_price"] - kc_snaps["kraken_spot"]).abs() / kc_snaps["spot_price"]
    first = kc_snaps.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()
    if len(first) > 20:
        med_diff = first["kc_diff_pct"].median()
        out.append(f"Median Coinbase-Kraken divergence: {med_diff*100:.4f}%\n")
        # High divergence → more uncertainty?
        high_div = first[first["kc_diff_pct"] > first["kc_diff_pct"].quantile(0.75)]
        low_div = first[first["kc_diff_pct"] <= first["kc_diff_pct"].quantile(0.25)]
        if len(high_div) > 5 and len(low_div) > 5:
            # Check if distance signal works better/worse
            high_sig = high_div[high_div["pct_dist"] >= 0.0015]
            low_sig = low_div[low_div["pct_dist"] >= 0.0015]
            if len(high_sig) >= 5:
                out.append(f"- High divergence + signal: {high_sig['correct_side'].mean()*100:.1f}% acc, n={len(high_sig)}")
            if len(low_sig) >= 5:
                out.append(f"- Low divergence + signal: {low_sig['correct_side'].mean()*100:.1f}% acc, n={len(low_sig)}")
    out.append("")

    # --- 2.13 Market Calibration ---
    out.append("## 2.13 Market Calibration Analysis\n")
    out.append("Is the market well-calibrated? (Do contracts priced at X% win X% of the time?)\n")

    # For each round, get the yes_mid at T+250-500
    t_snaps5 = snaps[(snaps["seconds_elapsed"] >= 250) & (snaps["seconds_elapsed"] <= 500)]
    first = t_snaps5.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()
    valid = first[(first["yes_bid"] > 0) & (first["yes_ask"] > 0) & (first["yes_ask"] < 1)].copy()
    valid["yes_mid"] = (valid["yes_bid"] + valid["yes_ask"]) / 2
    valid["actual_yes"] = (valid["outcome"] == "yes").astype(float)

    if len(valid) > 50:
        # Bin by implied probability
        bins = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        valid["prob_bin"] = pd.cut(valid["yes_mid"], bins=bins)
        cal = valid.groupby("prob_bin").agg(
            n=("actual_yes", "count"),
            implied_prob=("yes_mid", "mean"),
            actual_win_rate=("actual_yes", "mean"),
        ).dropna()
        cal["miscalibration"] = cal["actual_win_rate"] - cal["implied_prob"]
        out.append(cal.to_markdown() + "\n")
        out.append("Positive miscalibration = market underprices the outcome (potential edge).\n")

    # --- 2.14 Cross-platform analysis ---
    if len(pm) > 0:
        out.append("## 2.14 Cross-Platform (Polymarket vs Kalshi)\n")
        pm_15m = pm[(pm["file_duration"] == "15m")].copy()
        if len(pm_15m) > 0:
            # Check PM spread situation
            pm_15m_snaps = pm_15m[pm_15m["row_type"] == "snapshot"]
            quoted = pm_15m_snaps[(pm_15m_snaps["up_bid"] > 0.05) & (pm_15m_snaps["up_ask"] < 0.95)]
            out.append(f"PM 15m snapshots with tradeable quotes: {len(quoted):,} / {len(pm_15m_snaps):,}\n")

            if len(quoted) > 100:
                out.append("PM has tradeable 15m markets — cross-platform analysis possible.\n")
                # Try to match PM and Kalshi rounds by end_date / timestamp
                # This is complex — just report what we see
                pm_ends = pm_15m[pm_15m["row_type"].str.contains("end|resolved", case=False, na=False)]
                out.append(f"PM 15m resolved rounds: {len(pm_ends)}\n")
            else:
                out.append("PM 15m markets have very thin books — cross-platform arb not feasible yet.\n")

    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Phase 3: Exploitable Edge Quantification
# ---------------------------------------------------------------------------

def phase3_edge(kalshi: pd.DataFrame) -> str:
    out = []
    out.append("\n# Phase 3: Exploitable Edge Quantification\n")
    out.append("For each candidate strategy, compute realistic EV after fees, frequency, "
               "stability, and required speed.\n")

    ends = kalshi[kalshi["row_type"] == "round_end"][["round_ticker", "outcome"]].copy()
    ends = ends.drop_duplicates("round_ticker")
    ends = ends.rename(columns={"outcome": "round_outcome"})
    snaps = kalshi[kalshi["row_type"] == "snapshot"].copy()
    snaps = snaps.merge(ends, on="round_ticker", how="inner")
    snaps["abs_dist"] = (snaps["spot_price"] - snaps["strike"]).abs()
    snaps["pct_dist"] = snaps["abs_dist"] / snaps["strike"]
    snaps["spot_above"] = snaps["spot_price"] > snaps["strike"]
    snaps["outcome"] = snaps["round_outcome"]
    snaps["correct_side"] = ((snaps["spot_above"] & (snaps["outcome"] == "yes")) |
                              (~snaps["spot_above"] & (snaps["outcome"] == "no")))

    # --- 3.1 Full strategy simulation ---
    out.append("## 3.1 Strategy Simulation (all parameter combos)\n")
    out.append("Simulating: at first snapshot in window with dist>threshold, "
               "buy the ask. Hold to expiry.\n")

    combos = []
    for t_start, t_end in [(180, 400), (250, 500), (300, 540), (350, 600), (450, 700), (500, 800), (600, 850)]:
        for d_thresh in [0.001, 0.0015, 0.002, 0.003, 0.005]:
            for coins in [["BTC", "ETH", "XRP"], ["BTC", "ETH", "SOL", "XRP"],
                          ["ETH"], ["BTC", "ETH"], ["BTC"]]:
                coins_label = "+".join(coins)
                t_snaps = snaps[(snaps["seconds_elapsed"] >= t_start) &
                                (snaps["seconds_elapsed"] <= t_end) &
                                (snaps["coin"].isin(coins))]
                first = t_snaps.sort_values("seconds_elapsed").groupby(
                    ["coin", "round_ticker"]).first().reset_index()
                sig = first[first["pct_dist"] >= d_thresh].copy()

                if len(sig) < 20:
                    continue

                # Realistic entry price
                sig = sig.copy()
                sig["entry_price"] = np.where(
                    sig["spot_above"],
                    sig["yes_ask"],
                    np.where(sig["no_ask"] > 0, sig["no_ask"], 1 - sig["yes_bid"])
                )
                sig = sig[sig["entry_price"] > 0.01]
                if len(sig) < 20:
                    continue

                acc = sig["correct_side"].mean()
                med_price = sig["entry_price"].median()
                avg_price = sig["entry_price"].mean()
                fee = kalshi_fee(med_price)
                ev_per = acc * (1 - med_price - fee) - (1 - acc) * (med_price + fee)
                be_wr = (med_price + fee)

                # Daily frequency
                n_days = sig["file_date"].nunique()
                daily_trades = len(sig) / max(1, n_days)
                daily_ev = ev_per * daily_trades

                # Stability: accuracy per day
                day_acc = sig.groupby("file_date")["correct_side"].agg(["mean", "count"])
                worst_day = day_acc["mean"].min()
                best_day = day_acc["mean"].max()
                acc_std = day_acc["mean"].std()

                combos.append({
                    "window": f"T+{t_start}-{t_end}",
                    "dist": f">{d_thresh*100:.2f}%",
                    "coins": coins_label,
                    "n": len(sig),
                    "n_days": n_days,
                    "acc": acc,
                    "med_price": med_price,
                    "fee": fee,
                    "be_wr": be_wr,
                    "ev_per": ev_per,
                    "daily_trades": daily_trades,
                    "daily_ev": daily_ev,
                    "worst_day_acc": worst_day,
                    "best_day_acc": best_day,
                    "acc_std": acc_std,
                })

    if combos:
        combo_df = pd.DataFrame(combos)
        # Sort by daily EV
        combo_df = combo_df.sort_values("daily_ev", ascending=False)

        # Show top 20 by daily EV
        out.append("### Top 20 Strategies by Daily EV\n")
        top = combo_df.head(20).copy()
        top["acc_str"] = top["acc"].apply(lambda x: f"{x*100:.1f}%")
        top["ev_str"] = top["ev_per"].apply(lambda x: f"${x:.4f}")
        top["daily_ev_str"] = top["daily_ev"].apply(lambda x: f"${x:.3f}")
        top["med_price_str"] = top["med_price"].apply(lambda x: f"${x:.3f}")
        top["be_wr_str"] = top["be_wr"].apply(lambda x: f"{x*100:.1f}%")
        top["worst_str"] = top["worst_day_acc"].apply(lambda x: f"{x*100:.0f}%")
        display = top[["window", "dist", "coins", "n", "n_days", "acc_str", "med_price_str",
                        "be_wr_str", "ev_str", "daily_trades", "daily_ev_str", "worst_str"]]
        display.columns = ["Window", "Dist", "Coins", "N", "Days", "Acc", "MedPrice",
                           "BE_WR", "EV/trade", "Trades/day", "EV/day", "WorstDay"]
        out.append(display.to_markdown(index=False) + "\n")

        # Show top 20 by EV per trade (more conservative)
        out.append("### Top 20 Strategies by EV per Trade\n")
        top_ev = combo_df.sort_values("ev_per", ascending=False).head(20).copy()
        top_ev["acc_str"] = top_ev["acc"].apply(lambda x: f"{x*100:.1f}%")
        top_ev["ev_str"] = top_ev["ev_per"].apply(lambda x: f"${x:.4f}")
        top_ev["daily_ev_str"] = top_ev["daily_ev"].apply(lambda x: f"${x:.3f}")
        top_ev["med_price_str"] = top_ev["med_price"].apply(lambda x: f"${x:.3f}")
        top_ev["be_wr_str"] = top_ev["be_wr"].apply(lambda x: f"{x*100:.1f}%")
        top_ev["worst_str"] = top_ev["worst_day_acc"].apply(lambda x: f"{x*100:.0f}%")
        display2 = top_ev[["window", "dist", "coins", "n", "n_days", "acc_str", "med_price_str",
                            "be_wr_str", "ev_str", "daily_trades", "daily_ev_str", "worst_str"]]
        display2.columns = ["Window", "Dist", "Coins", "N", "Days", "Acc", "MedPrice",
                            "BE_WR", "EV/trade", "Trades/day", "EV/day", "WorstDay"]
        out.append(display2.to_markdown(index=False) + "\n")

        # Negative EV strategies (warning)
        neg = combo_df[combo_df["ev_per"] < 0]
        out.append(f"**{len(neg)} / {len(combo_df)} parameter combos are negative EV.**\n")
        if len(neg) > 0:
            out.append(f"Most negative: {neg.iloc[-1]['window']} {neg.iloc[-1]['dist']} "
                       f"{neg.iloc[-1]['coins']}: EV=${neg.iloc[-1]['ev_per']:.4f}\n")

    # --- 3.2 Day-by-day stability for top strategies ---
    out.append("## 3.2 Day-by-Day Stability (Top 3 Strategies)\n")
    if combos:
        top3 = combo_df.head(3)
        for _, strat in top3.iterrows():
            out.append(f"\n### {strat['window']} dist{strat['dist']} {strat['coins']}\n")
            # Re-run to get per-day breakdown
            params = strat['window'].replace("T+", "").split("-")
            t_start, t_end = int(params[0]), int(params[1])
            d_thresh = float(strat['dist'].replace(">", "").replace("%", "")) / 100
            coins = strat['coins'].split("+")

            t_snaps = snaps[(snaps["seconds_elapsed"] >= t_start) &
                            (snaps["seconds_elapsed"] <= t_end) &
                            (snaps["coin"].isin(coins))]
            first = t_snaps.sort_values("seconds_elapsed").groupby(
                ["coin", "round_ticker"]).first().reset_index()
            sig = first[first["pct_dist"] >= d_thresh].copy()
            sig["entry_price"] = np.where(
                sig["spot_above"],
                sig["yes_ask"],
                np.where(sig["no_ask"] > 0, sig["no_ask"], 1 - sig["yes_bid"])
            )
            sig = sig[sig["entry_price"] > 0.01]

            day_stats = sig.groupby("file_date").apply(
                lambda g: pd.Series({
                    "trades": len(g),
                    "accuracy": g["correct_side"].mean(),
                    "med_price": g["entry_price"].median(),
                    "wins": g["correct_side"].sum(),
                    "losses": (~g["correct_side"]).sum(),
                })
            )
            for d, row in day_stats.iterrows():
                fee = kalshi_fee(row["med_price"])
                ev = row["accuracy"] * (1 - row["med_price"] - fee) - (1 - row["accuracy"]) * (row["med_price"] + fee)
                out.append(f"  {d}: {int(row['trades'])} trades, "
                           f"{row['accuracy']*100:.0f}% acc ({int(row['wins'])}W/{int(row['losses'])}L), "
                           f"med_price=${row['med_price']:.3f}, EV=${ev:.4f}")

    # --- 3.3 What if we combine spot distance with book confirmation? ---
    out.append("\n## 3.3 Combined Signal: Spot Distance + Book Confirmation\n")
    out.append("Require both: dist>threshold AND book agrees (yes_mid supports direction).\n")

    for t_start, t_end in [(250, 500), (300, 540), (450, 700)]:
        for d_thresh in [0.0015, 0.002, 0.003]:
            t_snaps = snaps[(snaps["seconds_elapsed"] >= t_start) &
                            (snaps["seconds_elapsed"] <= t_end) &
                            (snaps["coin"].isin(["BTC", "ETH", "XRP"]))]
            first = t_snaps.sort_values("seconds_elapsed").groupby(
                ["coin", "round_ticker"]).first().reset_index()
            sig = first[first["pct_dist"] >= d_thresh].copy()
            sig["entry_price"] = np.where(
                sig["spot_above"],
                sig["yes_ask"],
                np.where(sig["no_ask"] > 0, sig["no_ask"], 1 - sig["yes_bid"])
            )
            sig = sig[sig["entry_price"] > 0.01]
            sig["yes_mid"] = (sig["yes_bid"] + sig["yes_ask"]) / 2
            valid_book = sig[(sig["yes_bid"] > 0) & (sig["yes_ask"] < 1)]

            if len(valid_book) < 10:
                continue

            # Book confirms spot direction
            book_confirms = valid_book[
                ((valid_book["spot_above"]) & (valid_book["yes_mid"] > 0.5)) |
                ((~valid_book["spot_above"]) & (valid_book["yes_mid"] < 0.5))
            ]
            book_disagrees = valid_book[
                ((valid_book["spot_above"]) & (valid_book["yes_mid"] <= 0.5)) |
                ((~valid_book["spot_above"]) & (valid_book["yes_mid"] >= 0.5))
            ]

            if len(book_confirms) >= 5:
                acc_c = book_confirms["correct_side"].mean()
                med_p = book_confirms["entry_price"].median()
                fee = kalshi_fee(med_p)
                ev = acc_c * (1 - med_p - fee) - (1 - acc_c) * (med_p + fee)
                out.append(f"T+{t_start}-{t_end} dist>{d_thresh*100:.2f}% + book confirms: "
                           f"acc={acc_c*100:.1f}%, n={len(book_confirms)}, "
                           f"med=${med_p:.3f}, EV=${ev:.4f}")
            if len(book_disagrees) >= 5:
                acc_d = book_disagrees["correct_side"].mean()
                out.append(f"  ... book disagrees: acc={acc_d*100:.1f}%, n={len(book_disagrees)}")

    out.append("")

    # --- 3.4 Optimal single-coin strategies ---
    out.append("## 3.4 Optimal Per-Coin Strategy\n")
    out.append("Best parameters per coin (by EV/trade, min 30 samples):\n")

    if combos:
        combo_df_pos = combo_df[(combo_df["ev_per"] > 0) & (combo_df["n"] >= 30)]
        for coin_label in ["ETH", "BTC", "XRP", "SOL", "BTC+ETH+XRP", "BTC+ETH", "BTC+ETH+SOL+XRP"]:
            coin_strats = combo_df_pos[combo_df_pos["coins"] == coin_label]
            if len(coin_strats) > 0:
                best = coin_strats.sort_values("ev_per", ascending=False).iloc[0]
                out.append(f"\n**{coin_label}**: {best['window']} dist{best['dist']}")
                out.append(f"  Acc={best['acc']*100:.1f}%, MedPrice=${best['med_price']:.3f}, "
                           f"EV/trade=${best['ev_per']:.4f}, {best['daily_trades']:.1f} trades/day, "
                           f"EV/day=${best['daily_ev']:.3f}, n={best['n']}, "
                           f"worst_day={best['worst_day_acc']*100:.0f}%")

    # --- 3.5 Statistical significance ---
    out.append("\n## 3.5 Statistical Significance\n")
    out.append("For top strategies, compute 95% confidence intervals using Wilson score.\n")

    if combos:
        top5 = combo_df.head(5)
        for _, strat in top5.iterrows():
            n = strat["n"]
            p = strat["acc"]
            z = 1.96
            denom = 1 + z**2 / n
            center = (p + z**2 / (2 * n)) / denom
            margin = z * np.sqrt((p * (1 - p) + z**2 / (4 * n)) / n) / denom
            lo, hi = center - margin, center + margin
            fee = kalshi_fee(strat["med_price"])
            be_wr = strat["med_price"] + fee

            ev_lo = lo * (1 - strat["med_price"] - fee) - (1 - lo) * (strat["med_price"] + fee)
            ev_hi = hi * (1 - strat["med_price"] - fee) - (1 - hi) * (strat["med_price"] + fee)

            out.append(f"\n**{strat['window']} dist{strat['dist']} {strat['coins']}** (n={n})")
            out.append(f"  Accuracy: {p*100:.1f}% (95% CI: {lo*100:.1f}% - {hi*100:.1f}%)")
            out.append(f"  Break-even WR: {be_wr*100:.1f}%")
            out.append(f"  EV/trade: ${strat['ev_per']:.4f} (95% CI: ${ev_lo:.4f} to ${ev_hi:.4f})")
            if lo > be_wr:
                out.append(f"  ✓ Lower bound {lo*100:.1f}% > BE {be_wr*100:.1f}% — statistically significant edge")
            else:
                out.append(f"  ✗ Lower bound {lo*100:.1f}% ≤ BE {be_wr*100:.1f}% — NOT statistically significant")

    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Phase 4: Strategy Recommendations
# ---------------------------------------------------------------------------

def phase4_recommendations(kalshi: pd.DataFrame) -> str:
    out = []
    out.append("\n# Phase 4: Strategy Recommendations\n")
    out.append("Based on the analysis above, here are concrete recommendations.\n")

    # This section will be populated based on the data — the actual content
    # is written after we see Phase 3 results. For now, generate the framework.

    out.append("## 4.1 Primary Strategy Recommendation\n")
    out.append("*[Filled in after Phase 3 analysis runs]*\n")

    out.append("## 4.2 Alternative Strategies\n")
    out.append("*[Filled in after Phase 3 analysis runs]*\n")

    out.append("## 4.3 What NOT to Do\n")
    out.append("*[Filled in after Phase 3 analysis runs]*\n")

    out.append("## 4.4 Infrastructure Requirements\n")
    out.append("*[Filled in after Phase 3 analysis runs]*\n")

    out.append("## 4.5 Risk Assessment\n")
    out.append("*[Filled in after Phase 3 analysis runs]*\n")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading Kalshi data...")
    kalshi = load_kalshi_rounds()
    print(f"  {len(kalshi):,} rows, {kalshi['round_ticker'].nunique():,} unique rounds, "
          f"{kalshi['coin'].nunique()} coins, {kalshi['file_date'].nunique()} days")

    print("Loading Polymarket data...")
    pm = load_pm_rounds()
    if len(pm) > 0:
        print(f"  {len(pm):,} rows, {pm['coin'].nunique()} coins, "
              f"{pm['file_duration'].nunique()} durations")
    else:
        print("  No PM data loaded")

    print("\n=== Phase 1: Data Understanding ===")
    p1 = phase1_understand(kalshi, pm)
    print("  Done.")

    print("\n=== Phase 2: Pattern Discovery ===")
    p2 = phase2_patterns(kalshi, pm)
    print("  Done.")

    print("\n=== Phase 3: Edge Quantification ===")
    p3 = phase3_edge(kalshi)
    print("  Done.")

    print("\n=== Phase 4: Recommendations ===")
    p4 = phase4_recommendations(kalshi)
    print("  Done.")

    # Write output
    report = f"""---
title: V3 Data Analysis — Comprehensive Edge Search
date: 2026-03-17
data: Kalshi rounds Mar 8-17 (9 days), Polymarket rounds Mar 10-17
---

{p1}

{p2}

{p3}

{p4}
"""

    OUT_FILE.write_text(report)
    print(f"\nReport written to {OUT_FILE}")
    print("\n" + "=" * 80)
    print("SUMMARY — printing key sections to console:")
    print("=" * 80)

    # Print the most important parts
    for section in [p1, p2, p3]:
        print(section[:5000] if len(section) > 5000 else section)
        if len(section) > 5000:
            print(f"\n... [truncated, {len(section)} chars total — see full report] ...\n")


if __name__ == "__main__":
    main()
