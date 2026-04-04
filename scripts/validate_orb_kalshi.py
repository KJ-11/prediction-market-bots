"""Test ORB signal (first-minute BTC/ETH/SOL spot move) on Kalshi 15m markets.

The ORB signal was validated on PM 5m. Here we test: does first-minute spot momentum
also predict Kalshi 15m outcomes? Different oracle (CF Benchmarks), longer timeframe.

Also tests: can we use BTC's first-minute move to predict OTHER coins on Kalshi?
(Cross-coin ORB signal.)
"""
from __future__ import annotations

import glob
import math
from pathlib import Path

import numpy as np
import pandas as pd

DATA_KX = Path(__file__).resolve().parent.parent / "data" / "rounds"
COINS = ["BTC", "ETH", "SOL"]
RNG = np.random.default_rng(42)
N_BOOTSTRAP = 1_000

# Entry timing: seconds into the 15-min round
ENTRY_OFFSETS = [60, 90, 120, 180, 240]


def kalshi_fee(price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return math.ceil(0.07 * price * (1 - price) * 100) / 100


def bootstrap_ci(arr: np.ndarray, n: int = N_BOOTSTRAP, ci: float = 0.95):
    if len(arr) < 2:
        return float("nan"), float("nan"), float("nan")
    means = np.array([arr[RNG.integers(0, len(arr), size=len(arr))].mean() for _ in range(n)])
    alpha = (1 - ci) / 2
    lo, hi = np.quantile(means, [alpha, 1 - alpha])
    return float(arr.mean()), float(lo), float(hi)


def load_kalshi(coin: str) -> pd.DataFrame:
    prefix = f"KX{coin}15M"
    files = sorted(glob.glob(str(DATA_KX / f"{prefix}-*.csv")))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_csv(f, on_bad_lines="skip") for f in files]
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
    for col in ["yes_bid", "yes_ask", "no_bid", "no_ask", "seconds_remaining",
                "spot_price", "strike", "kraken_spot"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def extract_orb_features(df: pd.DataFrame, entry_offset: int = 120) -> pd.DataFrame:
    """Extract ORB features from Kalshi 15m data.

    Signal: first-minute spot move (T+0 to T+60s into the round).
    Entry: book price at T+{entry_offset}s.
    """
    snapshots = df[df["row_type"] == "snapshot"].copy()
    outcomes = df[df["row_type"] == "round_end"][["round_ticker", "outcome"]].drop_duplicates("round_ticker")

    records = []
    for ticker, grp in snapshots.groupby("round_ticker"):
        grp = grp.sort_values("seconds_remaining", ascending=False)
        max_sr = grp["seconds_remaining"].max()

        # Start price: near round start (highest seconds_remaining, ~900 for 15m)
        start_mask = grp["seconds_remaining"].between(max_sr - 15, max_sr + 5)
        if start_mask.sum() == 0:
            continue
        start_row = grp.loc[start_mask].iloc[0]
        start_price = start_row["spot_price"]
        if pd.isna(start_price):
            continue

        # T+60s price
        target_sr = max_sr - 60
        t60_mask = grp["seconds_remaining"].between(target_sr - 5, target_sr + 5)
        if t60_mask.sum() == 0:
            continue
        t60_row = grp.loc[t60_mask].iloc[-1]
        t60_price = t60_row["spot_price"]
        if pd.isna(t60_price):
            continue

        move = t60_price - start_price
        abs_move = abs(move)
        if abs_move < 0.01:
            continue

        predicted = "yes" if move > 0 else "no"  # up = yes (above strike)

        # Entry price at specified offset
        entry_sr = max_sr - entry_offset
        entry_mask = grp["seconds_remaining"].between(entry_sr - 5, entry_sr + 5)
        if entry_mask.sum() > 0:
            entry_row = grp.loc[entry_mask].iloc[-1]
            if predicted == "yes":
                entry = entry_row.get("yes_ask")
            else:
                # Buying NO = 1 - yes_bid
                yes_bid = entry_row.get("yes_bid")
                entry = 1 - yes_bid if pd.notna(yes_bid) else float("nan")
            book_exists = pd.notna(entry) and 0.05 < entry < 0.95
        else:
            entry = float("nan")
            book_exists = False

        # Also grab strike for distance analysis
        strike = start_row.get("strike", float("nan"))

        out = outcomes[outcomes["round_ticker"] == ticker]
        outcome = out["outcome"].iloc[0] if not out.empty and out["outcome"].iloc[0] in ("yes", "no") else None

        records.append({
            "ticker": ticker,
            "start_price": start_price,
            "t60_price": t60_price,
            "move": move,
            "abs_move": abs_move,
            "predicted": predicted,
            "entry_price": entry if book_exists else float("nan"),
            "book_exists": book_exists,
            "outcome": outcome,
            "strike": strike,
            "spot_distance": abs(start_price - strike) if pd.notna(strike) else float("nan"),
        })

    feat = pd.DataFrame(records)
    if feat.empty:
        return feat
    feat = feat[feat["outcome"].notna()]
    feat["correct"] = (feat["predicted"] == feat["outcome"]).astype(int)
    return feat


def analyze_coin(coin: str, df: pd.DataFrame):
    print(f"\n{'#' * 80}")
    print(f"# {coin} — ORB on Kalshi 15m")
    print(f"{'#' * 80}")

    if df.empty:
        print("  No data.")
        return

    n_rounds = df[df["row_type"] == "round_end"]["round_ticker"].nunique()
    print(f"  Total rounds with outcome: {n_rounds}")

    # Test multiple entry offsets
    print(f"\n  {'Entry':>8} {'Rounds':>7} {'Signal':>7} {'Book%':>6} {'Exec':>5} "
          f"{'ExAcc':>7} {'AvgEntry':>9} {'NetEV':>9} {'95% CI':>22}")
    print(f"  {'-'*95}")

    for offset in ENTRY_OFFSETS:
        feat = extract_orb_features(df, entry_offset=offset)
        if feat.empty:
            print(f"  T+{offset:>3}s {'0':>7}")
            continue

        n = len(feat)
        sig_acc = feat["correct"].mean()

        exec_df = feat[feat["book_exists"]].copy()
        book_pct = len(exec_df) / n * 100
        n_exec = len(exec_df)

        if n_exec > 5:
            exec_acc = exec_df["correct"].mean()
            avg_entry = exec_df["entry_price"].mean()
            pnl = exec_df["correct"].values * 1.0 - exec_df["entry_price"].values
            fees = np.array([kalshi_fee(p) for p in exec_df["entry_price"].values])
            net = pnl - fees
            mean_pnl, lo, hi = bootstrap_ci(net)
            print(f"  T+{offset:>3}s {n:>7} {sig_acc:>6.1%} {book_pct:>5.0f}% {n_exec:>5} "
                  f"{exec_acc:>6.1%} ${avg_entry:>7.3f} ${mean_pnl:>+7.3f} [{lo:>+.3f}, {hi:>+.3f}]")
        else:
            print(f"  T+{offset:>3}s {n:>7} {sig_acc:>6.1%} {book_pct:>5.0f}% {n_exec:>5}")

    # Move-bucket analysis at best entry offset
    feat = extract_orb_features(df, entry_offset=120)
    if feat.empty:
        return

    print(f"\n  MOVE BUCKETS (entry at T+120s):")
    print(f"  {'Bucket':<12} {'N':>6} {'Accuracy':>9} {'Exec':>5} {'ExAcc':>7} {'NetEV':>9}")
    print(f"  {'-'*60}")

    buckets = [("$0-10", 0, 10), ("$10-25", 10, 25), ("$25-50", 25, 50),
               ("$50-100", 50, 100), ("$100+", 100, float("inf"))]

    for label, lo, hi in buckets:
        subset = feat[(feat["abs_move"] >= lo) & (feat["abs_move"] < hi)]
        n = len(subset)
        if n == 0:
            continue
        acc = subset["correct"].mean()
        ex = subset[subset["book_exists"]]
        n_ex = len(ex)
        if n_ex > 0:
            ex_acc = ex["correct"].mean()
            pnl = ex["correct"].values * 1.0 - ex["entry_price"].values
            fees = np.array([kalshi_fee(p) for p in ex["entry_price"].values])
            net_ev = np.mean(pnl - fees)
            print(f"  {label:<12} {n:>6} {acc:>8.1%} {n_ex:>5} {ex_acc:>6.1%} ${net_ev:>+7.3f}")
        else:
            print(f"  {label:<12} {n:>6} {acc:>8.1%} {n_ex:>5}")


def analyze_price_capped(df_all: dict[str, pd.DataFrame]):
    """Price-capped entry: only take trades where ask <= threshold."""
    print(f"\n{'=' * 80}")
    print("PRICE-CAPPED ENTRY ANALYSIS")
    print(f"{'=' * 80}")
    print("Only enter when ask price <= cap. Lower price = more upside if correct.")

    for coin in COINS:
        df = df_all.get(coin)
        if df is None or df.empty:
            continue

        feat = extract_orb_features(df, entry_offset=120)
        exec_df = feat[feat["book_exists"]].copy()
        if exec_df.empty:
            continue

        print(f"\n  {coin}:")
        print(f"  {'Cap':>6} {'Trades':>7} {'WinRate':>8} {'AvgEntry':>9} {'NetEV':>9} {'95% CI':>22}")
        print(f"  {'-'*70}")

        for cap in [0.60, 0.65, 0.70, 0.75, 0.80, 0.85]:
            capped = exec_df[exec_df["entry_price"] <= cap]
            n = len(capped)
            if n < 10:
                print(f"  ${cap:.2f} {n:>7}")
                continue
            wr = capped["correct"].mean()
            avg_entry = capped["entry_price"].mean()
            pnl = capped["correct"].values * 1.0 - capped["entry_price"].values
            fees = np.array([kalshi_fee(p) for p in capped["entry_price"].values])
            net = pnl - fees
            mean_pnl, lo, hi = bootstrap_ci(net)
            print(f"  ${cap:.2f} {n:>7} {wr:>7.1%} ${avg_entry:>7.3f} ${mean_pnl:>+7.3f} [{lo:>+.3f}, {hi:>+.3f}]")


def main():
    print("=" * 80)
    print("ORB SIGNAL ON KALSHI 15m")
    print("Does first-minute spot momentum predict Kalshi 15m outcome?")
    print("=" * 80)

    df_all = {}
    for coin in COINS:
        df = load_kalshi(coin)
        if not df.empty:
            n_rounds = df[df["row_type"] == "round_end"]["round_ticker"].nunique()
            print(f"  {coin}: {len(df):,} rows, {n_rounds} rounds")
            df_all[coin] = df
        else:
            print(f"  {coin}: no data")

    for coin in COINS:
        if coin in df_all:
            analyze_coin(coin, df_all[coin])

    analyze_price_capped(df_all)

    print(f"\n{'=' * 80}")
    print("DONE")
    print("=" * 80)


if __name__ == "__main__":
    main()
