"""Validate Opening Range Breakout strategy on PM 5m BTC data.

ORB claim: first-minute BTC move predicts 5-min resolution direction.
External backtest (4,389 trades): $10=57%, $10-25=68%, $50-100=76%, $100+=99%.
"""
from __future__ import annotations

import glob
import os
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "rounds" / "polymarket"

# Move magnitude buckets (USD)
BUCKETS = [
    ("$0-10", 0, 10),
    ("$10-25", 10, 25),
    ("$25-50", 25, 50),
    ("$50-100", 50, 100),
    ("$100+", 100, float("inf")),
]

# External claims to compare against
EXTERNAL_CLAIMS = {
    "$0-10": 0.57,
    "$10-25": 0.68,
    "$25-50": None,  # not reported separately
    "$50-100": 0.76,
    "$100+": 0.99,
}


def pm_fee(price: float) -> float:
    """PM crypto fee per contract: price * 0.25 * (price * (1-price))^2."""
    return price * 0.25 * (price * (1 - price)) ** 2


def pm_fee_maker(price: float) -> float:
    """PM maker fee (20% rebate)."""
    return pm_fee(price) * 0.80


def bootstrap_ci(values: np.ndarray, n_iter: int = 1000, ci: float = 0.95):
    """Bootstrap confidence interval for mean."""
    if len(values) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(42)
    means = [np.mean(rng.choice(values, size=len(values), replace=True)) for _ in range(n_iter)]
    lo = np.percentile(means, (1 - ci) / 2 * 100)
    hi = np.percentile(means, (1 + ci) / 2 * 100)
    return lo, hi


def load_data() -> pd.DataFrame:
    """Load all BTC 5m PM round data."""
    files = sorted(glob.glob(str(DATA_DIR / "BTC-5m-*.csv")))
    if not files:
        print("No BTC-5m files found")
        return pd.DataFrame()
    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f, on_bad_lines="skip")
            dfs.append(df)
        except Exception as e:
            print(f"  WARNING: Could not read {os.path.basename(f)}: {e}")
    return pd.concat(dfs, ignore_index=True)


def extract_round_features(df: pd.DataFrame) -> pd.DataFrame:
    """Extract per-round features for ORB analysis.

    For each 5m round:
    - start_price: spot at seconds_remaining nearest to 300 (round measuring window start)
    - t60_price: spot at seconds_remaining nearest to 240 (T+60s into measuring window)
    - entry_price: up_ask or down_ask at T+60s (taker entry)
    - outcome: up or down
    """
    df["seconds_remaining"] = pd.to_numeric(df["seconds_remaining"], errors="coerce")
    df["spot_price"] = pd.to_numeric(df["spot_price"], errors="coerce")
    df["up_ask"] = pd.to_numeric(df["up_ask"], errors="coerce")
    df["down_ask"] = pd.to_numeric(df["down_ask"], errors="coerce")

    snapshots = df[df["row_type"] == "snapshot"].copy()
    outcomes = df[df["row_type"] == "round_end"][["slug", "outcome"]].drop_duplicates("slug")

    records = []
    for slug, grp in snapshots.groupby("slug"):
        grp = grp.sort_values("seconds_remaining", ascending=False)

        # Find snapshot nearest to 300s (start of 5-min measuring window)
        start_mask = grp["seconds_remaining"].between(295, 310)
        if start_mask.sum() == 0:
            continue
        start_row = grp.loc[start_mask].iloc[-1]  # closest to 300 from above
        start_price = start_row["spot_price"]
        if pd.isna(start_price):
            continue

        # Find snapshot nearest to 240s (T+60s)
        t60_mask = grp["seconds_remaining"].between(235, 245)
        if t60_mask.sum() == 0:
            continue
        t60_row = grp.loc[t60_mask].iloc[-1]  # closest to 240 from above
        t60_price = t60_row["spot_price"]
        if pd.isna(t60_price):
            continue

        move = t60_price - start_price
        abs_move = abs(move)

        if abs_move < 0.01:  # essentially no move
            continue

        predicted = "up" if move > 0 else "down"

        # Entry price at T+60s
        entry = t60_row["up_ask"] if predicted == "up" else t60_row["down_ask"]
        book_exists = not pd.isna(entry) and entry < 0.90

        records.append({
            "slug": slug,
            "start_price": start_price,
            "t60_price": t60_price,
            "move": move,
            "abs_move": abs_move,
            "predicted": predicted,
            "entry_price": entry if book_exists else float("nan"),
            "book_exists": book_exists,
        })

    features = pd.DataFrame(records)
    if features.empty:
        return features

    # Merge outcomes
    features = features.merge(outcomes, on="slug", how="left")
    features = features[features["outcome"].isin(["up", "down"])]
    features["correct"] = (features["predicted"] == features["outcome"]).astype(int)

    return features


