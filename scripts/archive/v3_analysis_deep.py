"""
V3 Deep Analysis — Follow-up on promising signals from initial analysis.

Key angles to investigate:
1. Calibration edge: the 0.70-0.80 price bin wins 80.5% vs 74.9% implied
2. Low-volatility filter: 92.4% accuracy in low-vol rounds
3. Limit order strategy (buy at bid/mid instead of ask)
4. ETH-specific edge (only coin with positive EV)
5. Polymarket cross-platform (PM 15m vs Kalshi 15m)
6. Price-level entry strategy (enter at specific price levels)
7. Combo: low-vol + distance + ETH
"""
from __future__ import annotations

import warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

PROJECT = Path(__file__).resolve().parent.parent
KALSHI_DIR = PROJECT / "data" / "rounds"
PM_DIR = PROJECT / "data" / "rounds" / "polymarket"
FEE_COEFF = 0.07

OUT_FILE = PROJECT / "research" / "v3-data-analysis.md"


def kalshi_fee(price: float) -> float:
    return np.ceil(FEE_COEFF * price * (1 - price) * 100) / 100


def load_kalshi():
    frames = []
    for f in sorted(KALSHI_DIR.glob("KX*15M-*.csv")):
        coin = f.name.split("-")[0].replace("KX", "").replace("15M", "")
        df = pd.read_csv(f, engine="python", on_bad_lines="skip")
        df["coin"] = coin
        df["file_date"] = f.name.split("-", 1)[1].replace(".csv", "")
        frames.append(df)
    all_df = pd.concat(frames, ignore_index=True)
    all_df["timestamp"] = pd.to_datetime(all_df["timestamp"], format="ISO8601", utc=True)
    for col in ["strike", "spot_price", "yes_bid", "yes_ask", "no_bid", "no_ask",
                 "volume", "seconds_remaining", "seconds_elapsed",
                 "spot_minus_strike", "spot_move_pct", "kraken_spot"]:
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce")
    return all_df


def load_pm():
    frames = []
    for f in sorted(PM_DIR.glob("*.csv")):
        parts = f.stem.split("-")
        coin = parts[0]
        duration = parts[1]
        df = pd.read_csv(f, engine="python", on_bad_lines="skip")
        df["coin"] = coin
        df["file_duration"] = duration
        frames.append(df)
    all_df = pd.concat(frames, ignore_index=True)
    all_df["timestamp"] = pd.to_datetime(all_df["timestamp"], format="ISO8601", utc=True)
    for col in ["up_bid", "up_ask", "down_bid", "down_ask", "up_midpoint",
                 "spread", "last_trade_price", "spot_price", "kraken_price",
                 "rtds_price", "volume", "seconds_remaining"]:
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce")
    return all_df


def prepare_snaps(kalshi):
    ends = kalshi[kalshi["row_type"] == "round_end"][["round_ticker", "outcome"]].copy()
    ends = ends.drop_duplicates("round_ticker")
    ends = ends.rename(columns={"outcome": "round_outcome"})
    snaps = kalshi[kalshi["row_type"] == "snapshot"].copy()
    snaps = snaps.merge(ends, on="round_ticker", how="inner")
    snaps["pct_dist"] = (snaps["spot_price"] - snaps["strike"]).abs() / snaps["strike"]
    snaps["spot_above"] = snaps["spot_price"] > snaps["strike"]
    snaps["outcome"] = snaps["round_outcome"]
    snaps["correct_side"] = ((snaps["spot_above"] & (snaps["outcome"] == "yes")) |
                              (~snaps["spot_above"] & (snaps["outcome"] == "no")))
    snaps["yes_mid"] = (snaps["yes_bid"] + snaps["yes_ask"]) / 2
    snaps["yes_spread"] = snaps["yes_ask"] - snaps["yes_bid"]
    return snaps


