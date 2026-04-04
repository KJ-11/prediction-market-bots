"""Deep cascade analysis: slot timing, YES-only edge, two-slot pricing, maker fees.

Key findings from cascade validation:
- Slot 1 signal is 72-75% on Kalshi (strong)
- YES side is consistently +EV, NO side is negative
- Slots 1+2 agree = 92-95% accuracy on Kalshi
- Need: what's the Kalshi pricing when we'd actually enter?
"""
from __future__ import annotations

import glob
import math
from datetime import timedelta

import numpy as np
import pandas as pd

DATA_PM = "data/rounds/polymarket"
DATA_KX = "data/rounds"
COINS = ["BTC", "ETH", "SOL"]
RNG = np.random.default_rng(42)
N_BOOTSTRAP = 1_000


def kalshi_fee_taker(price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return math.ceil(0.07 * price * (1 - price) * 100) / 100


def kalshi_fee_maker(price: float) -> float:
    return kalshi_fee_taker(price) * 0.25


def bootstrap_ci(arr: np.ndarray, n: int = N_BOOTSTRAP, ci: float = 0.95):
    if len(arr) < 2:
        return float("nan"), float("nan"), float("nan")
    means = np.array([arr[RNG.integers(0, len(arr), size=len(arr))].mean() for _ in range(n)])
    alpha = (1 - ci) / 2
    lo, hi = np.quantile(means, [alpha, 1 - alpha])
    return float(arr.mean()), float(lo), float(hi)


def load_pm_rounds(coin: str, duration: str) -> pd.DataFrame:
    pattern = f"{DATA_PM}/{coin}-{duration}-*.csv"
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_csv(f, on_bad_lines="skip") for f in files]
    df = pd.concat(frames, ignore_index=True)
    df["end_date"] = pd.to_datetime(df["end_date"], utc=True)
    for col in ["up_bid", "up_ask", "down_bid", "down_ask", "seconds_remaining", "spot_price"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_kalshi(coin: str) -> pd.DataFrame:
    prefix = f"KX{coin}15M"
    pattern = f"{DATA_KX}/{prefix}-*.csv"
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_csv(f, on_bad_lines="skip") for f in files]
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
    for col in ["yes_bid", "yes_ask", "no_bid", "no_ask", "seconds_remaining", "spot_price"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def pm_round_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["slug", "end_date", "outcome"])
    round_ends = df[df["row_type"] == "round_end"].drop_duplicates(subset=["slug"], keep="last")
    outcomes = round_ends[["slug", "end_date", "outcome"]].copy()
    return outcomes[outcomes["outcome"].isin(["up", "down"])]


def kalshi_round_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["round_ticker", "end_time", "outcome"])
    round_ends = df[df["row_type"] == "round_end"].drop_duplicates(subset=["round_ticker"], keep="last")
    outcomes = round_ends[["round_ticker", "timestamp", "outcome"]].copy()
    outcomes = outcomes[outcomes["outcome"].isin(["yes", "no"])]
    return outcomes.rename(columns={"timestamp": "end_time"})


def align_5m_to_kalshi(outcomes_5m, kalshi_outcomes):
    if outcomes_5m.empty or kalshi_outcomes.empty:
        return pd.DataFrame()

    lookup_kx = {}
    for _, r in kalshi_outcomes.iterrows():
        end_time = r["end_time"]
        if isinstance(end_time, str):
            end_time = pd.to_datetime(end_time, utc=True)
        end_rounded = end_time.replace(second=0, microsecond=0)
        if end_time.second >= 30:
            end_rounded += timedelta(minutes=1)
        lookup_kx[end_rounded] = {"outcome": r["outcome"], "ticker": r["round_ticker"]}

    results = []
    for _, r5 in outcomes_5m.iterrows():
        end_5m = r5["end_date"]
        if isinstance(end_5m, str):
            end_5m = pd.to_datetime(end_5m, utc=True)
        for slot, offset in [(3, timedelta(0)), (2, timedelta(minutes=5)), (1, timedelta(minutes=10))]:
            candidate_end = end_5m + offset
            if candidate_end in lookup_kx:
                kx = lookup_kx[candidate_end]
                results.append({
                    "slug_5m": r5["slug"],
                    "end_5m": end_5m,
                    "outcome_5m": r5["outcome"],
                    "end_kx": candidate_end,
                    "outcome_kx": kx["outcome"],
                    "ticker_kx": kx["ticker"],
                    "slot": slot,
                })
                break

    return pd.DataFrame(results)


def get_kalshi_pricing_at_sr(kalshi_df, tickers, target_sr, window=20):
    """Get Kalshi pricing at a specific seconds_remaining window."""
    results = []
    for ticker in tickers:
        round_data = kalshi_df[kalshi_df["round_ticker"] == ticker]
        if round_data.empty:
            continue
        near = round_data[
            (round_data["seconds_remaining"] >= target_sr - window) &
            (round_data["seconds_remaining"] <= target_sr + window)
        ]
        if near.empty:
            continue
        idx = (near["seconds_remaining"] - target_sr).abs().idxmin()
        row = near.loc[idx]
        results.append({
            "ticker_kx": ticker,
            "yes_ask": row.get("yes_ask"),
            "yes_bid": row.get("yes_bid"),
            "sec_rem": row["seconds_remaining"],
        })
    return pd.DataFrame(results)