def assign_bucket(abs_move: float) -> str:
    for label, lo, hi in BUCKETS:
        if lo <= abs_move < hi:
            return label
    return "$100+"


def print_results(features: pd.DataFrame) -> None:
    total_rounds = len(features)
    up_pct = (features["outcome"] == "up").mean()
    baseline = max(up_pct, 1 - up_pct)

    print("=" * 85)
    print("OPENING RANGE BREAKOUT — BTC 5m Polymarket")
    print("=" * 85)
    print(f"Data: {total_rounds} complete rounds with T+0 and T+60s coverage")
    print(f"Baseline: {up_pct:.1%} up / {1-up_pct:.1%} down (always-pick-majority = {baseline:.1%})")
    print()

    features["bucket"] = features["abs_move"].apply(assign_bucket)

    # ── Signal Accuracy ──
    print("SIGNAL ACCURACY BY FIRST-MINUTE MOVE:")
    print("-" * 85)
    header = f"{'Bucket':<10} {'Rounds':>7} {'Accuracy':>9} {'95% CI':>18} {'vs Base':>9} {'Claimed':>9}"
    print(header)
    print("-" * 85)

    for label, lo, hi in BUCKETS:
        subset = features[features["bucket"] == label]
        n = len(subset)
        if n == 0:
            print(f"{label:<10} {'0':>7} {'—':>9} {'—':>18} {'—':>9} {'—':>9}")
            continue
        acc = subset["correct"].mean()
        ci_lo, ci_hi = bootstrap_ci(subset["correct"].values)
        vs_base = acc - baseline
        claimed = EXTERNAL_CLAIMS.get(label)
        claimed_str = f"{claimed:.0%}" if claimed else "—"
        print(f"{label:<10} {n:>7} {acc:>8.1%} [{ci_lo:>5.1%}, {ci_hi:>5.1%}] {vs_base:>+8.1%} {claimed_str:>9}")

    print()

    # ── Executable Trades ──
    executable = features[features["book_exists"]].copy()
    book_pct = len(executable) / total_rounds * 100 if total_rounds else 0

    print(f"EXECUTABLE TRADES (book exists, ask < $0.90 at T+60s):")
    print(f"Book availability: {len(executable)}/{total_rounds} rounds ({book_pct:.0f}%)")
    print("-" * 85)
    header = f"{'Bucket':<10} {'Trades':>7} {'Accuracy':>9} {'Med Entry':>10} {'Gross EV':>9} {'Net EV':>9} {'95% CI (Net)':>18}"
    print(header)
    print("-" * 85)

    for label, lo, hi in BUCKETS:
        subset = executable[executable["bucket"] == label]
        n = len(subset)
        if n == 0:
            print(f"{label:<10} {'0':>7}")
            continue

        acc = subset["correct"].mean()
        med_entry = subset["entry_price"].median()

        # Per-trade EV: win pays $1, lose pays $0
        per_trade_gross = subset["correct"].values * 1.0 - subset["entry_price"].values
        per_trade_fee = np.array([pm_fee(p) for p in subset["entry_price"].values])
        per_trade_net = per_trade_gross - per_trade_fee

        gross_ev = np.mean(per_trade_gross)
        net_ev = np.mean(per_trade_net)
        ci_lo, ci_hi = bootstrap_ci(per_trade_net)

        print(f"{label:<10} {n:>7} {acc:>8.1%} ${med_entry:>8.2f} ${gross_ev:>+7.3f} ${net_ev:>+7.3f} [${ci_lo:>+6.3f}, ${ci_hi:>+6.3f}]")

    print()

    # ── Move Distribution ──
    print("MOVE DISTRIBUTION:")
    print("-" * 50)
    for label, lo, hi in BUCKETS:
        n = (features["bucket"] == label).sum()
        pct = n / total_rounds * 100 if total_rounds else 0
        print(f"  {label:<10} {n:>6} rounds ({pct:>5.1f}%)")

    print()
    print("MEDIAN ABSOLUTE MOVE: ${:.2f}".format(features["abs_move"].median()))
    print("MEAN ABSOLUTE MOVE:   ${:.2f}".format(features["abs_move"].mean()))
    print()


def main():
    print("Loading BTC 5m PM data...")
    df = load_data()
    if df.empty:
        print("No data found.")
        return

    total_rows = len(df)
    total_slugs = df["slug"].nunique()
    print(f"  {total_rows:,} rows from {total_slugs} rounds")
    print()

    print("Extracting round features...")
    features = extract_round_features(df)
    if features.empty:
        print("No complete rounds with T+0/T+60s coverage.")
        return

    print(f"  {len(features)} rounds with valid first-minute data")
    print()

    print_results(features)


if __name__ == "__main__":
    main()