def main():
    print("Loading data...")
    kalshi = load_kalshi()
    pm = load_pm()
    snaps = prepare_snaps(kalshi)

    out = []
    out.append("\n\n# Phase 2B: Deep Pattern Analysis\n")

    # =========================================================================
    # 1. CALIBRATION EDGE — drill into the 0.70-0.80 miscalibration
    # =========================================================================
    out.append("## 2B.1 Calibration Edge: The $0.70-$0.80 Sweet Spot\n")
    out.append("Initial finding: contracts priced $0.70-$0.80 win 80.5% vs 74.9% implied.\n"
               "Can we exploit this? Does it persist when filtered by our signals?\n")

    for t_start, t_end in [(200, 450), (250, 500), (300, 540), (350, 600)]:
        out.append(f"\n### Window T+{t_start}-{t_end}\n")
        t_snaps = snaps[(snaps["seconds_elapsed"] >= t_start) & (snaps["seconds_elapsed"] <= t_end)]
        first = t_snaps.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()
        valid = first[(first["yes_bid"] > 0) & (first["yes_ask"] < 1)].copy()

        # For each price bucket, check: if we BET THE FAVORED SIDE at the ASK,
        # what's the EV?
        valid["favored_yes"] = valid["yes_mid"] > 0.5
        valid["entry_price"] = np.where(
            valid["favored_yes"],
            valid["yes_ask"],
            np.where(valid["no_ask"] > 0, valid["no_ask"], 1 - valid["yes_bid"])
        )
        valid["won"] = np.where(
            valid["favored_yes"],
            valid["outcome"] == "yes",
            valid["outcome"] == "no"
        )

        bins = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 1.00)]
        for lo, hi in bins:
            # Filter by yes_mid (or 1-yes_mid for no side)
            sel_yes = valid[(valid["favored_yes"]) & (valid["yes_mid"] >= lo) & (valid["yes_mid"] < hi)]
            sel_no = valid[(~valid["favored_yes"]) & ((1 - valid["yes_mid"]) >= lo) & ((1 - valid["yes_mid"]) < hi)]
            sel = pd.concat([sel_yes, sel_no])

            if len(sel) < 10:
                continue

            wr = sel["won"].mean()
            med_price = sel["entry_price"].median()
            avg_price = sel["entry_price"].mean()
            fee = kalshi_fee(med_price)
            ev = wr * (1 - med_price - fee) - (1 - wr) * (med_price + fee)

            # What if we buy at bid instead?
            sel_bid = sel.copy()
            sel_bid["bid_price"] = np.where(
                sel_bid["favored_yes"],
                sel_bid["yes_bid"],
                np.where(sel_bid["no_bid"] > 0, sel_bid["no_bid"], 1 - sel_bid["yes_ask"])
            )
            med_bid = sel_bid["bid_price"].median()
            fee_bid = kalshi_fee(med_bid)
            ev_bid = wr * (1 - med_bid - fee_bid) - (1 - wr) * (med_bid + fee_bid)

            # What if we buy at mid?
            mid_price = (med_price + med_bid) / 2
            fee_mid = kalshi_fee(mid_price)
            ev_mid = wr * (1 - mid_price - fee_mid) - (1 - wr) * (mid_price + fee_mid)

            out.append(f"  Implied {lo:.0%}-{hi:.0%}: n={len(sel)}, WR={wr*100:.1f}%, "
                       f"ask=${med_price:.3f} (EV=${ev:.4f}), "
                       f"mid=${mid_price:.3f} (EV=${ev_mid:.4f}), "
                       f"bid=${med_bid:.3f} (EV=${ev_bid:.4f})")

    # =========================================================================
    # 2. LOW VOLATILITY FILTER
    # =========================================================================
    out.append("\n\n## 2B.2 Low Volatility Filter\n")
    out.append("Low-vol rounds had 92.4% accuracy vs 76.4% for high-vol.\n"
               "But are the prices cheaper in low-vol rounds (making it exploitable)?\n")

    # Compute per-round volatility (std of spot_move_pct in first 250s)
    early = snaps[snaps["seconds_elapsed"] <= 250]
    round_vol = early.groupby("round_ticker").agg(
        vol=("spot_move_pct", "std"),
    ).dropna()

    # Get signal snapshots
    for t_start, t_end in [(250, 500), (350, 600)]:
        out.append(f"\n### Window T+{t_start}-{t_end}\n")
        t_snaps = snaps[(snaps["seconds_elapsed"] >= t_start) & (snaps["seconds_elapsed"] <= t_end)]
        first = t_snaps.sort_values("seconds_elapsed").groupby(["coin", "round_ticker"]).first().reset_index()

        for d_thresh in [0.0015, 0.002, 0.003]:
            sig = first[first["pct_dist"] >= d_thresh].copy()
            sig["entry_price"] = np.where(
                sig["spot_above"],
                sig["yes_ask"],
                np.where(sig["no_ask"] > 0, sig["no_ask"], 1 - sig["yes_bid"])
            )
            sig = sig[sig["entry_price"] > 0.01]
            sig = sig.merge(round_vol[["vol"]], left_on="round_ticker", right_index=True, how="inner")

            if len(sig) < 30:
                continue

            # Split into vol terciles
            sig["vol_tercile"] = pd.qcut(sig["vol"], 3, labels=["low", "mid", "high"], duplicates="drop")
            for v in ["low", "mid", "high"]:
                sel = sig[sig["vol_tercile"] == v]
                if len(sel) < 5:
                    continue
                acc = sel["correct_side"].mean()
                med_p = sel["entry_price"].median()
                fee = kalshi_fee(med_p)
                ev = acc * (1 - med_p - fee) - (1 - acc) * (med_p + fee)
                out.append(f"  dist>{d_thresh*100:.2f}% vol={v}: n={len(sel)}, "
                           f"acc={acc*100:.1f}%, med_price=${med_p:.3f}, EV=${ev:.4f}")

    # =========================================================================
    # 3. ETH-SPECIFIC DEEP DIVE
    # =========================================================================
    out.append("\n\n## 2B.3 ETH Deep Dive\n")
    out.append("ETH is the only coin with positive EV. Let's understand why and if it's real.\n")

    eth_snaps = snaps[snaps["coin"] == "ETH"].copy()
    eth_first = {}
    for t_start, t_end in [(180, 400), (200, 450), (250, 500), (300, 540), (350, 600)]:
        t_s = eth_snaps[(eth_snaps["seconds_elapsed"] >= t_start) & (eth_snaps["seconds_elapsed"] <= t_end)]
        first = t_s.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()
        eth_first[(t_start, t_end)] = first

    out.append("\n### ETH: Full parameter sweep\n")
    for (t_start, t_end), first in eth_first.items():
        for d_thresh in [0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005]:
            sig = first[first["pct_dist"] >= d_thresh].copy()
            sig["entry_price"] = np.where(
                sig["spot_above"],
                sig["yes_ask"],
                np.where(sig["no_ask"] > 0, sig["no_ask"], 1 - sig["yes_bid"])
            )
            sig = sig[sig["entry_price"] > 0.01]
            if len(sig) < 15:
                continue

            acc = sig["correct_side"].mean()
            med_p = sig["entry_price"].median()
            fee = kalshi_fee(med_p)
            ev = acc * (1 - med_p - fee) - (1 - acc) * (med_p + fee)

            # Also compute at bid
            sig["bid_price"] = np.where(
                sig["spot_above"],
                sig["yes_bid"],
                np.where(sig["no_bid"] > 0, sig["no_bid"], 1 - sig["yes_ask"])
            )
            med_bid = sig["bid_price"].median()
            fee_bid = kalshi_fee(med_bid)
            ev_bid = acc * (1 - med_bid - fee_bid) - (1 - acc) * (med_bid + fee_bid)

            n_days = sig["file_date"].nunique()
            daily = len(sig) / max(1, n_days)

            marker = "✓" if ev > 0 else " "
            marker_bid = "✓" if ev_bid > 0 else " "
            out.append(f"  {marker} T+{t_start}-{t_end} d>{d_thresh*100:.2f}%: "
                       f"n={len(sig)}, acc={acc*100:.1f}%, "
                       f"ask=${med_p:.3f} EV=${ev:.4f}, "
                       f"bid=${med_bid:.3f} EV={marker_bid}${ev_bid:.4f}, "
                       f"{daily:.1f}/day")

    # =========================================================================
    # 4. LIMIT ORDER ANALYSIS — what fill rate is needed?
    # =========================================================================
    out.append("\n\n## 2B.4 Limit Order Break-Even Analysis\n")
    out.append("If we post limit orders at the bid (or mid), what fill rate do we need "
               "for positive EV?\n")

    # For the best strategy (ETH T+250-500 dist>0.20%)
    t_snaps = eth_snaps[(eth_snaps["seconds_elapsed"] >= 250) & (eth_snaps["seconds_elapsed"] <= 500)]
    first = t_snaps.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()
    sig = first[first["pct_dist"] >= 0.002].copy()
    sig["ask_price"] = np.where(
        sig["spot_above"],
        sig["yes_ask"],
        np.where(sig["no_ask"] > 0, sig["no_ask"], 1 - sig["yes_bid"])
    )
    sig["bid_price"] = np.where(
        sig["spot_above"],
        sig["yes_bid"],
        np.where(sig["no_bid"] > 0, sig["no_bid"], 1 - sig["yes_ask"])
    )
    sig = sig[(sig["ask_price"] > 0.01) & (sig["bid_price"] > 0.01)]

    if len(sig) > 10:
        acc = sig["correct_side"].mean()
        med_ask = sig["ask_price"].median()
        med_bid = sig["bid_price"].median()
        med_mid = (med_ask + med_bid) / 2

        out.append(f"\nETH T+250-500 dist>0.20%: n={len(sig)}, acc={acc*100:.1f}%\n")

        for label, price in [("Ask", med_ask), ("Mid", med_mid), ("Bid", med_bid)]:
            fee = kalshi_fee(price)
            ev = acc * (1 - price - fee) - (1 - acc) * (price + fee)
            be_wr = (price + fee)
            out.append(f"  {label} ${price:.3f}: fee=${fee:.4f}, EV=${ev:.4f}, BE_WR={be_wr*100:.1f}%")

        # For bid entry: what fill rate makes total EV zero?
        fee_bid = kalshi_fee(med_bid)
        ev_per_fill = acc * (1 - med_bid - fee_bid) - (1 - acc) * (med_bid + fee_bid)
        if ev_per_fill > 0:
            out.append(f"\n  At bid: every filled trade = ${ev_per_fill:.4f} EV. "
                       f"Even with low fill rate, this is profitable per fill.")
            # But opportunity cost: each unfilled trade = $0 (no cost)
            out.append(f"  Daily filled trades needed for $0.10/day: {0.10/ev_per_fill:.0f}")
        else:
            out.append(f"\n  At bid: EV per fill is still ${ev_per_fill:.4f} — even bid entry is negative EV.")

    # =========================================================================
    # 5. PRICE LEVEL STRATEGY — only enter when ask is cheap enough
    # =========================================================================
    out.append("\n\n## 2B.5 Price-Capped Entry Strategy\n")
    out.append("Instead of buying whenever there's a signal, only buy when the ask is below "
               "a certain level. This avoids overpaying.\n")

    for t_start, t_end in [(250, 500), (200, 450), (350, 600)]:
        out.append(f"\n### Window T+{t_start}-{t_end} (all coins)\n")
        t_snaps = snaps[(snaps["seconds_elapsed"] >= t_start) & (snaps["seconds_elapsed"] <= t_end)]
        first = t_snaps.sort_values("seconds_elapsed").groupby(["coin", "round_ticker"]).first().reset_index()
        sig = first[first["pct_dist"] >= 0.0015].copy()
        sig["entry_price"] = np.where(
            sig["spot_above"],
            sig["yes_ask"],
            np.where(sig["no_ask"] > 0, sig["no_ask"], 1 - sig["yes_bid"])
        )
        sig = sig[sig["entry_price"] > 0.01]

        for max_price in [0.70, 0.75, 0.80, 0.82, 0.85, 0.88, 0.90]:
            sel = sig[sig["entry_price"] <= max_price]
            if len(sel) < 15:
                continue
            acc = sel["correct_side"].mean()
            med_p = sel["entry_price"].median()
            fee = kalshi_fee(med_p)
            ev = acc * (1 - med_p - fee) - (1 - acc) * (med_p + fee)
            n_days = sel["file_date"].nunique()
            daily = len(sel) / max(1, n_days)
            coins_used = sel["coin"].nunique()
            coin_list = "+".join(sorted(sel["coin"].unique()))
            marker = "✓" if ev > 0 else " "
            out.append(f"  {marker} ask≤${max_price:.2f}: n={len(sel)}, {coins_used} coins ({coin_list}), "
                       f"acc={acc*100:.1f}%, med=${med_p:.3f}, fee=${fee:.4f}, "
                       f"EV=${ev:.4f}, {daily:.1f}/day")

    # =========================================================================
    # 6. ETH + PRICE CAP
    # =========================================================================
    out.append("\n\n## 2B.6 ETH + Price Cap\n")
    for t_start, t_end in [(200, 450), (250, 500), (180, 400)]:
        out.append(f"\n### ETH T+{t_start}-{t_end}\n")
        t_s = eth_snaps[(eth_snaps["seconds_elapsed"] >= t_start) & (eth_snaps["seconds_elapsed"] <= t_end)]
        first = t_s.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()

        for d_thresh in [0.0015, 0.002, 0.003]:
            sig = first[first["pct_dist"] >= d_thresh].copy()
            sig["entry_price"] = np.where(
                sig["spot_above"],
                sig["yes_ask"],
                np.where(sig["no_ask"] > 0, sig["no_ask"], 1 - sig["yes_bid"])
            )
            sig = sig[sig["entry_price"] > 0.01]

            for max_price in [0.75, 0.80, 0.85, 0.88]:
                sel = sig[sig["entry_price"] <= max_price]
                if len(sel) < 10:
                    continue
                acc = sel["correct_side"].mean()
                med_p = sel["entry_price"].median()
                fee = kalshi_fee(med_p)
                ev = acc * (1 - med_p - fee) - (1 - acc) * (med_p + fee)
                n_days = sel["file_date"].nunique()
                daily = len(sel) / max(1, n_days)
                marker = "✓" if ev > 0 else " "
                out.append(f"  {marker} d>{d_thresh*100:.2f}% ask≤${max_price:.2f}: "
                           f"n={len(sel)}, acc={acc*100:.1f}%, med=${med_p:.3f}, "
                           f"EV=${ev:.4f}, {daily:.1f}/day, daily_EV=${ev*daily:.3f}")

    # =========================================================================
    # 7. POLYMARKET ANALYSIS
    # =========================================================================
    out.append("\n\n## 2B.7 Polymarket Analysis\n")

    pm_ends = pm[pm["row_type"].str.contains("end|resolved", case=False, na=False)]
    pm_snaps = pm[pm["row_type"] == "snapshot"]

    for dur in ["5m", "15m"]:
        out.append(f"\n### PM {dur} Markets\n")
        dur_ends = pm_ends[pm_ends["file_duration"] == dur].copy()
        dur_snaps = pm_snaps[pm_snaps["file_duration"] == dur].copy()

        if len(dur_ends) < 10:
            out.append(f"Only {len(dur_ends)} resolved rounds — skipping.\n")
            continue

        # Merge outcomes
        dur_ends_unique = dur_ends[["slug", "outcome"]].drop_duplicates("slug")
        dur_ends_unique = dur_ends_unique.rename(columns={"outcome": "round_outcome"})
        merged = dur_snaps.merge(dur_ends_unique, on="slug", how="inner")

        out.append(f"Resolved rounds: {len(dur_ends_unique)}")
        out.append(f"Snapshots with outcome: {len(merged):,}")
        out.append(f"Base rate: {(dur_ends_unique['round_outcome'] == 'up').mean()*100:.1f}% up\n")

        # Check book quality
        quoted = merged[(merged["up_bid"] > 0.05) & (merged["up_ask"] < 0.95)]
        out.append(f"Snapshots with quoted book: {len(quoted):,} ({100*len(quoted)/max(1,len(merged)):.1f}%)")

        if len(quoted) < 100:
            out.append("Insufficient quoted data for analysis.\n")
            continue

        quoted = quoted.copy()
        # Does the PM midpoint predict outcome?
        quoted["pm_says_up"] = quoted["up_midpoint"] > 0.5
        quoted["actual_up"] = quoted["round_outcome"] == "up"
        quoted["pm_correct"] = quoted["pm_says_up"] == quoted["actual_up"]

        # What's the spread?
        quoted["up_spread"] = quoted["up_ask"] - quoted["up_bid"]
        out.append(f"Median spread: ${quoted['up_spread'].median():.3f}")

        # PM midpoint accuracy by time remaining
        time_bins = [(240, 300), (180, 240), (120, 180), (60, 120), (30, 60)]
        if dur == "5m":
            time_bins = [(200, 300), (120, 200), (60, 120), (30, 60)]

        for t_lo, t_hi in time_bins:
            sel = quoted[(quoted["seconds_remaining"] >= t_lo) & (quoted["seconds_remaining"] < t_hi)]
            first_per = sel.sort_values("seconds_remaining", ascending=False).groupby("slug").first().reset_index()
            if len(first_per) < 10:
                continue
            acc = first_per["pm_correct"].mean()
            out.append(f"  T-{t_hi}s to T-{t_lo}s: midpoint accuracy {acc*100:.1f}%, n={len(first_per)}")

        # Calibration check
        out.append(f"\n### PM {dur} Calibration\n")
        # Take one snapshot per round at ~halfway
        if dur == "15m":
            mid_time = quoted[(quoted["seconds_remaining"] >= 400) & (quoted["seconds_remaining"] <= 600)]
        else:
            mid_time = quoted[(quoted["seconds_remaining"] >= 120) & (quoted["seconds_remaining"] <= 200)]

        first_per = mid_time.sort_values("seconds_remaining").groupby("slug").first().reset_index()
        if len(first_per) > 20:
            bins = [0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
            first_per["prob_bin"] = pd.cut(first_per["up_midpoint"], bins=bins)
            cal = first_per.groupby("prob_bin").agg(
                n=("actual_up", "count"),
                implied=("up_midpoint", "mean"),
                actual=("actual_up", "mean"),
            ).dropna()
            cal["miscal"] = cal["actual"] - cal["implied"]
            out.append(cal.to_markdown() + "\n")

        # PM volume — is there enough liquidity to trade?
        out.append(f"\n### PM {dur} Volume\n")
        round_vol = dur_snaps.groupby("slug")["volume"].max()
        out.append(f"Volume per round: median=${round_vol.median():.0f}, "
                   f"mean=${round_vol.mean():.0f}, max=${round_vol.max():.0f}")

    # =========================================================================
    # 8. CROSS-PLATFORM: PM price predicts Kalshi outcome?
    # =========================================================================
    out.append("\n\n## 2B.8 Cross-Platform: PM 15m → Kalshi 15m\n")
    out.append("Do PM prices help predict Kalshi outcomes for the same 15m window?\n"
               "Different resolution sources (PM: Binance/Chainlink, Kalshi: CF Benchmarks).\n")

    # Match by approximate time and coin
    kalshi_ends = kalshi[kalshi["row_type"] == "round_end"].copy()
    pm_15m_ends = pm_ends[pm_ends["file_duration"] == "15m"].copy()

    if len(pm_15m_ends) > 0:
        kalshi_ends["round_time"] = kalshi_ends["timestamp"].dt.floor("15min")
        pm_15m_ends["round_time"] = pd.to_datetime(pm_15m_ends["end_date"], utc=True).dt.floor("15min")

        # Try to match
        k_pivot = kalshi_ends[["coin", "round_time", "outcome"]].copy()
        k_pivot = k_pivot.rename(columns={"outcome": "kalshi_outcome"})

        p_pivot = pm_15m_ends[["coin", "round_time", "outcome"]].copy()
        p_pivot = p_pivot.rename(columns={"outcome": "pm_outcome"})

        # Normalize coin names
        p_pivot["coin"] = p_pivot["coin"].str.upper()

        cross = k_pivot.merge(p_pivot, on=["coin", "round_time"], how="inner")
        out.append(f"\nMatched cross-platform rounds: {len(cross)}")

        if len(cross) > 20:
            # Do they agree?
            cross["k_yes"] = cross["kalshi_outcome"] == "yes"
            cross["p_up"] = cross["pm_outcome"] == "up"
            cross["agree"] = cross["k_yes"] == cross["p_up"]
            agree_pct = cross["agree"].mean()
            out.append(f"Agreement rate: {agree_pct*100:.1f}%")
            out.append(f"When they disagree: {len(cross[~cross['agree']])} times\n")

            # Per coin
            for coin in sorted(cross["coin"].unique()):
                c = cross[cross["coin"] == coin]
                if len(c) > 5:
                    agree = c["agree"].mean()
                    out.append(f"  {coin}: {agree*100:.1f}% agreement, n={len(c)}")
    else:
        out.append("No PM 15m resolved rounds available for cross-platform analysis.\n")

    # =========================================================================
    # 9. COMBINATION: ETH + low vol + distance
    # =========================================================================
    out.append("\n\n## 2B.9 Triple Filter: ETH + Low Vol + Distance\n")

    early_eth = snaps[(snaps["coin"] == "ETH") & (snaps["seconds_elapsed"] <= 250)]
    eth_vol = early_eth.groupby("round_ticker").agg(vol=("spot_move_pct", "std")).dropna()
    vol_33 = eth_vol["vol"].quantile(0.33)

    for t_start, t_end in [(250, 500), (200, 450), (300, 540)]:
        out.append(f"\n### ETH T+{t_start}-{t_end} + low vol (<{vol_33:.6f})\n")
        t_s = eth_snaps[(eth_snaps["seconds_elapsed"] >= t_start) & (eth_snaps["seconds_elapsed"] <= t_end)]
        first = t_s.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()
        first = first.merge(eth_vol, left_on="round_ticker", right_index=True, how="inner")
        low_vol = first[first["vol"] <= vol_33]

        for d_thresh in [0.001, 0.0015, 0.002, 0.003]:
            sig = low_vol[low_vol["pct_dist"] >= d_thresh].copy()
            sig["entry_price"] = np.where(
                sig["spot_above"],
                sig["yes_ask"],
                np.where(sig["no_ask"] > 0, sig["no_ask"], 1 - sig["yes_bid"])
            )
            sig = sig[sig["entry_price"] > 0.01]
            if len(sig) < 8:
                continue

            acc = sig["correct_side"].mean()
            med_p = sig["entry_price"].median()
            fee = kalshi_fee(med_p)
            ev = acc * (1 - med_p - fee) - (1 - acc) * (med_p + fee)
            n_days = sig["file_date"].nunique()
            daily = len(sig) / max(1, n_days)
            marker = "✓" if ev > 0 else " "
            out.append(f"  {marker} d>{d_thresh*100:.2f}%: n={len(sig)}, acc={acc*100:.1f}%, "
                       f"med=${med_p:.3f}, EV=${ev:.4f}, {daily:.1f}/day")

    # =========================================================================
    # 10. FINAL: BTC spread advantage
    # =========================================================================
    out.append("\n\n## 2B.10 BTC Spread Advantage\n")
    out.append("BTC has $0.01 median spread vs $0.03 for others. "
               "Does this translate to better EV at the mid?\n")

    btc_snaps = snaps[snaps["coin"] == "BTC"]
    for t_start, t_end in [(250, 500), (350, 600)]:
        out.append(f"\n### BTC T+{t_start}-{t_end}\n")
        t_s = btc_snaps[(btc_snaps["seconds_elapsed"] >= t_start) & (btc_snaps["seconds_elapsed"] <= t_end)]
        first = t_s.sort_values("seconds_elapsed").groupby("round_ticker").first().reset_index()

        for d_thresh in [0.0015, 0.002, 0.003]:
            sig = first[first["pct_dist"] >= d_thresh].copy()
            sig["ask_price"] = np.where(
                sig["spot_above"],
                sig["yes_ask"],
                np.where(sig["no_ask"] > 0, sig["no_ask"], 1 - sig["yes_bid"])
            )
            sig["bid_price"] = np.where(
                sig["spot_above"],
                sig["yes_bid"],
                np.where(sig["no_bid"] > 0, sig["no_bid"], 1 - sig["yes_ask"])
            )
            sig = sig[(sig["ask_price"] > 0.01) & (sig["bid_price"] > 0.01)]
            if len(sig) < 10:
                continue

            acc = sig["correct_side"].mean()
            med_ask = sig["ask_price"].median()
            med_bid = sig["bid_price"].median()
            spread = med_ask - med_bid

            fee_ask = kalshi_fee(med_ask)
            ev_ask = acc * (1 - med_ask - fee_ask) - (1 - acc) * (med_ask + fee_ask)

            fee_bid = kalshi_fee(med_bid)
            ev_bid = acc * (1 - med_bid - fee_bid) - (1 - acc) * (med_bid + fee_bid)

            mid = (med_ask + med_bid) / 2
            fee_mid = kalshi_fee(mid)
            ev_mid = acc * (1 - mid - fee_mid) - (1 - acc) * (mid + fee_mid)

            out.append(f"  d>{d_thresh*100:.2f}%: n={len(sig)}, acc={acc*100:.1f}%, "
                       f"spread=${spread:.3f}, "
                       f"ask EV=${ev_ask:.4f}, mid EV=${ev_mid:.4f}, bid EV=${ev_bid:.4f}")

    text = "\n".join(out)
    print(text)

    # Append to the main report
    existing = OUT_FILE.read_text()
    # Insert before Phase 3
    if "# Phase 3:" in existing:
        parts = existing.split("# Phase 3:")
        updated = parts[0] + text + "\n\n# Phase 3:" + parts[1]
    else:
        updated = existing + "\n" + text

    OUT_FILE.write_text(updated)
    print(f"\nAppended to {OUT_FILE}")


if __name__ == "__main__":
    main()
