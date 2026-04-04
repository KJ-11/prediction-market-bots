"""Deep-dive into Opening Range Breakout findings on PM 5m data.

Investigates: book formation timing, cross-coin performance, entry timing
optimization, time-of-day patterns, move persistence, spread analysis,
and RTDS vs Coinbase divergence.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "rounds" / "polymarket"

COINS = ["BTC", "ETH", "SOL", "XRP"]

# Entry times to test (seconds into the measuring window, i.e. 300 - value = seconds_remaining)
ENTRY_OFFSETS = [30, 45, 60, 90, 120]

BUCKETS = [
    ("$0-10", 0, 10),
    ("$10-25", 10, 25),
    ("$25-50", 25, 50),
    ("$50-100", 50, 100),
    ("$100+", 100, float("inf")),
]


def pm_fee(price: float) -> float:
    """PM crypto fee per contract: price * 0.25 * (price * (1-price))^2."""
    return price * 0.25 * (price * (1 - price)) ** 2


def bootstrap_ci(values: np.ndarray, n_iter: int = 1000, ci: float = 0.95):
    """Bootstrap confidence interval for mean."""
    if len(values) < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(42)
    means = [np.mean(rng.choice(values, size=len(values), replace=True)) for _ in range(n_iter)]
    lo = np.percentile(means, (1 - ci) / 2 * 100)
    hi = np.percentile(means, (1 + ci) / 2 * 100)
    return lo, hi


def load_coin_data(coin: str) -> pd.DataFrame:
    """Load all 5m PM round data for a given coin."""
    files = sorted(glob.glob(str(DATA_DIR / f"{coin}-5m-*.csv")))
    if not files:
        return pd.DataFrame()
    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f, on_bad_lines="skip")
            dfs.append(df)
        except Exception as e:
            print(f"  WARNING: Could not read {os.path.basename(f)}: {e}")
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    # Coerce numeric columns once
    for col in ["seconds_remaining", "spot_price", "up_ask", "down_ask",
                 "up_bid", "down_bid", "spread", "rtds_price", "kraken_price"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["coin"] = coin
    return df


def load_all_data() -> pd.DataFrame:
    """Load data for all coins."""
    dfs = []
    for coin in COINS:
        df = load_coin_data(coin)
        if not df.empty:
            print(f"  {coin}: {len(df):,} rows, {df['slug'].nunique()} rounds")
            dfs.append(df)
        else:
            print(f"  {coin}: no data")
    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


def get_snapshots_and_outcomes(df: pd.DataFrame):
    """Split data into snapshots and outcomes."""
    snapshots = df[df["row_type"] == "snapshot"].copy()
    outcomes = df[df["row_type"] == "round_end"][["slug", "outcome"]].drop_duplicates("slug")
    return snapshots, outcomes


def assign_bucket(abs_move: float) -> str:
    for label, lo, hi in BUCKETS:
        if lo <= abs_move < hi:
            return label
    return "$100+"


# ═══════════════════════════════════════════════════════════════════
# 1. BOOK AVAILABILITY TIMING
# ═══════════════════════════════════════════════════════════════════

def analyze_book_timing(df: pd.DataFrame):
    """At what seconds_remaining does a tradeable book form (ask < 0.90)?"""
    print("=" * 85)
    print("1. BOOK AVAILABILITY TIMING")
    print("=" * 85)

    snapshots = df[(df["row_type"] == "snapshot") & (df["seconds_remaining"].between(0, 300))].copy()
    snapshots["has_book"] = (
        snapshots["up_ask"].notna() & (snapshots["up_ask"] < 0.90) &
        snapshots["down_ask"].notna() & (snapshots["down_ask"] < 0.90)
    )

    # Per-round: first seconds_remaining where book exists
    first_book = []
    for slug, grp in snapshots.groupby("slug"):
        grp_sorted = grp.sort_values("seconds_remaining", ascending=False)
        book_rows = grp_sorted[grp_sorted["has_book"]]
        if not book_rows.empty:
            first_sr = book_rows["seconds_remaining"].max()  # highest = earliest
            first_book.append({"slug": slug, "first_book_sr": first_sr,
                               "coin": grp["coin"].iloc[0]})

    fb_df = pd.DataFrame(first_book)
    if fb_df.empty:
        print("No books found.\n")
        return

    # Convert to "time into round" for readability
    fb_df["entry_available_at"] = 300 - fb_df["first_book_sr"]

    print(f"Rounds with book: {len(fb_df)} / {snapshots['slug'].nunique()}")
    print()
    print("Distribution of FIRST book appearance (seconds into measuring window):")
    print("-" * 50)

    bins = [0, 15, 30, 45, 60, 90, 120, 180, 240, 300]
    fb_df["bin"] = pd.cut(fb_df["entry_available_at"], bins=bins, right=True)
    dist = fb_df["bin"].value_counts().sort_index()
    total = len(fb_df)
    cumul = 0
    for interval, count in dist.items():
        cumul += count
        pct = count / total * 100
        cum_pct = cumul / total * 100
        print(f"  {str(interval):<12}  {count:>5} rounds ({pct:>5.1f}%)  cumulative: {cum_pct:>5.1f}%")

    print()
    print(f"Median first book at: T+{fb_df['entry_available_at'].median():.0f}s")
    print(f"P25: T+{fb_df['entry_available_at'].quantile(0.25):.0f}s  "
          f"P75: T+{fb_df['entry_available_at'].quantile(0.75):.0f}s")
    print()

    # Book availability rate at each checkpoint
    print("Book availability rate at entry checkpoints:")
    print("-" * 50)
    for offset in ENTRY_OFFSETS:
        sr = 300 - offset
        window_snaps = snapshots[snapshots["seconds_remaining"].between(sr - 5, sr + 5)]
        slug_book = window_snaps.groupby("slug")["has_book"].any()
        avail = slug_book.mean() * 100 if len(slug_book) > 0 else 0
        print(f"  T+{offset:>3}s (sr={sr}): {slug_book.sum():>5}/{len(slug_book)} rounds have book ({avail:.1f}%)")
    print()


# ═══════════════════════════════════════════════════════════════════
# 2. CROSS-COIN ANALYSIS
# ═══════════════════════════════════════════════════════════════════

def analyze_cross_coin(df: pd.DataFrame):
    """Does ORB work across coins?"""
    print("=" * 85)
    print("2. CROSS-COIN ORB ANALYSIS")
    print("=" * 85)

    snapshots, outcomes = get_snapshots_and_outcomes(df)

    print(f"{'Coin':<6} {'Rounds':>7} {'Accuracy':>9} {'95% CI':>18} "
          f"{'Book%':>6} {'Exec Acc':>9} {'Med Entry':>10} {'Net EV':>9}")
    print("-" * 85)

    for coin in COINS:
        coin_snaps = snapshots[snapshots["coin"] == coin]
        coin_outcomes = outcomes[outcomes["slug"].str.lower().str.startswith(coin.lower())]
        if coin_snaps.empty:
            print(f"{coin:<6} {'—':>7}")
            continue

        records = _extract_round_features_generic(coin_snaps, coin_outcomes, entry_offset=60)
        if not records:
            print(f"{coin:<6} {'0':>7}")
            continue

        feat = pd.DataFrame(records)
        feat = feat[feat["outcome"].isin(["up", "down"])]
        if feat.empty:
            print(f"{coin:<6} {'0':>7}")
            continue

        n = len(feat)
        acc = feat["correct"].mean()
        ci_lo, ci_hi = bootstrap_ci(feat["correct"].values)

        exec_df = feat[feat["book_exists"]]
        book_pct = len(exec_df) / n * 100
        if len(exec_df) > 0:
            exec_acc = exec_df["correct"].mean()
            med_entry = exec_df["entry_price"].median()
            pnl = exec_df["correct"].values * 1.0 - exec_df["entry_price"].values
            fees = np.array([pm_fee(p) for p in exec_df["entry_price"].values])
            net_ev = np.mean(pnl - fees)
            print(f"{coin:<6} {n:>7} {acc:>8.1%} [{ci_lo:>5.1%}, {ci_hi:>5.1%}] "
                  f"{book_pct:>5.0f}% {exec_acc:>8.1%} ${med_entry:>8.2f} ${net_ev:>+7.3f}")
        else:
            print(f"{coin:<6} {n:>7} {acc:>8.1%} [{ci_lo:>5.1%}, {ci_hi:>5.1%}] "
                  f"{book_pct:>5.0f}% {'—':>9} {'—':>10} {'—':>9}")

    print()


def _extract_round_features_generic(snapshots, outcomes, entry_offset=60):
    """Extract ORB features for given snapshots and outcomes."""
    sr_entry = 300 - entry_offset
    records = []
    for slug, grp in snapshots.groupby("slug"):
        grp = grp.sort_values("seconds_remaining", ascending=False)

        # Start price at sr~300
        start_mask = grp["seconds_remaining"].between(295, 310)
        if start_mask.sum() == 0:
            continue
        start_row = grp.loc[start_mask].iloc[-1]
        start_price = start_row["spot_price"]
        if pd.isna(start_price):
            continue

        # First-minute price (always at T+60 for the signal)
        t60_mask = grp["seconds_remaining"].between(235, 245)
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

        predicted = "up" if move > 0 else "down"

        # Entry price at the specified offset
        entry_mask = grp["seconds_remaining"].between(sr_entry - 5, sr_entry + 5)
        if entry_mask.sum() > 0:
            entry_row = grp.loc[entry_mask].iloc[-1]
            entry = entry_row["up_ask"] if predicted == "up" else entry_row["down_ask"]
            book_exists = not pd.isna(entry) and entry < 0.90
        else:
            entry = float("nan")
            book_exists = False

        # Outcome
        out = outcomes[outcomes["slug"] == slug]
        outcome = out["outcome"].iloc[0] if not out.empty else None

        rec = {
            "slug": slug,
            "start_price": start_price,
            "t60_price": t60_price,
            "move": move,
            "abs_move": abs_move,
            "predicted": predicted,
            "entry_price": entry if book_exists else float("nan"),
            "book_exists": book_exists,
            "outcome": outcome,
        }

        # Extra fields for downstream analyses
        if "rtds_price" in grp.columns and entry_mask.sum() > 0:
            entry_row = grp.loc[entry_mask].iloc[-1]
            rec["rtds_at_entry"] = entry_row.get("rtds_price", float("nan"))
            rec["spot_at_entry"] = entry_row["spot_price"]
        if "up_bid" in grp.columns and entry_mask.sum() > 0:
            entry_row = grp.loc[entry_mask].iloc[-1]
            rec["up_bid"] = entry_row.get("up_bid", float("nan"))
            rec["up_ask"] = entry_row.get("up_ask", float("nan"))
            rec["down_bid"] = entry_row.get("down_bid", float("nan"))
            rec["down_ask"] = entry_row.get("down_ask", float("nan"))
            rec["spread_at_entry"] = entry_row.get("spread", float("nan"))

        # Timestamp for time-of-day analysis
        if "timestamp" in grp.columns:
            rec["timestamp"] = start_row.get("timestamp")

        # Spot prices at later checkpoints for persistence analysis
        for check_offset in [120, 180]:
            check_sr = 300 - check_offset
            check_mask = grp["seconds_remaining"].between(check_sr - 5, check_sr + 5)
            if check_mask.sum() > 0:
                check_row = grp.loc[check_mask].iloc[-1]
                rec[f"spot_t{check_offset}"] = check_row["spot_price"]
            else:
                rec[f"spot_t{check_offset}"] = float("nan")

        records.append(rec)

    # Apply outcome filter
    for rec in records:
        if rec["outcome"] in ("up", "down"):
            rec["correct"] = int(rec["predicted"] == rec["outcome"])
        else:
            rec["correct"] = None

    return [r for r in records if r["correct"] is not None]


# ═══════════════════════════════════════════════════════════════════
# 3. ENTRY TIMING OPTIMIZATION
# ═══════════════════════════════════════════════════════════════════

def analyze_entry_timing(df: pd.DataFrame):
    """Test multiple entry offsets for BTC."""
    print("=" * 85)
    print("3. ENTRY TIMING OPTIMIZATION (BTC)")
    print("=" * 85)

    btc = df[df["coin"] == "BTC"]
    snapshots, outcomes = get_snapshots_and_outcomes(btc)

    print(f"{'Entry':>8} {'Rounds':>7} {'Signal':>7} {'Book%':>6} {'Exec':>5} "
          f"{'ExAcc':>7} {'Med$':>7} {'GrossEV':>9} {'NetEV':>9} {'NetCI':>18}")
    print("-" * 95)

    for offset in ENTRY_OFFSETS:
        records = _extract_round_features_generic(snapshots, outcomes, entry_offset=offset)
        if not records:
            print(f"T+{offset:>3}s {'0':>7}")
            continue

        feat = pd.DataFrame(records)
        n = len(feat)
        sig_acc = feat["correct"].mean()

        exec_df = feat[feat["book_exists"]]
        book_pct = len(exec_df) / n * 100
        n_exec = len(exec_df)

        if n_exec > 0:
            exec_acc = exec_df["correct"].mean()
            med_entry = exec_df["entry_price"].median()
            pnl = exec_df["correct"].values * 1.0 - exec_df["entry_price"].values
            fees = np.array([pm_fee(p) for p in exec_df["entry_price"].values])
            net = pnl - fees
            gross_ev = np.mean(pnl)
            net_ev = np.mean(net)
            ci_lo, ci_hi = bootstrap_ci(net)
            print(f"T+{offset:>3}s {n:>7} {sig_acc:>6.1%} {book_pct:>5.0f}% {n_exec:>5} "
                  f"{exec_acc:>6.1%} ${med_entry:>5.2f} ${gross_ev:>+7.3f} ${net_ev:>+7.3f} "
                  f"[${ci_lo:>+6.3f}, ${ci_hi:>+6.3f}]")
        else:
            print(f"T+{offset:>3}s {n:>7} {sig_acc:>6.1%} {book_pct:>5.0f}% {n_exec:>5} "
                  f"{'—':>7} {'—':>7} {'—':>9} {'—':>9} {'—':>18}")

    print()
    print("Note: Signal is always based on first-minute move (T+0 to T+60s).")
    print("Entry timing only affects book availability and entry price.")
    print()


# ═══════════════════════════════════════════════════════════════════
# 4. TIME-OF-DAY PATTERNS
# ═══════════════════════════════════════════════════════════════════

def analyze_time_of_day(df: pd.DataFrame):
    """Does ORB accuracy vary by hour?"""
    print("=" * 85)
    print("4. TIME-OF-DAY PATTERNS (BTC)")
    print("=" * 85)

    btc = df[df["coin"] == "BTC"]
    snapshots, outcomes = get_snapshots_and_outcomes(btc)
    records = _extract_round_features_generic(snapshots, outcomes, entry_offset=60)
    if not records:
        print("No data.\n")
        return

    feat = pd.DataFrame(records)
    feat["ts"] = pd.to_datetime(feat["timestamp"], errors="coerce", utc=True)
    feat["hour_utc"] = feat["ts"].dt.hour
    feat = feat.dropna(subset=["hour_utc"])
    feat["hour_utc"] = feat["hour_utc"].astype(int)

    print(f"{'Hour(UTC)':<10} {'Rounds':>7} {'Accuracy':>9} {'Book%':>6} "
          f"{'ExecN':>6} {'ExAcc':>7} {'NetEV':>9}")
    print("-" * 70)

    for hour in sorted(feat["hour_utc"].unique()):
        hf = feat[feat["hour_utc"] == hour]
        n = len(hf)
        acc = hf["correct"].mean()
        exec_df = hf[hf["book_exists"]]
        book_pct = len(exec_df) / n * 100
        n_exec = len(exec_df)
        if n_exec > 0:
            exec_acc = exec_df["correct"].mean()
            pnl = exec_df["correct"].values * 1.0 - exec_df["entry_price"].values
            fees = np.array([pm_fee(p) for p in exec_df["entry_price"].values])
            net_ev = np.mean(pnl - fees)
            print(f"{hour:>4}:00    {n:>7} {acc:>8.1%} {book_pct:>5.0f}% "
                  f"{n_exec:>6} {exec_acc:>6.1%} ${net_ev:>+7.3f}")
        else:
            print(f"{hour:>4}:00    {n:>7} {acc:>8.1%} {book_pct:>5.0f}% "
                  f"{n_exec:>6} {'—':>7} {'—':>9}")

    print()


# ═══════════════════════════════════════════════════════════════════
# 5. MOVE PERSISTENCE
# ═══════════════════════════════════════════════════════════════════

def analyze_move_persistence(df: pd.DataFrame):
    """For $25+ first-minute moves, does price continue or reverse?"""
    print("=" * 85)
    print("5. MOVE PERSISTENCE — $25+ FIRST-MINUTE MOVES (BTC)")
    print("=" * 85)

    btc = df[df["coin"] == "BTC"]
    snapshots, outcomes = get_snapshots_and_outcomes(btc)
    records = _extract_round_features_generic(snapshots, outcomes, entry_offset=60)
    if not records:
        print("No data.\n")
        return

    feat = pd.DataFrame(records)
    big = feat[feat["abs_move"] >= 25].copy()

    print(f"Rounds with first-minute move >= $25: {len(big)}")
    if big.empty:
        print("Not enough data.\n")
        return

    print()
    print("Spot price progression relative to T+0 start price:")
    print(f"{'Check':>8} {'Continues':>10} {'Reverses':>10} {'Continue%':>10} {'MedFurther':>11} {'MedReverse':>11}")
    print("-" * 70)

    for check_offset in [120, 180]:
        col = f"spot_t{check_offset}"
        valid = big.dropna(subset=[col])
        if valid.empty:
            print(f"T+{check_offset}s  {'—':>10} {'—':>10}")
            continue

        # Check if price at T+check_offset has moved further in the same direction
        # vs the start price compared to the T+60 move direction
        continued = 0
        reversed_ = 0
        further_amounts = []
        reverse_amounts = []

        for _, row in valid.iterrows():
            move_dir = 1 if row["move"] > 0 else -1
            later_move = (row[col] - row["start_price"]) * move_dir
            first_move = row["abs_move"]

            if later_move >= first_move:
                continued += 1
                further_amounts.append(later_move - first_move)
            else:
                reversed_ += 1
                reverse_amounts.append(first_move - later_move)

        total = continued + reversed_
        cont_pct = continued / total * 100 if total else 0
        med_further = np.median(further_amounts) if further_amounts else 0
        med_reverse = np.median(reverse_amounts) if reverse_amounts else 0

        print(f"T+{check_offset:>3}s  {continued:>10} {reversed_:>10} {cont_pct:>9.1f}% "
              f"${med_further:>9.2f} ${med_reverse:>9.2f}")

    # Also show: of the $25+ moves, how many resolved in the predicted direction?
    print()
    print("Resolution accuracy for $25+ moves:")
    print("-" * 50)
    for label, lo, hi in [("$25-50", 25, 50), ("$50-100", 50, 100), ("$100+", 100, float("inf"))]:
        subset = big[(big["abs_move"] >= lo) & (big["abs_move"] < hi)]
        if len(subset) == 0:
            continue
        acc = subset["correct"].mean()
        ci_lo, ci_hi = bootstrap_ci(subset["correct"].values)
        print(f"  {label:<10} {len(subset):>5} rounds  {acc:.1%} [{ci_lo:.1%}, {ci_hi:.1%}]")
    print()


# ═══════════════════════════════════════════════════════════════════
# 6. SPREAD ANALYSIS
# ═══════════════════════════════════════════════════════════════════

def analyze_spreads(df: pd.DataFrame):
    """Typical bid-ask spreads when a book exists."""
    print("=" * 85)
    print("6. SPREAD ANALYSIS AT T+60s (BTC)")
    print("=" * 85)

    btc = df[df["coin"] == "BTC"]
    snapshots, outcomes = get_snapshots_and_outcomes(btc)
    records = _extract_round_features_generic(snapshots, outcomes, entry_offset=60)
    if not records:
        print("No data.\n")
        return

    feat = pd.DataFrame(records)
    exec_df = feat[feat["book_exists"]].copy()

    if exec_df.empty:
        print("No executable rounds.\n")
        return

    # Compute effective spread for the side we'd trade
    exec_df["eff_spread"] = exec_df.apply(
        lambda r: (r["up_ask"] - r["up_bid"]) if r["predicted"] == "up" and pd.notna(r["up_bid"])
        else (r["down_ask"] - r["down_bid"]) if r["predicted"] == "down" and pd.notna(r["down_bid"])
        else float("nan"), axis=1
    )

    valid_spread = exec_df.dropna(subset=["eff_spread"])
    if valid_spread.empty:
        print("No spread data available.\n")
        return

    print(f"Rounds with spread data: {len(valid_spread)}")
    print()
    print("Effective bid-ask spread (taker cost):")
    print("-" * 50)
    print(f"  Mean:   ${valid_spread['eff_spread'].mean():.4f}")
    print(f"  Median: ${valid_spread['eff_spread'].median():.4f}")
    print(f"  P25:    ${valid_spread['eff_spread'].quantile(0.25):.4f}")
    print(f"  P75:    ${valid_spread['eff_spread'].quantile(0.75):.4f}")
    print(f"  P90:    ${valid_spread['eff_spread'].quantile(0.90):.4f}")
    print()

    # Spread distribution
    print("Spread distribution:")
    print("-" * 50)
    spread_bins = [0, 0.01, 0.02, 0.03, 0.05, 0.10, 0.20, 1.0]
    valid_spread["sp_bin"] = pd.cut(valid_spread["eff_spread"], bins=spread_bins, right=True)
    dist = valid_spread["sp_bin"].value_counts().sort_index()
    for interval, count in dist.items():
        pct = count / len(valid_spread) * 100
        print(f"  {str(interval):<12}  {count:>5} ({pct:>5.1f}%)")
    print()

    # Entry price distribution
    print("Entry price distribution:")
    print("-" * 50)
    price_bins = [0, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.80, 0.90]
    exec_df["price_bin"] = pd.cut(exec_df["entry_price"], bins=price_bins, right=True)
    pd_dist = exec_df["price_bin"].value_counts().sort_index()
    for interval, count in pd_dist.items():
        pct = count / len(exec_df) * 100
        print(f"  {str(interval):<12}  {count:>5} ({pct:>5.1f}%)")
    print()

    # Fee impact at different entry prices
    print("Fee impact by entry price:")
    print("-" * 50)
    test_prices = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]
    for p in test_prices:
        fee = pm_fee(p)
        print(f"  Entry ${p:.2f} → fee ${fee:.4f} ({fee/p*100:.2f}% of cost)")
    print()


# ═══════════════════════════════════════════════════════════════════
# 7. RTDS vs COINBASE DIVERGENCE
# ═══════════════════════════════════════════════════════════════════

def analyze_rtds_divergence(df: pd.DataFrame):
    """When RTDS and Coinbase spot diverge at T+60s, does that predict better?"""
    print("=" * 85)
    print("7. RTDS vs COINBASE DIVERGENCE AT ENTRY (BTC)")
    print("=" * 85)

    btc = df[df["coin"] == "BTC"]
    snapshots, outcomes = get_snapshots_and_outcomes(btc)
    records = _extract_round_features_generic(snapshots, outcomes, entry_offset=60)
    if not records:
        print("No data.\n")
        return

    feat = pd.DataFrame(records)
    feat["rtds_at_entry"] = pd.to_numeric(feat.get("rtds_at_entry"), errors="coerce")
    feat["spot_at_entry"] = pd.to_numeric(feat.get("spot_at_entry"), errors="coerce")

    valid = feat.dropna(subset=["rtds_at_entry", "spot_at_entry"])
    valid = valid[valid["rtds_at_entry"] > 0]  # filter out zeros

    if valid.empty:
        print("No rounds with both RTDS and spot data at entry.\n")
        return

    valid = valid.copy()
    valid["divergence"] = valid["spot_at_entry"] - valid["rtds_at_entry"]
    valid["abs_divergence"] = valid["divergence"].abs()

    print(f"Rounds with RTDS+spot at T+60s: {len(valid)}")
    print()
    print("Divergence statistics (Coinbase - RTDS):")
    print("-" * 50)
    print(f"  Mean abs divergence: ${valid['abs_divergence'].mean():.2f}")
    print(f"  Median abs divergence: ${valid['abs_divergence'].median():.2f}")
    print(f"  P90 abs divergence: ${valid['abs_divergence'].quantile(0.90):.2f}")
    print()

    # Split into aligned vs divergent
    # "Aligned" = both sources agree on direction relative to predicted move
    # Divergence direction: if predicted=up and rtds > coinbase, RTDS is "ahead"
    valid["div_agrees_signal"] = (
        ((valid["predicted"] == "up") & (valid["divergence"] > 0)) |
        ((valid["predicted"] == "down") & (valid["divergence"] < 0))
    )

    # Test: does large divergence predict outcome?
    div_buckets = [
        ("< $5", valid[valid["abs_divergence"] < 5]),
        ("$5-20", valid[(valid["abs_divergence"] >= 5) & (valid["abs_divergence"] < 20)]),
        ("$20+", valid[valid["abs_divergence"] >= 20]),
    ]

    print("Accuracy by abs(divergence) at entry:")
    print("-" * 60)
    print(f"{'Divergence':<12} {'Rounds':>7} {'Accuracy':>9} {'95% CI':>18}")
    print("-" * 60)
    for label, subset in div_buckets:
        n = len(subset)
        if n == 0:
            print(f"{label:<12} {'0':>7}")
            continue
        acc = subset["correct"].mean()
        ci_lo, ci_hi = bootstrap_ci(subset["correct"].values)
        print(f"{label:<12} {n:>7} {acc:>8.1%} [{ci_lo:>5.1%}, {ci_hi:>5.1%}]")
    print()

    # Does divergence direction matter?
    print("Accuracy when divergence direction agrees vs disagrees with signal:")
    print("-" * 60)
    for agrees, label in [(True, "Agrees"), (False, "Disagrees")]:
        subset = valid[valid["div_agrees_signal"] == agrees]
        n = len(subset)
        if n == 0:
            print(f"{label:<12} {'0':>7}")
            continue
        acc = subset["correct"].mean()
        ci_lo, ci_hi = bootstrap_ci(subset["correct"].values)
        print(f"{label:<12} {n:>7} {acc:>8.1%} [{ci_lo:>5.1%}, {ci_hi:>5.1%}]")
    print()


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    print("Loading PM 5m data for all coins...")
    df = load_all_data()
    if df.empty:
        print("No data found.")
        return
    print()

    analyze_book_timing(df)
    analyze_cross_coin(df)
    analyze_entry_timing(df)
    analyze_time_of_day(df)
    analyze_move_persistence(df)
    analyze_spreads(df)
    analyze_rtds_divergence(df)

    print("=" * 85)
    print("DONE")
    print("=" * 85)


if __name__ == "__main__":
    main()
