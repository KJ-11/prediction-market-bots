"""
V3 Creative Analysis — exploring angles beyond spot-distance.

1. "Free money" at extreme prices: If contract is at $0.95 with 3 min left, is that free 5%?
2. PM whale trades → Kalshi signal (corrected PM data with inverted midpoint)
3. Late-round reversion: when does a $0.90+ contract actually lose?
4. Spread capture: can we buy/sell around the midpoint profitably?
5. PM price leads → Kalshi price lags?
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
    # FIX: "up" token is actually the DOWN token. Invert midpoint.
    all_df["actual_up_prob"] = 1.0 - all_df["up_midpoint"]
    return all_df


def main():
    print("Loading data...")
    kalshi = load_kalshi()
    pm = load_pm()

    # Prepare Kalshi with outcomes
    ends = kalshi[kalshi["row_type"] == "round_end"][["round_ticker", "outcome"]].copy()
    ends = ends.drop_duplicates("round_ticker")
    ends = ends.rename(columns={"outcome": "round_outcome"})
    snaps = kalshi[kalshi["row_type"] == "snapshot"].copy()
    snaps = snaps.merge(ends, on="round_ticker", how="inner")
    snaps["outcome"] = snaps["round_outcome"]

    out = []

    # ==========================================================================
    # 1. "FREE MONEY" AT EXTREME PRICES
    # ==========================================================================
    out.append("=" * 70)
    out.append("1. 'FREE MONEY' — Buying contracts at $0.90+ near round end")
    out.append("=" * 70)
    out.append("\nQuestion: If YES contract is at $0.95 with 180s left, does YES win 95%+?\n"
               "If it wins MORE than the price implies, that's free money.\n"
               "We check: buy the FAVORED side at the ASK. What's the actual WR and EV?\n")

    # For each time bucket and price level, check actual win rate
    time_remaining_bins = [(0, 30), (30, 60), (60, 120), (120, 180), (180, 300), (300, 450)]
    price_levels = [0.90, 0.91, 0.92, 0.93, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99]

    # We bet on whichever side has yes_bid > 0.5 (the favored side)
    # Favored side's ask = yes_ask if yes_mid > 0.5, else no_ask
    for tr_lo, tr_hi in time_remaining_bins:
        out.append(f"\n### {tr_lo}-{tr_hi}s remaining\n")
        t_snaps = snaps[(snaps["seconds_remaining"] >= tr_lo) & (snaps["seconds_remaining"] < tr_hi)].copy()
        # Take one snapshot per round (first = closest to tr_hi)
        first = t_snaps.sort_values("seconds_remaining", ascending=False).groupby("round_ticker").first().reset_index()

        first["yes_mid"] = (first["yes_bid"] + first["yes_ask"]) / 2
        valid = first[(first["yes_bid"] > 0) & (first["yes_ask"] > 0) & (first["yes_ask"] < 1)].copy()

        # Determine favored side
        valid["favored_yes"] = valid["yes_mid"] > 0.5
        valid["favored_price"] = np.where(
            valid["favored_yes"],
            valid["yes_mid"],
            1 - valid["yes_mid"]
        )
        valid["entry_ask"] = np.where(
            valid["favored_yes"],
            valid["yes_ask"],
            np.where(valid["no_ask"] > 0, valid["no_ask"], 1 - valid["yes_bid"])
        )
        valid["won"] = np.where(
            valid["favored_yes"],
            valid["outcome"] == "yes",
            valid["outcome"] == "no"
        )

        for min_price in price_levels:
            sel = valid[valid["favored_price"] >= min_price]
            if len(sel) < 10:
                continue
            wr = sel["won"].mean()
            med_ask = sel["entry_ask"].median()
            fee = kalshi_fee(med_ask)
            ev = wr * (1 - med_ask - fee) - (1 - wr) * (med_ask + fee)
            # What does 1 loss cost?
            loss_cost = med_ask + fee
            win_profit = 1 - med_ask - fee
            # How many wins needed to cover 1 loss?
            wins_per_loss = loss_cost / win_profit if win_profit > 0 else float('inf')

            marker = "$$" if ev > 0.005 else ("✓" if ev > 0 else " ")
            out.append(f"  {marker} mid≥${min_price:.2f}: n={len(sel):4d}, "
                       f"WR={wr*100:.1f}%, ask=${med_ask:.3f}, fee=${fee:.4f}, "
                       f"EV=${ev:.4f}, "
                       f"1 loss = {wins_per_loss:.0f} wins to recover")

    # Per-coin breakdown for the best zone
    out.append("\n### Per-coin: 60-120s remaining, mid≥$0.95\n")
    t_snaps = snaps[(snaps["seconds_remaining"] >= 60) & (snaps["seconds_remaining"] < 120)].copy()
    first = t_snaps.sort_values("seconds_remaining", ascending=False).groupby(
        ["coin", "round_ticker"]).first().reset_index()
    first["yes_mid"] = (first["yes_bid"] + first["yes_ask"]) / 2
    valid = first[(first["yes_bid"] > 0) & (first["yes_ask"] < 1)].copy()
    valid["favored_yes"] = valid["yes_mid"] > 0.5
    valid["favored_price"] = np.where(valid["favored_yes"], valid["yes_mid"], 1 - valid["yes_mid"])
    valid["entry_ask"] = np.where(
        valid["favored_yes"], valid["yes_ask"],
        np.where(valid["no_ask"] > 0, valid["no_ask"], 1 - valid["yes_bid"])
    )
    valid["won"] = np.where(
        valid["favored_yes"],
        valid["outcome"] == "yes",
        valid["outcome"] == "no"
    )
    sel = valid[valid["favored_price"] >= 0.95]
    for coin in sorted(sel["coin"].unique()):
        c = sel[sel["coin"] == coin]
        if len(c) >= 5:
            wr = c["won"].mean()
            med_ask = c["entry_ask"].median()
            fee = kalshi_fee(med_ask)
            ev = wr * (1 - med_ask - fee) - (1 - wr) * (med_ask + fee)
            out.append(f"  {coin}: n={len(c)}, WR={wr*100:.1f}%, ask=${med_ask:.3f}, EV=${ev:.4f}")

    # ==========================================================================
    # 2. WHEN DO HIGH-PRICE CONTRACTS ACTUALLY LOSE?
    # ==========================================================================
    out.append("\n" + "=" * 70)
    out.append("2. ANATOMY OF LOSSES — When do $0.90+ contracts lose?")
    out.append("=" * 70)
    out.append("\nFor rounds where the favored side (mid>$0.90) LOST, what happened?\n")

    t_snaps = snaps[(snaps["seconds_remaining"] >= 30) & (snaps["seconds_remaining"] < 120)].copy()
    first = t_snaps.sort_values("seconds_remaining", ascending=False).groupby("round_ticker").first().reset_index()
    first["yes_mid"] = (first["yes_bid"] + first["yes_ask"]) / 2
    valid = first[(first["yes_bid"] > 0) & (first["yes_ask"] < 1)].copy()
    valid["favored_yes"] = valid["yes_mid"] > 0.5
    valid["favored_price"] = np.where(valid["favored_yes"], valid["yes_mid"], 1 - valid["yes_mid"])
    valid["won"] = np.where(valid["favored_yes"], valid["outcome"] == "yes", valid["outcome"] == "no")

    high_conf = valid[valid["favored_price"] >= 0.90]
    losses = high_conf[~high_conf["won"]]
    wins = high_conf[high_conf["won"]]

    out.append(f"Total high-conf trades (mid≥$0.90, 30-120s left): {len(high_conf)}")
    out.append(f"Wins: {len(wins)} ({len(wins)/len(high_conf)*100:.1f}%)")
    out.append(f"Losses: {len(losses)} ({len(losses)/len(high_conf)*100:.1f}%)\n")

    if len(losses) > 0:
        # What was the distance from strike for these losses?
        losses = losses.copy()
        losses["pct_dist"] = (losses["spot_price"] - losses["strike"]).abs() / losses["strike"]
        out.append("Losses breakdown:")
        out.append(f"  Median distance from strike: {losses['pct_dist'].median()*100:.3f}%")
        out.append(f"  Mean distance from strike: {losses['pct_dist'].mean()*100:.3f}%")
        out.append(f"  Coins: {losses['coin'].value_counts().to_dict()}")
        out.append(f"  Favored price distribution: "
                   f"${losses['favored_price'].min():.2f} - ${losses['favored_price'].max():.2f} "
                   f"(median ${losses['favored_price'].median():.2f})")

        # Show each loss
        out.append(f"\n  Individual losses (all {len(losses)}):")
        for _, row in losses.sort_values("favored_price", ascending=False).iterrows():
            pct_d = (row["spot_price"] - row["strike"]) / row["strike"] * 100
            direction = "above" if row["spot_price"] > row["strike"] else "below"
            out.append(f"    {row['coin']} {row['round_ticker']}: mid=${row['favored_price']:.2f}, "
                       f"spot {pct_d:+.3f}% {direction} strike, "
                       f"secs_left={row['seconds_remaining']:.0f}")

    # ==========================================================================
    # 3. POLYMARKET CORRECTED ANALYSIS
    # ==========================================================================
    out.append("\n" + "=" * 70)
    out.append("3. PM CORRECTED ANALYSIS (inverted midpoint: actual_up_prob = 1 - up_midpoint)")
    out.append("=" * 70)

    pm_ends = pm[pm["row_type"].str.contains("end|resolved", case=False, na=False)].copy()
    pm_snaps = pm[pm["row_type"] == "snapshot"].copy()

    for dur in ["5m", "15m"]:
        out.append(f"\n### PM {dur} — Corrected Calibration\n")
        dur_ends = pm_ends[pm_ends["file_duration"] == dur][["slug", "outcome"]].drop_duplicates("slug")
        dur_ends = dur_ends.rename(columns={"outcome": "round_outcome"})
        dur_snaps = pm_snaps[pm_snaps["file_duration"] == dur].copy()
        merged = dur_snaps.merge(dur_ends, on="slug", how="inner")
        known = merged[merged["round_outcome"].isin(["up", "down"])].copy()

        if len(known) < 100:
            out.append(f"Only {len(known)} rows — skipping")
            continue

        known["actual_up"] = (known["round_outcome"] == "up").astype(float)

        # Mid-round calibration using CORRECTED midpoint
        if dur == "15m":
            mid_time = known[(known["seconds_remaining"] >= 400) & (known["seconds_remaining"] <= 600)]
        else:
            mid_time = known[(known["seconds_remaining"] >= 120) & (known["seconds_remaining"] <= 200)]

        first_per = mid_time.sort_values("seconds_remaining").groupby("slug").first().reset_index()

        if len(first_per) > 20:
            quoted = first_per[(first_per["up_bid"] > 0.05) & (first_per["up_ask"] < 0.95)].copy()
            if len(quoted) > 20:
                bins = [0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
                quoted["prob_bin"] = pd.cut(quoted["actual_up_prob"], bins=bins)
                cal = quoted.groupby("prob_bin").agg(
                    n=("actual_up", "count"),
                    implied=("actual_up_prob", "mean"),
                    actual=("actual_up", "mean"),
                ).dropna()
                cal["miscal"] = cal["actual"] - cal["implied"]
                out.append(cal.to_markdown())
                out.append(f"\nCorrected midpoint predicts outcome: "
                           f"{((quoted['actual_up_prob'] > 0.5) == (quoted['actual_up'] > 0.5)).mean()*100:.1f}% "
                           f"(n={len(quoted)})")

    # ==========================================================================
    # 4. PM TRADE FLOW AS SIGNAL
    # ==========================================================================
    out.append("\n" + "=" * 70)
    out.append("4. PM TRADE FLOW — Can PM trades predict Kalshi outcomes?")
    out.append("=" * 70)
    out.append("\nPM 15m and Kalshi 15m overlap in time. PM resolves on Binance, Kalshi on CF Benchmarks.\n"
               "If a PM whale trade pushes the price, does that predict the Kalshi outcome?\n")

    # Check: PM last_trade_price direction as signal
    # Note: last_trade_price is on the DOWN token (since up_token is actually down)
    # So high last_trade_price = down is winning = price going down
    pm_15m_ends = pm_ends[(pm_ends["file_duration"] == "15m") & (pm_ends["outcome"].isin(["up", "down"]))].copy()
    pm_15m_snaps = pm_snaps[pm_snaps["file_duration"] == "15m"].copy()

    if len(pm_15m_ends) > 10:
        pm_15m_ends_u = pm_15m_ends[["slug", "outcome"]].drop_duplicates("slug").rename(
            columns={"outcome": "round_outcome"})
        pm_merged = pm_15m_snaps.merge(pm_15m_ends_u, on="slug", how="inner")

        # At various times, does the corrected PM midpoint predict outcome?
        for tr_lo, tr_hi in [(600, 900), (400, 600), (200, 400), (60, 200)]:
            sel = pm_merged[(pm_merged["seconds_remaining"] >= tr_lo) & (pm_merged["seconds_remaining"] < tr_hi)]
            first = sel.sort_values("seconds_remaining", ascending=False).groupby("slug").first().reset_index()
            quoted = first[(first["up_bid"] > 0.05) & (first["up_ask"] < 0.95)]
            if len(quoted) < 20:
                continue
            quoted = quoted.copy()
            quoted["pm_says_up"] = quoted["actual_up_prob"] > 0.5
            quoted["actual_up"] = quoted["round_outcome"] == "up"
            acc = (quoted["pm_says_up"] == quoted["actual_up"]).mean()
            out.append(f"  PM 15m {tr_lo}-{tr_hi}s remaining: midpoint predicts outcome {acc*100:.1f}% "
                       f"(n={len(quoted)})")

        # Now the key question: can PM 15m price predict KALSHI outcome?
        out.append("\n### PM price → Kalshi outcome\n")
        kalshi_ends = kalshi[kalshi["row_type"] == "round_end"].copy()
        kalshi_ends["round_time"] = kalshi_ends["timestamp"].dt.floor("15min")

        # For each Kalshi round, find the corresponding PM round
        # PM end_date should match or be close to Kalshi round close time
        pm_15m_snaps_w_outcome = pm_merged.copy()
        pm_15m_snaps_w_outcome["end_dt"] = pd.to_datetime(
            pm_15m_snaps_w_outcome["end_date"], utc=True)
        pm_15m_snaps_w_outcome["round_time"] = pm_15m_snaps_w_outcome["end_dt"].dt.floor("15min")

        # Get PM midpoint at mid-round (300-600s remaining)
        pm_mid = pm_15m_snaps_w_outcome[
            (pm_15m_snaps_w_outcome["seconds_remaining"] >= 300) &
            (pm_15m_snaps_w_outcome["seconds_remaining"] <= 600) &
            (pm_15m_snaps_w_outcome["up_bid"] > 0.05) &
            (pm_15m_snaps_w_outcome["up_ask"] < 0.95)
        ]
        pm_first = pm_mid.sort_values("seconds_remaining").groupby(
            ["coin", "round_time"]).first().reset_index()
        pm_first["pm_says_up"] = pm_first["actual_up_prob"] > 0.5
        pm_first["pm_confidence"] = (pm_first["actual_up_prob"] - 0.5).abs()
        pm_signal = pm_first[["coin", "round_time", "pm_says_up", "pm_confidence", "actual_up_prob"]].copy()
        pm_signal["coin"] = pm_signal["coin"].str.upper()

        # Merge with Kalshi outcomes
        k_ends = kalshi_ends[["coin", "round_time", "outcome"]].rename(columns={"outcome": "kalshi_outcome"})
        cross = pm_signal.merge(k_ends, on=["coin", "round_time"], how="inner")

        if len(cross) > 20:
            cross["kalshi_yes"] = cross["kalshi_outcome"] == "yes"
            cross["pm_correct"] = cross["pm_says_up"] == cross["kalshi_yes"]
            overall = cross["pm_correct"].mean()
            out.append(f"PM mid-round price predicts Kalshi outcome: {overall*100:.1f}% (n={len(cross)})")

            # By confidence level
            for lo, hi in [(0, 0.05), (0.05, 0.15), (0.15, 0.25), (0.25, 0.50)]:
                sel = cross[(cross["pm_confidence"] >= lo) & (cross["pm_confidence"] < hi)]
                if len(sel) >= 10:
                    acc = sel["pm_correct"].mean()
                    out.append(f"  PM confidence {lo:.2f}-{hi:.2f}: {acc*100:.1f}% predicts Kalshi, n={len(sel)}")

            # The interesting question: when PM says UP but Kalshi book says NO,
            # which one is right?
            # We'd need to merge with Kalshi mid-round snapshots too
            out.append("\n### PM vs Kalshi disagreement")

            k_snaps = snaps[(snaps["seconds_remaining"] >= 300) & (snaps["seconds_remaining"] <= 600)].copy()
            k_first = k_snaps.sort_values("seconds_remaining").groupby(
                ["coin", "round_ticker"]).first().reset_index()
            k_first["yes_mid"] = (k_first["yes_bid"] + k_first["yes_ask"]) / 2
            k_valid = k_first[(k_first["yes_bid"] > 0) & (k_first["yes_ask"] < 1)].copy()
            k_valid["k_says_yes"] = k_valid["yes_mid"] > 0.5
            k_valid["round_time"] = k_valid["timestamp"].dt.floor("15min")

            k_signal = k_valid[["coin", "round_time", "k_says_yes", "yes_mid", "outcome"]].copy()

            cross2 = pm_signal.merge(k_signal, on=["coin", "round_time"], how="inner")

            if len(cross2) > 20:
                cross2["agree"] = cross2["pm_says_up"] == cross2["k_says_yes"]
                cross2["actual_yes"] = cross2["outcome"] == "yes"

                agree = cross2[cross2["agree"]]
                disagree = cross2[~cross2["agree"]]

                if len(agree) > 5:
                    acc = (agree["pm_says_up"] == agree["actual_yes"]).mean()
                    out.append(f"  When PM and Kalshi AGREE: {acc*100:.1f}% correct (n={len(agree)})")

                if len(disagree) > 5:
                    pm_right = (disagree["pm_says_up"] == disagree["actual_yes"]).mean()
                    k_right = (disagree["k_says_yes"] == disagree["actual_yes"]).mean()
                    out.append(f"  When they DISAGREE (n={len(disagree)}):")
                    out.append(f"    PM is right: {pm_right*100:.1f}%")
                    out.append(f"    Kalshi is right: {k_right*100:.1f}%")

                    # This is the key: if one platform is consistently right,
                    # we can trade on the other platform's mispricing
                    if len(disagree) >= 10:
                        # What Kalshi price could we enter at?
                        disagree_pm_right = disagree[disagree["pm_says_up"] == disagree["actual_yes"]]
                        if len(disagree_pm_right) > 0:
                            out.append(f"\n    When PM is right and Kalshi disagrees:")
                            out.append(f"      Cases: {len(disagree_pm_right)}")
                            out.append(f"      Kalshi yes_mid in these cases: "
                                       f"${disagree_pm_right['yes_mid'].median():.3f} median")
                            # We'd be buying a contract at ~$0.40-0.50 and winning
                            # That's HUGE EV if it's real
        else:
            out.append(f"Only {len(cross)} matched cross-platform rounds")

    # ==========================================================================
    # 5. PM VOLUME SPIKES AS SIGNAL
    # ==========================================================================
    out.append("\n" + "=" * 70)
    out.append("5. PM VOLUME SPIKES — Do big PM trades predict outcomes?")
    out.append("=" * 70)

    if len(pm_15m_snaps_w_outcome) > 0:
        # Look at volume changes — a spike in volume = whale trade
        pm_vol = pm_15m_snaps_w_outcome.sort_values(["slug", "seconds_remaining"], ascending=[True, False])

        # Compute volume delta (change between consecutive snapshots)
        pm_vol["vol_delta"] = pm_vol.groupby("slug")["volume"].diff()
        pm_vol = pm_vol.dropna(subset=["vol_delta"])

        # Get max volume spike per round
        vol_spikes = pm_vol.groupby("slug").agg(
            max_vol_delta=("vol_delta", "max"),
            total_vol=("volume", "max"),
            outcome=("round_outcome", "first"),
        ).dropna()

        # Does having a big volume spike predict outcome?
        vol_spikes["big_spike"] = vol_spikes["max_vol_delta"] > vol_spikes["max_vol_delta"].quantile(0.75)
        vol_spikes["actual_up"] = vol_spikes["outcome"] == "up"

        out.append(f"\nPM 15m rounds analyzed: {len(vol_spikes)}")
        out.append(f"Median max volume spike per round: ${vol_spikes['max_vol_delta'].median():.0f}")
        out.append(f"Top quartile spike threshold: ${vol_spikes['max_vol_delta'].quantile(0.75):.0f}")

        big = vol_spikes[vol_spikes["big_spike"]]
        small = vol_spikes[~vol_spikes["big_spike"]]
        out.append(f"\nBig spike rounds: {(big['actual_up']).mean()*100:.1f}% up (n={len(big)})")
        out.append(f"Normal rounds: {(small['actual_up']).mean()*100:.1f}% up (n={len(small)})")
        out.append("(If both ~50%, volume spikes don't predict direction)")

        # More useful: does a spike in the UP direction (price increase) predict up?
        # The last_trade_price on DOWN token going DOWN means UP is winning
        # This is complex with inverted tokens - let's look at price change + volume
        out.append("\n### Volume-weighted price direction\n")

        # For each round, compute: did the corrected midpoint move in the direction
        # of the eventual outcome during high-volume periods?
        for slug in vol_spikes.index[:5]:  # just show examples
            r = pm_vol[pm_vol["slug"] == slug].sort_values("seconds_remaining", ascending=False)
            if len(r) < 10:
                continue
            outcome = vol_spikes.loc[slug, "outcome"]
            max_spike = vol_spikes.loc[slug, "max_vol_delta"]
            spike_row = r[r["vol_delta"] == r["vol_delta"].max()].iloc[0]
            out.append(f"  {slug}: outcome={outcome}, max_spike=${max_spike:.0f} "
                       f"at {spike_row['seconds_remaining']:.0f}s remaining, "
                       f"total_vol=${vol_spikes.loc[slug, 'total_vol']:.0f}")

    # ==========================================================================
    # 6. SPREAD CAPTURE / MARKET MAKING
    # ==========================================================================
    out.append("\n" + "=" * 70)
    out.append("6. SPREAD CAPTURE — Can we profit by providing liquidity?")
    out.append("=" * 70)
    out.append("\nInstead of directional betting, what if we quote both sides near the midpoint?\n"
               "Risk: getting picked off when spot moves. Reward: spread capture.\n")

    # For each round, measure: if we quoted yes_bid+0.01 and no_bid+0.01,
    # how often would we get filled on both sides vs getting stuck on one?
    # Simpler: what's the average spread, and how volatile is the midpoint?

    for coin in ["BTC", "ETH"]:
        out.append(f"\n### {coin}\n")
        c_snaps = snaps[(snaps["coin"] == coin) &
                        (snaps["seconds_remaining"] >= 60) &
                        (snaps["seconds_remaining"] <= 600)].copy()
        c_snaps["yes_mid"] = (c_snaps["yes_bid"] + c_snaps["yes_ask"]) / 2
        c_snaps["yes_spread"] = c_snaps["yes_ask"] - c_snaps["yes_bid"]
        quoted = c_snaps[(c_snaps["yes_bid"] > 0.05) & (c_snaps["yes_ask"] < 0.95)]

        if len(quoted) < 100:
            continue

        out.append(f"  Median spread: ${quoted['yes_spread'].median():.3f}")
        out.append(f"  Mean spread: ${quoted['yes_spread'].mean():.3f}")

        # How much does the midpoint move per snapshot (~1 second)?
        quoted = quoted.sort_values(["round_ticker", "seconds_remaining"], ascending=[True, False])
        quoted["mid_change"] = quoted.groupby("round_ticker")["yes_mid"].diff().abs()
        mid_changes = quoted["mid_change"].dropna()

        out.append(f"  Median per-second midpoint change: ${mid_changes.median():.4f}")
        out.append(f"  Mean per-second midpoint change: ${mid_changes.mean():.4f}")
        out.append(f"  95th percentile change: ${mid_changes.quantile(0.95):.4f}")

        # If spread is $0.01 (BTC) and mid changes by $0.002/sec on median,
        # that means ~5 seconds before your quote is stale
        half_spread = quoted['yes_spread'].median() / 2
        safe_time = half_spread / mid_changes.median() if mid_changes.median() > 0 else float('inf')
        out.append(f"  Half-spread / median change = {safe_time:.1f} seconds before adverse selection")

        fee = kalshi_fee(0.50)  # worst case fee at 50/50
        out.append(f"  Fee at $0.50 (worst): ${fee:.4f}")
        out.append(f"  Net spread after 2x fee: ${quoted['yes_spread'].median() - 2*fee:.4f}")
        if quoted['yes_spread'].median() > 2 * fee:
            out.append(f"  ✓ Spread exceeds 2x fee — MM potentially viable (ignoring adverse selection)")
        else:
            out.append(f"  ✗ Spread < 2x fee — MM not viable for {coin}")

    # ==========================================================================
    # 7. KALSHI: LATE-ROUND BID STRATEGY (THE FLIP SIDE)
    # ==========================================================================
    out.append("\n" + "=" * 70)
    out.append("7. CONTRARIAN: Buy the UNDERDOG at extreme times")
    out.append("=" * 70)
    out.append("\nIf contract is at $0.05 (5%) with 120s left, it wins 5% of the time.\n"
               "But if it wins even 6-7%, that's HUGE EV because you risk $0.05 to win $0.95.\n"
               "The payoff asymmetry means even a tiny miscalibration is exploitable.\n")

    for tr_lo, tr_hi in [(30, 60), (60, 120), (120, 180), (180, 300)]:
        out.append(f"\n### {tr_lo}-{tr_hi}s remaining\n")
        t_snaps = snaps[(snaps["seconds_remaining"] >= tr_lo) & (snaps["seconds_remaining"] < tr_hi)]
        first = t_snaps.sort_values("seconds_remaining", ascending=False).groupby("round_ticker").first().reset_index()
        first["yes_mid"] = (first["yes_bid"] + first["yes_ask"]) / 2
        valid = first[(first["yes_bid"] > 0) & (first["yes_ask"] < 1)].copy()

        # Underdog = the side with mid < 0.50
        valid["underdog_yes"] = valid["yes_mid"] < 0.5
        valid["underdog_price"] = np.where(valid["underdog_yes"], valid["yes_mid"], 1 - valid["yes_mid"])
        valid["underdog_ask"] = np.where(
            valid["underdog_yes"], valid["yes_ask"],
            np.where(valid["no_ask"] > 0, valid["no_ask"], 1 - valid["yes_bid"])
        )
        valid["underdog_won"] = np.where(
            valid["underdog_yes"],
            valid["outcome"] == "yes",
            valid["outcome"] == "no"
        )

        for max_price in [0.02, 0.03, 0.05, 0.08, 0.10, 0.15]:
            sel = valid[valid["underdog_price"] <= max_price]
            if len(sel) < 10:
                continue
            wr = sel["underdog_won"].mean()
            med_ask = sel["underdog_ask"].median()
            if med_ask <= 0:
                continue
            fee = kalshi_fee(med_ask)
            ev = wr * (1 - med_ask - fee) - (1 - wr) * (med_ask + fee)
            # Payout ratio
            payout = (1 - med_ask - fee) / (med_ask + fee) if (med_ask + fee) > 0 else 0
            implied = med_ask
            edge = wr - implied

            marker = "✓" if ev > 0 else " "
            out.append(f"  {marker} underdog≤${max_price:.2f}: n={len(sel)}, "
                       f"WR={wr*100:.1f}% (implied={implied*100:.0f}%), "
                       f"edge={edge*100:+.1f}%, "
                       f"ask=${med_ask:.3f}, payout {payout:.0f}:1, "
                       f"EV=${ev:.4f}")

    # Print and save
    text = "\n".join(out)
    print(text)

    out_file = PROJECT / "research" / "v3-creative-analysis.md"
    out_file.write_text(f"# V3 Creative Analysis\n\n{text}\n")
    print(f"\nWritten to {out_file}")


if __name__ == "__main__":
    main()
