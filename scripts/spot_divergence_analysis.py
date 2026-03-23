"""Spot price divergence analysis across Coinbase, Kraken, and RTDS (Binance/Chainlink)."""
from __future__ import annotations

import glob
import warnings
from decimal import Decimal

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

DATA_DIR = "data/rounds"
PM_DIR = f"{DATA_DIR}/polymarket"

KALSHI_COINS = {
    "KXBTC15M": "BTC",
    "KXETH15M": "ETH",
    "KXSOL15M": "SOL",
    "KXXRP15M": "XRP",
}

# ── Loaders ──────────────────────────────────────────────────────────────

def load_kalshi() -> pd.DataFrame:
    frames = []
    for prefix, coin in KALSHI_COINS.items():
        files = sorted(glob.glob(f"{DATA_DIR}/{prefix}-*.csv"))
        for f in files:
            df = pd.read_csv(f)
            df["coin"] = coin
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["spot_price"] = pd.to_numeric(df["spot_price"], errors="coerce")
    df["kraken_spot"] = pd.to_numeric(df["kraken_spot"], errors="coerce")
    return df


def load_polymarket() -> pd.DataFrame:
    files = sorted(glob.glob(f"{PM_DIR}/*.csv"))
    frames = []
    for f in files:
        try:
            df = pd.read_csv(f, on_bad_lines="skip")
        except Exception as e:
            print(f"  WARN: skipping {f}: {e}")
            continue
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    for col in ["spot_price", "kraken_price", "rtds_price"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ── Divergence stats ─────────────────────────────────────────────────────

def divergence_stats(series_a: pd.Series, series_b: pd.Series, ref_price: pd.Series, label_a: str, label_b: str):
    """Compute divergence stats between two price series."""
    mask = series_a.notna() & series_b.notna() & ref_price.notna()
    a, b, ref = series_a[mask], series_b[mask], ref_price[mask]
    if len(a) == 0:
        return None

    abs_diff = (a - b).abs()
    bps_diff = (abs_diff / ref) * 10_000  # basis points

    return {
        "n_rows": len(a),
        "mean_abs_$": abs_diff.mean(),
        "median_abs_$": abs_diff.median(),
        "p95_abs_$": abs_diff.quantile(0.95),
        "p99_abs_$": abs_diff.quantile(0.99),
        "max_abs_$": abs_diff.max(),
        "mean_bps": bps_diff.mean(),
        "median_bps": bps_diff.median(),
        "p95_bps": bps_diff.quantile(0.95),
        "p99_bps": bps_diff.quantile(0.99),
        "max_bps": bps_diff.max(),
    }


def print_stats(stats: dict, label: str):
    if stats is None:
        print(f"  {label}: NO DATA")
        return
    print(f"  {label} ({stats['n_rows']:,} rows):")
    print(f"    Absolute $:  mean={stats['mean_abs_$']:.4f}  median={stats['median_abs_$']:.4f}  p95={stats['p95_abs_$']:.4f}  p99={stats['p99_abs_$']:.4f}  max={stats['max_abs_$']:.4f}")
    print(f"    Basis pts:   mean={stats['mean_bps']:.2f}  median={stats['median_bps']:.2f}  p95={stats['p95_bps']:.2f}  p99={stats['p99_bps']:.2f}  max={stats['max_bps']:.2f}")


# ── Direction disagreement (>10bps) ──────────────────────────────────────

def direction_disagreement_pct(series_a: pd.Series, series_b: pd.Series, ref_price: pd.Series, threshold_bps: float = 10.0):
    """How often do two sources disagree on direction of move from reference by > threshold_bps?"""
    mask = series_a.notna() & series_b.notna() & ref_price.notna()
    a, b, ref = series_a[mask].values, series_b[mask].values, ref_price[mask].values
    if len(a) < 2:
        return None, 0

    # Compute move from previous value
    move_a = np.diff(a)
    move_b = np.diff(b)
    ref_mid = ref[1:]

    # Only consider where both moved meaningfully (> threshold_bps)
    bps_a = np.abs(move_a) / ref_mid * 10_000
    bps_b = np.abs(move_b) / ref_mid * 10_000
    both_meaningful = (bps_a > threshold_bps) & (bps_b > threshold_bps)

    if both_meaningful.sum() == 0:
        return 0.0, 0

    # Among meaningful moves, how often do they disagree on direction?
    disagree = (np.sign(move_a) != np.sign(move_b)) & both_meaningful
    n_meaningful = int(both_meaningful.sum())
    pct = disagree.sum() / n_meaningful * 100
    return pct, n_meaningful


# ── Resolution disagreement (PM only) ───────────────────────────────────

def resolution_disagreement(pm_df: pd.DataFrame, coin: str):
    """At round_end, does RTDS vs Coinbase disagree on up/down from round start?"""
    cdf = pm_df[pm_df["coin"] == coin].copy()
    if cdf.empty:
        return None

    # Group by slug (each slug is a unique round)
    results = []
    for slug, grp in cdf.groupby("slug"):
        snaps = grp[grp["row_type"] == "snapshot"].sort_values("timestamp")
        ends = grp[grp["row_type"] == "round_end"]
        if snaps.empty or ends.empty:
            continue

        # First snapshot = starting prices
        first = snaps.iloc[0]
        end = ends.iloc[0]

        start_cb = first["spot_price"]
        start_rtds = first["rtds_price"]
        end_cb = end["spot_price"]
        end_rtds = end["rtds_price"]

        if pd.isna(start_cb) or pd.isna(end_cb) or pd.isna(start_rtds) or pd.isna(end_rtds):
            continue

        # Coinbase says up/down, RTDS says up/down
        cb_up = end_cb >= start_cb
        rtds_up = end_rtds >= start_rtds
        results.append({
            "slug": slug,
            "cb_up": cb_up,
            "rtds_up": rtds_up,
            "disagree": cb_up != rtds_up,
            "cb_move_bps": (end_cb - start_cb) / start_cb * 10_000,
            "rtds_move_bps": (end_rtds - start_rtds) / start_rtds * 10_000,
        })

    if not results:
        return None
    rdf = pd.DataFrame(results)
    return {
        "total_rounds": len(rdf),
        "disagree_count": int(rdf["disagree"].sum()),
        "disagree_pct": rdf["disagree"].mean() * 100,
        "avg_cb_move_bps": rdf["cb_move_bps"].abs().mean(),
        "avg_rtds_move_bps": rdf["rtds_move_bps"].abs().mean(),
    }


# ── Staleness check ─────────────────────────────────────────────────────

def staleness_pct(series: pd.Series) -> float | None:
    """% of consecutive snapshots with identical price."""
    s = series.dropna()
    if len(s) < 2:
        return None
    same = (s.values[1:] == s.values[:-1]).sum()
    return same / (len(s) - 1) * 100


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print("Loading data...")
    kalshi = load_kalshi()
    pm = load_polymarket()

    kalshi_snap = kalshi[kalshi["row_type"] == "snapshot"] if not kalshi.empty else kalshi
    pm_snap = pm[pm["row_type"] == "snapshot"] if not pm.empty else pm

    print(f"  Kalshi snapshots: {len(kalshi_snap):,}")
    print(f"  Polymarket snapshots: {len(pm_snap):,}")

    coins = ["BTC", "ETH", "SOL", "XRP"]

    # ── 1. Coinbase vs Kraken ────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("1. COINBASE vs KRAKEN DIVERGENCE")
    print("=" * 80)

    print("\n--- Kalshi Platform ---")
    for coin in coins:
        cdf = kalshi_snap[kalshi_snap["coin"] == coin]
        stats = divergence_stats(cdf["spot_price"], cdf["kraken_spot"], cdf["spot_price"], "Coinbase", "Kraken")
        print_stats(stats, f"{coin}")
        pct, n = direction_disagreement_pct(cdf["spot_price"], cdf["kraken_spot"], cdf["spot_price"])
        if pct is not None:
            print(f"    Direction disagree (>10bps): {pct:.2f}% of {n:,} meaningful moves")

    print("\n--- Polymarket Platform ---")
    for coin in coins:
        cdf = pm_snap[pm_snap["coin"] == coin]
        stats = divergence_stats(cdf["spot_price"], cdf["kraken_price"], cdf["spot_price"], "Coinbase", "Kraken")
        print_stats(stats, f"{coin}")
        pct, n = direction_disagreement_pct(cdf["spot_price"], cdf["kraken_price"], cdf["spot_price"])
        if pct is not None:
            print(f"    Direction disagree (>10bps): {pct:.2f}% of {n:,} meaningful moves")

    # ── 2. RTDS vs Coinbase (PM only) ────────────────────────────────────
    print("\n" + "=" * 80)
    print("2. RTDS (Binance/Chainlink) vs COINBASE DIVERGENCE — Polymarket only")
    print("=" * 80)

    for coin in coins:
        cdf = pm_snap[pm_snap["coin"] == coin]
        stats = divergence_stats(cdf["rtds_price"], cdf["spot_price"], cdf["spot_price"], "RTDS", "Coinbase")
        print_stats(stats, f"{coin}")
        pct, n = direction_disagreement_pct(cdf["rtds_price"], cdf["spot_price"], cdf["spot_price"])
        if pct is not None:
            print(f"    Direction disagree (>10bps): {pct:.2f}% of {n:,} meaningful moves")

    print("\n  --- Resolution Disagreement (RTDS vs Coinbase at round boundaries) ---")
    for coin in coins:
        res = resolution_disagreement(pm, coin)
        if res is None:
            print(f"  {coin}: NO DATA")
        else:
            print(f"  {coin}: {res['disagree_count']}/{res['total_rounds']} rounds disagree ({res['disagree_pct']:.1f}%)")
            print(f"    Avg |Coinbase move|: {res['avg_cb_move_bps']:.1f} bps,  Avg |RTDS move|: {res['avg_rtds_move_bps']:.1f} bps")

    # ── 3. RTDS vs Kraken (PM only) ──────────────────────────────────────
    print("\n" + "=" * 80)
    print("3. RTDS (Binance/Chainlink) vs KRAKEN DIVERGENCE — Polymarket only")
    print("=" * 80)

    for coin in coins:
        cdf = pm_snap[pm_snap["coin"] == coin]
        stats = divergence_stats(cdf["rtds_price"], cdf["kraken_price"], cdf["kraken_price"], "RTDS", "Kraken")
        print_stats(stats, f"{coin}")
        pct, n = direction_disagreement_pct(cdf["rtds_price"], cdf["kraken_price"], cdf["kraken_price"])
        if pct is not None:
            print(f"    Direction disagree (>10bps): {pct:.2f}% of {n:,} meaningful moves")

    # ── 4. Staleness check ───────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("4. STALENESS CHECK (% consecutive snapshots with identical price)")
    print("=" * 80)

    print("\n--- Kalshi ---")
    print(f"  {'Coin':<6} {'Coinbase':>10} {'Kraken':>10}")
    for coin in coins:
        cdf = kalshi_snap[kalshi_snap["coin"] == coin]
        cb = staleness_pct(cdf["spot_price"])
        kr = staleness_pct(cdf["kraken_spot"])
        cb_s = f"{cb:.1f}%" if cb is not None else "N/A"
        kr_s = f"{kr:.1f}%" if kr is not None else "N/A"
        print(f"  {coin:<6} {cb_s:>10} {kr_s:>10}")

    print("\n--- Polymarket ---")
    print(f"  {'Coin':<6} {'Coinbase':>10} {'Kraken':>10} {'RTDS':>10}")
    for coin in coins:
        cdf = pm_snap[pm_snap["coin"] == coin]
        cb = staleness_pct(cdf["spot_price"])
        kr = staleness_pct(cdf["kraken_price"])
        rt = staleness_pct(cdf["rtds_price"])
        cb_s = f"{cb:.1f}%" if cb is not None else "N/A"
        kr_s = f"{kr:.1f}%" if kr is not None else "N/A"
        rt_s = f"{rt:.1f}%" if rt is not None else "N/A"
        print(f"  {coin:<6} {cb_s:>10} {kr_s:>10} {rt_s:>10}")

    # ── Per-duration breakdown for PM staleness ──────────────────────────
    print("\n--- Polymarket Staleness by Duration ---")
    if not pm_snap.empty and "duration" in pm_snap.columns:
        durations = sorted(pm_snap["duration"].dropna().unique())
        print(f"  {'Coin':<6} {'Duration':<10} {'Coinbase':>10} {'Kraken':>10} {'RTDS':>10} {'Rows':>8}")
        for coin in coins:
            for dur in durations:
                cdf = pm_snap[(pm_snap["coin"] == coin) & (pm_snap["duration"] == dur)]
                if cdf.empty:
                    continue
                cb = staleness_pct(cdf["spot_price"])
                kr = staleness_pct(cdf["kraken_price"])
                rt = staleness_pct(cdf["rtds_price"])
                cb_s = f"{cb:.1f}%" if cb is not None else "N/A"
                kr_s = f"{kr:.1f}%" if kr is not None else "N/A"
                rt_s = f"{rt:.1f}%" if rt is not None else "N/A"
                print(f"  {coin:<6} {dur:<10} {cb_s:>10} {kr_s:>10} {rt_s:>10} {len(cdf):>8,}")

    print("\nDone.")


if __name__ == "__main__":
    main()