def main():
    print("=" * 80)
    print("CASCADE DEEP ANALYSIS")
    print("YES-only edge, two-slot pricing, maker vs taker")
    print("=" * 80)

    for coin in COINS:
        print(f"\n{'#' * 80}")
        print(f"# {coin}")
        print(f"{'#' * 80}")

        pm5m_df = load_pm_rounds(coin, "5m")
        kx_df = load_kalshi(coin)
        pm5m_outcomes = pm_round_outcomes(pm5m_df)
        kx_outcomes = kalshi_round_outcomes(kx_df)

        print(f"  PM 5m outcomes: {len(pm5m_outcomes)}")
        print(f"  Kalshi outcomes: {len(kx_outcomes)}")

        aligned = align_5m_to_kalshi(pm5m_outcomes, kx_outcomes)
        if aligned.empty:
            print("  No aligned pairs.")
            continue

        # ── SECTION 1: YES-only slot 1 with various entry timings ──
        print(f"\n  {'=' * 70}")
        print(f"  SLOT 1: YES-ONLY (5m=up → buy YES on Kalshi)")
        print(f"  {'=' * 70}")

        slot1 = aligned[aligned["slot"] == 1].copy()
        slot1_up = slot1[slot1["outcome_5m"] == "up"].copy()

        # Test entry at different seconds_remaining
        # Slot 1 resolves 10 min before 15m end → ~600s remaining on Kalshi
        # But PM resolution has some delay, so realistic entry is ~550-580s
        for target_sr in [600, 550, 500, 450, 400, 300]:
            pricing = get_kalshi_pricing_at_sr(kx_df, slot1_up["ticker_kx"].unique(), target_sr)
            if pricing.empty:
                continue
            merged = slot1_up.merge(pricing, on="ticker_kx", how="inner")
            merged["entry"] = pd.to_numeric(merged["yes_ask"], errors="coerce")
            merged = merged.dropna(subset=["entry"])
            merged = merged[(merged["entry"] > 0.05) & (merged["entry"] < 0.95)]

            if len(merged) < 10:
                continue

            merged["win"] = (merged["outcome_kx"] == "yes").astype(int)
            merged["fee_taker"] = merged["entry"].apply(kalshi_fee_taker)
            merged["fee_maker"] = merged["entry"].apply(kalshi_fee_maker)
            merged["pnl_taker"] = merged.apply(
                lambda r: (1 - r["entry"] - r["fee_taker"]) if r["win"] else (-r["entry"] - r["fee_taker"]), axis=1)
            merged["pnl_maker"] = merged.apply(
                lambda r: (1 - r["entry"] - r["fee_maker"]) if r["win"] else (-r["entry"] - r["fee_maker"]), axis=1)

            wr = merged["win"].mean()
            avg_entry = merged["entry"].mean()
            _, lo_t, hi_t = bootstrap_ci(merged["pnl_taker"].values)
            _, lo_m, hi_m = bootstrap_ci(merged["pnl_maker"].values)

            print(f"\n    Entry at SR={target_sr}  (n={len(merged)})")
            print(f"      Win rate: {wr:.1%}  |  Avg entry: ${avg_entry:.3f}")
            print(f"      Taker EV: ${merged['pnl_taker'].mean():+.4f}  [{lo_t:+.4f}, {hi_t:+.4f}]")
            print(f"      Maker EV: ${merged['pnl_maker'].mean():+.4f}  [{lo_m:+.4f}, {hi_m:+.4f}]")

        # ── SECTION 2: Two-slot cascade (slots 1+2 agree "up") ──
        print(f"\n  {'=' * 70}")
        print(f"  TWO-SLOT CASCADE: Slots 1+2 both 'up' → buy YES on Kalshi")
        print(f"  {'=' * 70}")

        # Group by Kalshi round, check if slots 1 and 2 both resolved "up"
        two_slot_yes = []
        for end_kx, group in aligned.groupby("end_kx"):
            slot_outcomes = {row["slot"]: row["outcome_5m"] for _, row in group.iterrows()}
            if 1 in slot_outcomes and 2 in slot_outcomes:
                if slot_outcomes[1] == "up" and slot_outcomes[2] == "up":
                    ticker = group["ticker_kx"].iloc[0]
                    outcome_kx = group["outcome_kx"].iloc[0]
                    two_slot_yes.append({
                        "ticker_kx": ticker,
                        "end_kx": end_kx,
                        "outcome_kx": outcome_kx,
                    })

        ts_df = pd.DataFrame(two_slot_yes)
        if ts_df.empty:
            print("    No two-slot 'up' cases.")
        else:
            print(f"    Total two-slot UP cases: {len(ts_df)}")
            ts_df["win"] = (ts_df["outcome_kx"] == "yes").astype(int)
            wr = ts_df["win"].mean()
            print(f"    Win rate: {wr:.1%}")

            # Pricing at ~300s remaining (after slot 2 resolves, ~5 min left)
            for target_sr in [300, 250, 200]:
                pricing = get_kalshi_pricing_at_sr(kx_df, ts_df["ticker_kx"].unique(), target_sr)
                if pricing.empty:
                    continue
                merged = ts_df.merge(pricing, on="ticker_kx", how="inner")
                merged["entry"] = pd.to_numeric(merged["yes_ask"], errors="coerce")
                merged = merged.dropna(subset=["entry"])
                merged = merged[(merged["entry"] > 0.05) & (merged["entry"] < 0.95)]

                if len(merged) < 5:
                    print(f"    SR={target_sr}: only {len(merged)} trades with valid pricing")
                    continue

                merged["fee_taker"] = merged["entry"].apply(kalshi_fee_taker)
                merged["fee_maker"] = merged["entry"].apply(kalshi_fee_maker)
                merged["pnl_taker"] = merged.apply(
                    lambda r: (1 - r["entry"] - r["fee_taker"]) if r["win"] else (-r["entry"] - r["fee_taker"]), axis=1)
                merged["pnl_maker"] = merged.apply(
                    lambda r: (1 - r["entry"] - r["fee_maker"]) if r["win"] else (-r["entry"] - r["fee_maker"]), axis=1)

                avg_entry = merged["entry"].mean()
                wr_exec = merged["win"].mean()
                _, lo_t, hi_t = bootstrap_ci(merged["pnl_taker"].values)
                _, lo_m, hi_m = bootstrap_ci(merged["pnl_maker"].values)

                print(f"\n    Entry at SR={target_sr}  (n={len(merged)})")
                print(f"      Win rate: {wr_exec:.1%}  |  Avg entry: ${avg_entry:.3f}")
                print(f"      Taker EV: ${merged['pnl_taker'].mean():+.4f}  [{lo_t:+.4f}, {hi_t:+.4f}]")
                print(f"      Maker EV: ${merged['pnl_maker'].mean():+.4f}  [{lo_m:+.4f}, {hi_m:+.4f}]")

        # ── SECTION 3: Both-direction slot 1 with maker fees ──
        print(f"\n  {'=' * 70}")
        print(f"  SLOT 1: BOTH DIRECTIONS with Maker Fees")
        print(f"  {'=' * 70}")

        for direction, signal, kx_outcome in [("5m=up → YES", "up", "yes"), ("5m=down → NO", "down", "no")]:
            subset = slot1[slot1["outcome_5m"] == signal].copy()
            pricing = get_kalshi_pricing_at_sr(kx_df, subset["ticker_kx"].unique(), 550)
            if pricing.empty:
                continue
            merged = subset.merge(pricing, on="ticker_kx", how="inner")

            if kx_outcome == "yes":
                merged["entry"] = pd.to_numeric(merged["yes_ask"], errors="coerce")
            else:
                merged["entry"] = 1 - pd.to_numeric(merged["yes_bid"], errors="coerce")

            merged = merged.dropna(subset=["entry"])
            merged = merged[(merged["entry"] > 0.05) & (merged["entry"] < 0.95)]

            if len(merged) < 10:
                continue

            merged["win"] = (merged["outcome_kx"] == kx_outcome).astype(int)
            merged["fee_taker"] = merged["entry"].apply(kalshi_fee_taker)
            merged["fee_maker"] = merged["entry"].apply(kalshi_fee_maker)
            merged["pnl_taker"] = merged.apply(
                lambda r: (1 - r["entry"] - r["fee_taker"]) if r["win"] else (-r["entry"] - r["fee_taker"]), axis=1)
            merged["pnl_maker"] = merged.apply(
                lambda r: (1 - r["entry"] - r["fee_maker"]) if r["win"] else (-r["entry"] - r["fee_maker"]), axis=1)

            wr = merged["win"].mean()
            avg_entry = merged["entry"].mean()
            _, lo_t, hi_t = bootstrap_ci(merged["pnl_taker"].values)
            _, lo_m, hi_m = bootstrap_ci(merged["pnl_maker"].values)

            print(f"\n    {direction}  at SR=550  (n={len(merged)})")
            print(f"      Win rate: {wr:.1%}  |  Avg entry: ${avg_entry:.3f}")
            print(f"      Taker EV: ${merged['pnl_taker'].mean():+.4f}  [{lo_t:+.4f}, {hi_t:+.4f}]")
            print(f"      Maker EV: ${merged['pnl_maker'].mean():+.4f}  [{lo_m:+.4f}, {hi_m:+.4f}]")

    print(f"\n{'=' * 80}")
    print("DONE")
    print("=" * 80)


if __name__ == "__main__":
    main()
