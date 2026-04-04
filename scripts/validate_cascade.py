"""
Cascade Signal Validation: Do PM 5m outcomes predict overlapping 15m outcomes?

Tests whether resolved PM 5-minute rounds provide a "free signal" for the
containing PM 15-minute and Kalshi 15-minute rounds that are still open.
"""
from __future__ import annotations

import glob
import math
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

# ── Config ──────────────────────────────────────────────────────────────────

DATA_PM = "data/rounds/polymarket"
DATA_KX = "data/rounds"
COINS = ["BTC", "ETH", "SOL"]
RNG = np.random.default_rng(42)
N_BOOTSTRAP = 1_000


# ── Fee models ──────────────────────────────────────────────────────────────

def pm_fee(price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return price * 0.25 * (price * (1 - price)) ** 2


def kalshi_fee(price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return math.ceil(0.07 * price * (1 - price) * 100) / 100


# ── Bootstrap CI ────────────────────────────────────────────────────────────

def bootstrap_ci(arr: np.ndarray, n: int = N_BOOTSTRAP, ci: float = 0.95) -> tuple[float, float, float]:
    """Return (mean, ci_lo, ci_hi) via bootstrap."""
    if len(arr) == 0:
        return (np.nan, np.nan, np.nan)
    means = np.array([arr[RNG.integers(0, len(arr), size=len(arr))].mean() for _ in range(n)])
    alpha = (1 - ci) / 2
    lo, hi = np.quantile(means, [alpha, 1 - alpha])
    return float(arr.mean()), float(lo), float(hi)


# ── Data loading ────────────────────────────────────────────────────────────

def load_pm_rounds(coin: str, duration: str) -> pd.DataFrame:
    """Load PM round data, return one row per round with outcome."""
    pattern = f"{DATA_PM}/{coin}-{duration}-*.csv"
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame()

    frames = []
    for f in files:
        df = pd.read_csv(f, on_bad_lines="skip")
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)
    df["end_date"] = pd.to_datetime(df["end_date"], utc=True)

    # Numeric columns
    for col in ["up_bid", "up_ask", "down_bid", "down_ask", "seconds_remaining", "spot_price"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def load_kalshi_rounds(coin: str) -> pd.DataFrame:
    """Load Kalshi 15m round data."""
    prefix = f"KX{coin}15M"
    pattern = f"{DATA_KX}/{prefix}-*.csv"
    files = sorted(glob.glob(pattern))
    if not files:
        return pd.DataFrame()

    frames = []
    for f in files:
        df = pd.read_csv(f, on_bad_lines="skip")
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)

    for col in ["yes_bid", "yes_ask", "no_bid", "no_ask", "seconds_remaining", "spot_price"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ── Extract round outcomes ──────────────────────────────────────────────────

def pm_round_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """One row per PM round: slug, end_date, outcome."""
    if df.empty:
        return pd.DataFrame(columns=["slug", "end_date", "outcome"])

    # Take the last row per slug to get the final outcome
    last_rows = df.sort_values("seconds_remaining").groupby("slug").first().reset_index()
    # Also check round_end rows
    round_ends = df[df["row_type"] == "round_end"].drop_duplicates(subset=["slug"], keep="last")

    # Prefer round_end rows for outcome
    outcomes = round_ends[["slug", "end_date", "outcome"]].copy()
    # Filter to known outcomes only
    outcomes = outcomes[outcomes["outcome"].isin(["up", "down"])].copy()
    return outcomes


def kalshi_round_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """One row per Kalshi round: round_ticker, end_time (from ticker), outcome."""
    if df.empty:
        return pd.DataFrame(columns=["round_ticker", "end_time", "outcome"])

    round_ends = df[df["row_type"] == "round_end"].drop_duplicates(subset=["round_ticker"], keep="last")
    outcomes = round_ends[["round_ticker", "timestamp", "outcome"]].copy()
    outcomes = outcomes[outcomes["outcome"].isin(["yes", "no"])].copy()
    outcomes = outcomes.rename(columns={"timestamp": "end_time"})
    return outcomes


def parse_kalshi_end_time(ticker: str) -> datetime | None:
    """Parse KXBTC15M-26MAR092015-15 → end datetime.

    Format: KX{COIN}15M-{YY}{MON}{DD}{HHMM}-{MM}
    The last -MM is the minute of the end time.
    Full time = day at HH:MM where HH from HHMM and MM from suffix.
    Actually the ticker encodes: {date}{start_hour}{start_min}-{end_min}
    E.g., KXBTC15M-26MAR092015-15 means date=2026-Mar-09, starts at 20:15 → but -15 suffix
    Wait: the end time is the round_end timestamp.
    """
    # Actually, let's just use the timestamp from the round_end row.
    # We already have it in the outcomes df.
    return None


# ── Time alignment ──────────────────────────────────────────────────────────

def align_5m_to_15m_pm(outcomes_5m: pd.DataFrame, outcomes_15m: pd.DataFrame) -> pd.DataFrame:
    """Match each 5m round to its containing 15m round.

    A 15m round ending at T covers [T-15m, T].
    A 5m round ending at T5 belongs to the 15m round where T5 is in [T-15m, T].
    Slot: 1 if T5 = T-10m, 2 if T5 = T-5m, 3 if T5 = T.
    """
    if outcomes_5m.empty or outcomes_15m.empty:
        return pd.DataFrame()

    results = []
    # Build lookup: 15m end_date → outcome
    lookup_15m = {}
    for _, r in outcomes_15m.iterrows():
        end_dt = r["end_date"]
        if isinstance(end_dt, str):
            end_dt = pd.to_datetime(end_dt, utc=True)
        lookup_15m[end_dt] = r["outcome"]

    for _, r5 in outcomes_5m.iterrows():
        end_5m = r5["end_date"]
        if isinstance(end_5m, str):
            end_5m = pd.to_datetime(end_5m, utc=True)

        # This 5m round could belong to a 15m round ending at:
        # end_5m (slot 3), end_5m + 5min (slot 2), end_5m + 10min (slot 1)
        for slot, offset in [(3, timedelta(0)), (2, timedelta(minutes=5)), (1, timedelta(minutes=10))]:
            candidate_15m_end = end_5m + offset
            if candidate_15m_end in lookup_15m:
                results.append({
                    "slug_5m": r5["slug"],
                    "end_5m": end_5m,
                    "outcome_5m": r5["outcome"],
                    "end_15m": candidate_15m_end,
                    "outcome_15m": lookup_15m[candidate_15m_end],
                    "slot": slot,
                })
                break  # Only assign to one 15m round

    return pd.DataFrame(results)


def align_5m_to_kalshi(
    outcomes_5m: pd.DataFrame, kalshi_df: pd.DataFrame, kalshi_outcomes: pd.DataFrame
) -> pd.DataFrame:
    """Match PM 5m rounds to Kalshi 15m rounds.

    Kalshi rounds also run on 15-minute boundaries. Use the round_end timestamp
    to determine the Kalshi round's end time, then align similarly.
    """
    if outcomes_5m.empty or kalshi_outcomes.empty:
        return pd.DataFrame()

    # Build lookup: round Kalshi end times to nearest minute → outcome
    # Kalshi end times are at seconds_remaining=0 timestamps
    lookup_kx = {}
    for _, r in kalshi_outcomes.iterrows():
        end_time = r["end_time"]
        if isinstance(end_time, str):
            end_time = pd.to_datetime(end_time, utc=True)
        # Round to nearest minute
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


# ── Mid-round pricing ──────────────────────────────────────────────────────

def get_pm15m_pricing_at_slot1(pm15m_df: pd.DataFrame, aligned: pd.DataFrame) -> pd.DataFrame:
    """Get PM 15m pricing at ~600 seconds remaining (after slot 1 resolves)."""
    if aligned.empty or pm15m_df.empty:
        return pd.DataFrame()

    # For each 15m round in aligned, find price at seconds_remaining ~ 600
    results = []
    for end_15m in aligned["end_15m"].unique():
        slug_mask = pm15m_df["end_date"] == end_15m
        round_data = pm15m_df[slug_mask]
        if round_data.empty:
            continue

        # Get rows near 600 seconds remaining (580-620 window)
        near600 = round_data[
            (round_data["seconds_remaining"] >= 580) & (round_data["seconds_remaining"] <= 620)
        ]
        if near600.empty:
            continue

        # Take the row closest to 600
        idx = (near600["seconds_remaining"] - 600).abs().idxmin()
        row = near600.loc[idx]
        results.append({
            "end_15m": end_15m,
            "up_ask_600": row.get("up_ask"),
            "down_ask_600": row.get("down_ask"),
            "up_bid_600": row.get("up_bid"),
            "down_bid_600": row.get("down_bid"),
            "sec_rem": row["seconds_remaining"],
        })

    return pd.DataFrame(results)


def get_kalshi_pricing_at_slot1(kalshi_df: pd.DataFrame, aligned: pd.DataFrame) -> pd.DataFrame:
    """Get Kalshi 15m pricing at ~600 seconds remaining."""
    if aligned.empty or kalshi_df.empty:
        return pd.DataFrame()

    results = []
    for ticker in aligned["ticker_kx"].unique():
        round_data = kalshi_df[kalshi_df["round_ticker"] == ticker]
        if round_data.empty:
            continue

        near600 = round_data[
            (round_data["seconds_remaining"] >= 580) & (round_data["seconds_remaining"] <= 620)
        ]
        if near600.empty:
            continue

        idx = (near600["seconds_remaining"] - 600).abs().idxmin()
        row = near600.loc[idx]
        results.append({
            "ticker_kx": ticker,
            "yes_ask_600": row.get("yes_ask"),
            "yes_bid_600": row.get("yes_bid"),
            "no_ask_600": row.get("no_bid"),  # no_bid is complement
            "sec_rem": row["seconds_remaining"],
        })

    return pd.DataFrame(results)


# ── Analysis functions ──────────────────────────────────────────────────────

def analyze_agreement(aligned: pd.DataFrame, outcome_col_5m: str, outcome_col_15m: str,
                      map_up: str = "up", map_yes: str = "up") -> dict:
    """Compute agreement rate between 5m and 15m outcomes per slot."""
    results = {}
    # Normalize outcomes to up/down
    df = aligned.copy()
    df["norm_5m"] = df[outcome_col_5m].map({"up": "up", "down": "down"})
    if map_yes == "up":
        df["norm_15m"] = df[outcome_col_15m].map({"up": "up", "down": "down", "yes": "up", "no": "down"})
    else:
        df["norm_15m"] = df[outcome_col_15m]

    df = df.dropna(subset=["norm_5m", "norm_15m"])

    for slot in sorted(df["slot"].unique()):
        slot_data = df[df["slot"] == slot]
        if len(slot_data) == 0:
            continue
        agrees = (slot_data["norm_5m"] == slot_data["norm_15m"]).astype(int).values
        mean, lo, hi = bootstrap_ci(agrees)
        results[slot] = {
            "n": len(agrees),
            "agreement": mean,
            "ci_lo": lo,
            "ci_hi": hi,
        }

    return results


def analyze_cascading(aligned: pd.DataFrame, outcome_col_15m: str) -> dict:
    """When multiple 5m slots in a window agree, how often does 15m agree?"""
    if aligned.empty:
        return {}

    # Normalize
    df = aligned.copy()
    df["norm_5m"] = df["outcome_5m"].map({"up": "up", "down": "down"})
    df["norm_15m"] = df[outcome_col_15m].map({"up": "up", "down": "down", "yes": "up", "no": "down"})
    df = df.dropna(subset=["norm_5m", "norm_15m"])

    results = {}

    # Group by 15m round end
    end_col = "end_15m" if "end_15m" in df.columns else "end_kx"

    for end_time, group in df.groupby(end_col):
        slots_present = sorted(group["slot"].unique())
        slot_outcomes = {row["slot"]: row["norm_5m"] for _, row in group.iterrows()}
        outcome_15m = group["norm_15m"].iloc[0]

        # Only slot 1 available
        if 1 in slot_outcomes:
            key = "slot1_only"
            results.setdefault(key, [])
            results[key].append(int(slot_outcomes[1] == outcome_15m))

        # Slots 1 and 2 agree
        if 1 in slot_outcomes and 2 in slot_outcomes:
            if slot_outcomes[1] == slot_outcomes[2]:
                key = "slots_1_2_agree"
                results.setdefault(key, [])
                results[key].append(int(slot_outcomes[1] == outcome_15m))
            else:
                key = "slots_1_2_disagree"
                results.setdefault(key, [])
                results[key].append(int(slot_outcomes[1] == outcome_15m))

        # All three agree
        if 1 in slot_outcomes and 2 in slot_outcomes and 3 in slot_outcomes:
            if slot_outcomes[1] == slot_outcomes[2] == slot_outcomes[3]:
                key = "all_3_agree"
                results.setdefault(key, [])
                results[key].append(int(slot_outcomes[1] == outcome_15m))

    # Compute stats for each
    summary = {}
    for key, vals in results.items():
        arr = np.array(vals)
        mean, lo, hi = bootstrap_ci(arr)
        summary[key] = {"n": len(arr), "agreement": mean, "ci_lo": lo, "ci_hi": hi}

    return summary


def analyze_tradeable_edge(aligned_pm: pd.DataFrame, pricing_pm: pd.DataFrame,
                           aligned_kx: pd.DataFrame, pricing_kx: pd.DataFrame) -> None:
    """Analyze whether the cascade signal provides tradeable edge after fees."""

    print("\n" + "=" * 80)
    print("SECTION 4: TRADEABLE EDGE ANALYSIS")
    print("=" * 80)

    # ── PM 15m edge ──
    if not aligned_pm.empty and not pricing_pm.empty:
        slot1 = aligned_pm[aligned_pm["slot"] == 1].copy()
        slot1 = slot1.merge(pricing_pm, on="end_15m", how="inner")
        slot1 = slot1.dropna(subset=["up_ask_600", "down_ask_600"])

        if len(slot1) > 0:
            print(f"\n  PM 15m: {len(slot1)} rounds with slot-1 signal + pricing at ~600s")

            # Strategy: after 5m slot1 resolves "up", buy "up" on 15m at up_ask_600
            up_signal = slot1[slot1["outcome_5m"] == "up"].copy()
            down_signal = slot1[slot1["outcome_5m"] == "down"].copy()

            for label, subset, ask_col, outcome_match in [
                ("5m=up → buy UP", up_signal, "up_ask_600", "up"),
                ("5m=down → buy DOWN", down_signal, "down_ask_600", "down"),
            ]:
                if len(subset) == 0:
                    continue
                subset = subset.copy()
                subset["entry"] = pd.to_numeric(subset[ask_col], errors="coerce")
                subset = subset.dropna(subset=["entry"])
                subset["outcome_15m_norm"] = subset["outcome_15m"]
                subset["win"] = (subset["outcome_15m_norm"] == outcome_match).astype(int)
                subset["fee"] = subset["entry"].apply(pm_fee)
                subset["pnl"] = subset.apply(
                    lambda r: (1 - r["entry"] - r["fee"]) if r["win"] else (-r["entry"] - r["fee"]),
                    axis=1,
                )

                wr = subset["win"].mean()
                avg_entry = subset["entry"].mean()
                avg_fee = subset["fee"].mean()
                avg_pnl = subset["pnl"].mean()
                pnl_arr = subset["pnl"].values
                mean_pnl, lo_pnl, hi_pnl = bootstrap_ci(pnl_arr)

                print(f"\n    {label}  (n={len(subset)})")
                print(f"      Win rate:     {wr:.1%}")
                print(f"      Avg entry:    {avg_entry:.4f}")
                print(f"      Avg fee:      {avg_fee:.4f}")
                print(f"      Avg PnL/trade: {avg_pnl:+.4f}  [{lo_pnl:+.4f}, {hi_pnl:+.4f}]")
        else:
            print("\n  PM 15m: no rounds with both signal and pricing data")
    else:
        print("\n  PM 15m: insufficient data for edge analysis")

    # ── Kalshi 15m edge ──
    if not aligned_kx.empty and not pricing_kx.empty:
        slot1 = aligned_kx[aligned_kx["slot"] == 1].copy()
        slot1 = slot1.merge(pricing_kx, on="ticker_kx", how="inner")
        slot1 = slot1.dropna(subset=["yes_ask_600"])

        if len(slot1) > 0:
            print(f"\n  Kalshi 15m: {len(slot1)} rounds with slot-1 signal + pricing at ~600s")

            # 5m up → buy YES on Kalshi; 5m down → buy NO (i.e., sell YES)
            up_signal = slot1[slot1["outcome_5m"] == "up"].copy()
            down_signal = slot1[slot1["outcome_5m"] == "down"].copy()

            for label, subset, direction in [
                ("5m=up → buy YES", up_signal, "yes"),
                ("5m=down → buy NO", down_signal, "no"),
            ]:
                if len(subset) == 0:
                    continue
                subset = subset.copy()
                if direction == "yes":
                    subset["entry"] = pd.to_numeric(subset["yes_ask_600"], errors="coerce")
                    subset["win"] = (subset["outcome_kx"] == "yes").astype(int)
                else:
                    # Buying NO = 1 - yes_bid
                    subset["entry"] = 1 - pd.to_numeric(subset["yes_bid_600"], errors="coerce")
                    subset["win"] = (subset["outcome_kx"] == "no").astype(int)

                subset = subset.dropna(subset=["entry"])
                subset["fee"] = subset["entry"].apply(kalshi_fee)
                subset["pnl"] = subset.apply(
                    lambda r: (1 - r["entry"] - r["fee"]) if r["win"] else (-r["entry"] - r["fee"]),
                    axis=1,
                )

                wr = subset["win"].mean()
                avg_entry = subset["entry"].mean()
                avg_fee = subset["fee"].mean()
                avg_pnl = subset["pnl"].mean()
                pnl_arr = subset["pnl"].values
                mean_pnl, lo_pnl, hi_pnl = bootstrap_ci(pnl_arr)

                print(f"\n    {label}  (n={len(subset)})")
                print(f"      Win rate:     {wr:.1%}")
                print(f"      Avg entry:    {avg_entry:.4f}")
                print(f"      Avg fee:      {avg_fee:.4f}")
                print(f"      Avg PnL/trade: {avg_pnl:+.4f}  [{lo_pnl:+.4f}, {hi_pnl:+.4f}]")
        else:
            print("\n  Kalshi 15m: no rounds with both signal and pricing data")
    else:
        print("\n  Kalshi 15m: insufficient data for edge analysis")


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 80)
    print("CASCADE SIGNAL VALIDATION")
    print("Does a PM 5m outcome predict the overlapping PM/Kalshi 15m outcome?")
    print("=" * 80)

    for coin in COINS:
        print(f"\n{'#' * 80}")
        print(f"# COIN: {coin}")
        print(f"{'#' * 80}")

        # Load data
        pm5m_df = load_pm_rounds(coin, "5m")
        pm15m_df = load_pm_rounds(coin, "15m")
        kx_df = load_kalshi_rounds(coin)

        pm5m_outcomes = pm_round_outcomes(pm5m_df)
        pm15m_outcomes = pm_round_outcomes(pm15m_df)
        kx_outcomes = kalshi_round_outcomes(kx_df)

        print(f"\n  Data loaded:")
        print(f"    PM 5m rounds with outcome:    {len(pm5m_outcomes)}")
        print(f"    PM 15m rounds with outcome:   {len(pm15m_outcomes)}")
        print(f"    Kalshi 15m rounds with outcome: {len(kx_outcomes)}")

        if pm5m_outcomes.empty:
            print("  >> No PM 5m outcome data, skipping coin")
            continue

        # ── Section 1 & 2: PM 5m → PM 15m agreement by slot ──
        aligned_pm = align_5m_to_15m_pm(pm5m_outcomes, pm15m_outcomes)
        print(f"\n  Aligned PM 5m→15m pairs: {len(aligned_pm)}")

        if not aligned_pm.empty:
            print(f"\n  {'=' * 70}")
            print(f"  SECTION 2: 5m SLOT → PM 15m AGREEMENT RATE")
            print(f"  {'=' * 70}")
            agreement = analyze_agreement(aligned_pm, "outcome_5m", "outcome_15m")
            print(f"  {'Slot':<6} {'N':>6} {'Agreement':>12} {'95% CI':>20}")
            print(f"  {'-'*6} {'-'*6} {'-'*12} {'-'*20}")
            for slot in sorted(agreement.keys()):
                a = agreement[slot]
                print(f"  {slot:<6} {a['n']:>6} {a['agreement']:>11.1%} [{a['ci_lo']:.1%}, {a['ci_hi']:.1%}]")

        # ── Section 3: Cascading signals (PM) ──
        if not aligned_pm.empty:
            print(f"\n  {'=' * 70}")
            print(f"  SECTION 3: CASCADING SIGNALS (PM 5m → PM 15m)")
            print(f"  {'=' * 70}")
            cascade = analyze_cascading(aligned_pm, "outcome_15m")
            print(f"  {'Condition':<25} {'N':>6} {'Agreement':>12} {'95% CI':>20}")
            print(f"  {'-'*25} {'-'*6} {'-'*12} {'-'*20}")
            for key in ["slot1_only", "slots_1_2_agree", "slots_1_2_disagree", "all_3_agree"]:
                if key in cascade:
                    c = cascade[key]
                    label = {
                        "slot1_only": "Slot 1 only",
                        "slots_1_2_agree": "Slots 1+2 agree",
                        "slots_1_2_disagree": "Slots 1+2 disagree",
                        "all_3_agree": "All 3 agree",
                    }[key]
                    print(f"  {label:<25} {c['n']:>6} {c['agreement']:>11.1%} [{c['ci_lo']:.1%}, {c['ci_hi']:.1%}]")

        # ── Section 4: Tradeable edge (PM) ──
        pricing_pm = pd.DataFrame()
        if not aligned_pm.empty and not pm15m_df.empty:
            pricing_pm = get_pm15m_pricing_at_slot1(pm15m_df, aligned_pm)

        # ── Section 5: Cross-platform (PM 5m → Kalshi 15m) ──
        aligned_kx = align_5m_to_kalshi(pm5m_outcomes, kx_df, kx_outcomes)
        print(f"\n  Aligned PM 5m→Kalshi 15m pairs: {len(aligned_kx)}")

        if not aligned_kx.empty:
            print(f"\n  {'=' * 70}")
            print(f"  SECTION 5: CROSS-PLATFORM (PM 5m → Kalshi 15m)")
            print(f"  {'=' * 70}")
            agreement_kx = analyze_agreement(aligned_kx, "outcome_5m", "outcome_kx")
            print(f"  {'Slot':<6} {'N':>6} {'Agreement':>12} {'95% CI':>20}")
            print(f"  {'-'*6} {'-'*6} {'-'*12} {'-'*20}")
            for slot in sorted(agreement_kx.keys()):
                a = agreement_kx[slot]
                print(f"  {slot:<6} {a['n']:>6} {a['agreement']:>11.1%} [{a['ci_lo']:.1%}, {a['ci_hi']:.1%}]")

            # Cascading for cross-platform
            print(f"\n  {'=' * 70}")
            print(f"  SECTION 5b: CASCADING SIGNALS (PM 5m → Kalshi 15m)")
            print(f"  {'=' * 70}")
            cascade_kx = analyze_cascading(aligned_kx, "outcome_kx")
            print(f"  {'Condition':<25} {'N':>6} {'Agreement':>12} {'95% CI':>20}")
            print(f"  {'-'*25} {'-'*6} {'-'*12} {'-'*20}")
            for key in ["slot1_only", "slots_1_2_agree", "slots_1_2_disagree", "all_3_agree"]:
                if key in cascade_kx:
                    c = cascade_kx[key]
                    label = {
                        "slot1_only": "Slot 1 only",
                        "slots_1_2_agree": "Slots 1+2 agree",
                        "slots_1_2_disagree": "Slots 1+2 disagree",
                        "all_3_agree": "All 3 agree",
                    }[key]
                    print(f"  {label:<25} {c['n']:>6} {c['agreement']:>11.1%} [{c['ci_lo']:.1%}, {c['ci_hi']:.1%}]")

        # ── Section 4: Tradeable edge ──
        pricing_kx = pd.DataFrame()
        if not aligned_kx.empty and not kx_df.empty:
            pricing_kx = get_kalshi_pricing_at_slot1(kx_df, aligned_kx)

        analyze_tradeable_edge(aligned_pm, pricing_pm, aligned_kx, pricing_kx)

    # ── Summary ──
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print("""
  Key question: Does the first 5m round outcome (slot 1) predict the 15m outcome?
  - Agreement ~50% = no signal (coin flip)
  - Agreement >55% with tight CI = potential signal worth exploring
  - Agreement >60% with tradeable pricing = actionable edge

  Slot 3 should be near-trivially correlated (same close time).
  Slot 1 is the interesting one — 10 minutes of trading remain.

  Cross-platform (PM 5m → Kalshi 15m) is the most interesting case:
  the signal may not be priced into Kalshi's market at all.
    """)


if __name__ == "__main__":
    main()
