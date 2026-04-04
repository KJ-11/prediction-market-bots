"""Comprehensive strategy discovery: let the data tell us what works.

Four analyses in one script:
1. Calibration table — market efficiency test (price vs actual resolution rate)
2. Spot trajectory ML — random forest on price features
3. KL divergence — 5m vs 15m PM pricing disagreement
4. Time-of-day spread/pricing patterns
"""
from __future__ import annotations

import glob
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, roc_auc_score

warnings.filterwarnings("ignore")

DATA_KX = Path("data/rounds")
DATA_PM = Path("data/rounds/polymarket")
COINS = ["BTC", "ETH", "SOL"]
RNG = np.random.default_rng(42)
N_BOOTSTRAP = 1_000


def kalshi_fee(price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return math.ceil(0.07 * price * (1 - price) * 100) / 100


def bootstrap_ci(arr: np.ndarray, n: int = N_BOOTSTRAP, ci: float = 0.95):
    if len(arr) < 3:
        return float("nan"), float("nan"), float("nan")
    means = np.array([arr[RNG.integers(0, len(arr), size=len(arr))].mean() for _ in range(n)])
    alpha = (1 - ci) / 2
    lo, hi = np.quantile(means, [alpha, 1 - alpha])
    return float(arr.mean()), float(lo), float(hi)


# ── Data Loading ─────────────────────────────────────────────────────

def load_kalshi(coin: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(DATA_KX / f"KX{coin}15M-*.csv")))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_csv(f, on_bad_lines="skip") for f in files]
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
    for col in ["yes_bid", "yes_ask", "no_bid", "no_ask",
                "seconds_remaining", "spot_price", "strike",
                "kraken_spot", "volume", "spot_move_pct"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_pm(coin: str, duration: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(DATA_PM / f"{coin}-{duration}-*.csv")))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_csv(f, on_bad_lines="skip") for f in files]
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
    for col in ["up_bid", "up_ask", "down_bid", "down_ask",
                "seconds_remaining", "spot_price", "spread"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "end_date" in df.columns:
        df["end_date"] = pd.to_datetime(df["end_date"], utc=True)
    return df


def get_kalshi_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    ends = df[df["row_type"] == "round_end"].drop_duplicates("round_ticker", keep="last")
    result = ends[ends["outcome"].isin(["yes", "no"])][["round_ticker", "outcome"]].copy()
    result = result.rename(columns={"outcome": "final_outcome"})
    return result


# ═════════════════════════════════════════════════════════════════════
# 1. CALIBRATION TABLE
# ═════════════════════════════════════════════════════════════════════

def analyze_calibration(all_data: dict[str, pd.DataFrame]):
    """At a given price and time, how often does YES actually resolve?

    If market says 65% (yes_ask=0.65) at SR=600, does YES win 65% of the time?
    If reality is 72%, that's a 7% mispricing we can exploit.
    """
    print("=" * 90)
    print("1. CALIBRATION TABLE — Market Efficiency Test")
    print("   Does the market price accurately reflect resolution probability?")
    print("=" * 90)

    # Price bins and time bins
    price_bins = [(0.30, 0.40), (0.40, 0.50), (0.50, 0.55), (0.55, 0.60),
                  (0.60, 0.65), (0.65, 0.70), (0.70, 0.75), (0.75, 0.80),
                  (0.80, 0.90)]
    time_bins = [
        ("SR 800-900", 800, 900),  # first 0-100s
        ("SR 600-700", 600, 700),  # 200-300s in
        ("SR 400-500", 400, 500),  # 400-500s in
        ("SR 200-300", 200, 300),  # 600-700s in
        ("SR 50-150", 50, 150),    # last 1-2 min
    ]

    for coin in COINS:
        df = all_data.get(coin)
        if df is None or df.empty:
            continue

        outcomes = get_kalshi_outcomes(df)
        snapshots = df[df["row_type"] == "snapshot"].copy()
        snapshots = snapshots.drop(columns=["outcome"], errors="ignore")
        snapshots = snapshots.merge(outcomes, on="round_ticker", how="inner")
        snapshots["yes_win"] = (snapshots["final_outcome"] == "yes").astype(int)

        # Compute midprice
        snapshots["mid"] = (snapshots["yes_bid"] + snapshots["yes_ask"]) / 2
        snapshots = snapshots.dropna(subset=["mid", "seconds_remaining"])

        print(f"\n  {'─' * 80}")
        print(f"  {coin} — Calibration: market_price vs actual_YES_rate")
        print(f"  {'─' * 80}")

        for time_label, sr_lo, sr_hi in time_bins:
            time_slice = snapshots[
                (snapshots["seconds_remaining"] >= sr_lo) &
                (snapshots["seconds_remaining"] <= sr_hi)
            ]
            if time_slice.empty:
                continue

            # One observation per round per time bin (take median mid)
            per_round = time_slice.groupby("round_ticker").agg(
                mid=("mid", "median"),
                yes_win=("yes_win", "first"),
            ).reset_index()

            print(f"\n    {time_label} (n={len(per_round)} rounds):")
            print(f"    {'Price Bin':<14} {'N':>5} {'Mkt Price':>10} {'Actual YES%':>12} "
                  f"{'Mispricing':>11} {'95% CI':>22}")
            print(f"    {'─' * 78}")

            for p_lo, p_hi in price_bins:
                subset = per_round[(per_round["mid"] >= p_lo) & (per_round["mid"] < p_hi)]
                n = len(subset)
                if n < 10:
                    continue

                mkt_price = subset["mid"].mean()
                actual_rate = subset["yes_win"].mean()
                mispricing = actual_rate - mkt_price
                _, ci_lo, ci_hi = bootstrap_ci(subset["yes_win"].values)
                mis_lo = ci_lo - mkt_price
                mis_hi = ci_hi - mkt_price

                flag = " ***" if abs(mispricing) > 0.05 and n >= 20 else ""
                print(f"    ${p_lo:.2f}-{p_hi:.2f}   {n:>5} "
                      f"    ${mkt_price:.3f}      {actual_rate:>6.1%} "
                      f"    {mispricing:>+6.1%} [{mis_lo:>+.1%}, {mis_hi:>+.1%}]{flag}")

    # Summary: find the biggest mispricings across all coins
    print(f"\n  {'=' * 80}")
    print("  BIGGEST MISPRICINGS (|mispricing| > 5%, N >= 20)")
    print(f"  {'=' * 80}")

    rows = []
    for coin in COINS:
        df = all_data.get(coin)
        if df is None or df.empty:
            continue
        outcomes = get_kalshi_outcomes(df)
        snapshots = df[df["row_type"] == "snapshot"].copy()
        snapshots = snapshots.drop(columns=["outcome"], errors="ignore")
        snapshots = snapshots.merge(outcomes, on="round_ticker", how="inner")
        snapshots["yes_win"] = (snapshots["final_outcome"] == "yes").astype(int)
        snapshots["mid"] = (snapshots["yes_bid"] + snapshots["yes_ask"]) / 2
        snapshots = snapshots.dropna(subset=["mid", "seconds_remaining"])

        for time_label, sr_lo, sr_hi in time_bins:
            time_slice = snapshots[
                (snapshots["seconds_remaining"] >= sr_lo) &
                (snapshots["seconds_remaining"] <= sr_hi)
            ]
            if time_slice.empty:
                continue
            per_round = time_slice.groupby("round_ticker").agg(
                mid=("mid", "median"),
                yes_win=("yes_win", "first"),
            ).reset_index()

            for p_lo, p_hi in price_bins:
                subset = per_round[(per_round["mid"] >= p_lo) & (per_round["mid"] < p_hi)]
                n = len(subset)
                if n < 20:
                    continue
                mkt_price = subset["mid"].mean()
                actual_rate = subset["yes_win"].mean()
                mispricing = actual_rate - mkt_price
                if abs(mispricing) > 0.05:
                    _, ci_lo, ci_hi = bootstrap_ci(subset["yes_win"].values)
                    rows.append({
                        "coin": coin, "time": time_label,
                        "price_bin": f"${p_lo:.2f}-{p_hi:.2f}",
                        "n": n, "mkt": mkt_price, "actual": actual_rate,
                        "mispricing": mispricing,
                        "ci_lo": ci_lo - mkt_price, "ci_hi": ci_hi - mkt_price,
                    })

    if rows:
        rows.sort(key=lambda x: abs(x["mispricing"]), reverse=True)
        print(f"\n  {'Coin':<5} {'Time':<14} {'Price':<14} {'N':>5} "
              f"{'Mkt':>6} {'Actual':>7} {'Mis':>7} {'CI':>22}")
        print(f"  {'─' * 85}")
        for r in rows[:20]:
            print(f"  {r['coin']:<5} {r['time']:<14} {r['price_bin']:<14} {r['n']:>5} "
                  f"{r['mkt']:>5.1%} {r['actual']:>6.1%} {r['mispricing']:>+6.1%} "
                  f"[{r['ci_lo']:>+.1%}, {r['ci_hi']:>+.1%}]")
    else:
        print("\n  No mispricings > 5% found with N >= 20.")

    # Tradeable edge from mispricing
    print(f"\n  {'=' * 80}")
    print("  TRADEABLE EDGE FROM MISPRICING")
    print("  If actual > market, buy YES at ask. If actual < market, buy NO.")
    print(f"  {'=' * 80}")

    for coin in COINS:
        df = all_data.get(coin)
        if df is None or df.empty:
            continue
        outcomes = get_kalshi_outcomes(df)
        snapshots = df[df["row_type"] == "snapshot"].copy()
        snapshots = snapshots.drop(columns=["outcome"], errors="ignore")
        snapshots = snapshots.merge(outcomes, on="round_ticker", how="inner")
        snapshots["yes_win"] = (snapshots["final_outcome"] == "yes").astype(int)
        snapshots = snapshots.dropna(subset=["yes_ask", "yes_bid", "seconds_remaining"])

        # Strategy: at SR~600, if historical actual > market mid, buy YES at ask
        sr_window = snapshots[
            (snapshots["seconds_remaining"] >= 550) &
            (snapshots["seconds_remaining"] <= 650)
        ]
        if sr_window.empty:
            continue

        per_round = sr_window.groupby("round_ticker").agg(
            yes_ask=("yes_ask", "median"),
            yes_bid=("yes_bid", "median"),
            mid=("mid" if "mid" in sr_window.columns else "yes_bid", "median"),
            yes_win=("yes_win", "first"),
        ).reset_index()

        # Recompute mid
        per_round["mid"] = (per_round["yes_bid"] + per_round["yes_ask"]) / 2

        # Simple calibration strategy: buy YES when mid < 0.50 (underpriced YES)
        # and buy NO when mid > 0.50 (underpriced NO)
        # But more specifically: use price bins where we found mispricing

        # Generic: buy YES when mid is in a bin where actual > mid + fee
        per_round["fee"] = per_round["yes_ask"].apply(kalshi_fee)
        per_round["pnl_yes"] = per_round.apply(
            lambda r: (1 - r["yes_ask"] - r["fee"]) if r["yes_win"] == 1
            else (-r["yes_ask"] - r["fee"]),
            axis=1
        )
        per_round["pnl_no"] = per_round.apply(
            lambda r: (1 - (1 - r["yes_bid"]) - kalshi_fee(1 - r["yes_bid"]))
            if r["yes_win"] == 0
            else (-(1 - r["yes_bid"]) - kalshi_fee(1 - r["yes_bid"])),
            axis=1
        )

        # "Always buy YES" baseline
        n_total = len(per_round)
        yes_ev = per_round["pnl_yes"].mean()
        no_ev = per_round["pnl_no"].mean()
        _, yes_lo, yes_hi = bootstrap_ci(per_round["pnl_yes"].values)
        _, no_lo, no_hi = bootstrap_ci(per_round["pnl_no"].values)

        print(f"\n    {coin} at SR 550-650 (n={n_total}):")
        print(f"      Always YES: EV ${yes_ev:+.4f} [{yes_lo:+.4f}, {yes_hi:+.4f}]")
        print(f"      Always NO:  EV ${no_ev:+.4f} [{no_lo:+.4f}, {no_hi:+.4f}]")

        # Mid-biased: buy YES when mid < 0.50, buy NO when mid > 0.50
        low_mid = per_round[per_round["mid"] < 0.50]
        high_mid = per_round[per_round["mid"] > 0.50]
        if len(low_mid) > 10 and len(high_mid) > 10:
            combo_pnl = np.concatenate([low_mid["pnl_yes"].values, high_mid["pnl_no"].values])
            mean_c, lo_c, hi_c = bootstrap_ci(combo_pnl)
            print(f"      Contrarian (YES when mid<0.50, NO when mid>0.50):")
            print(f"        n={len(combo_pnl)}, EV ${mean_c:+.4f} [{lo_c:+.4f}, {hi_c:+.4f}]")

    print()


# ═════════════════════════════════════════════════════════════════════
# 2. SPOT TRAJECTORY ML
# ═════════════════════════════════════════════════════════════════════

def extract_ml_features(df: pd.DataFrame) -> pd.DataFrame:
    """Extract per-round features from Kalshi snapshot data.

    Features extracted at different time windows:
    - Spot momentum (1min, 3min, 5min returns)
    - Spot volatility (std of 1-sec returns)
    - Distance from strike (normalized)
    - Book pressure (yes_bid / (yes_bid + no_bid))
    - Volume
    - Spread
    - Price level (yes midprice)
    """
    outcomes = get_kalshi_outcomes(df)
    snapshots = df[df["row_type"] == "snapshot"].copy()
    snapshots = snapshots.drop(columns=["outcome"], errors="ignore")
    snapshots = snapshots.dropna(subset=["spot_price", "seconds_remaining", "strike"])

    records = []
    for ticker, grp in snapshots.groupby("round_ticker"):
        out_row = outcomes[outcomes["round_ticker"] == ticker]
        if out_row.empty:
            continue
        outcome = out_row["final_outcome"].iloc[0]

        grp = grp.sort_values("seconds_remaining", ascending=False)
        max_sr = grp["seconds_remaining"].max()
        if max_sr < 800:
            continue  # need near-full round data

        # Extract features at multiple checkpoints
        for checkpoint_sr, label in [(600, "sr600"), (450, "sr450"), (300, "sr300")]:
            # Get snapshot window around checkpoint
            window = grp[
                (grp["seconds_remaining"] >= checkpoint_sr - 10) &
                (grp["seconds_remaining"] <= checkpoint_sr + 10)
            ]
            if window.empty:
                continue

            row = window.iloc[len(window) // 2]  # middle row
            spot = row["spot_price"]
            strike = row["strike"]

            if pd.isna(spot) or pd.isna(strike) or strike == 0:
                continue

            # Spot trajectory features
            # Get spot prices at various lookback windows
            spots_1min = grp[
                (grp["seconds_remaining"] >= checkpoint_sr) &
                (grp["seconds_remaining"] <= checkpoint_sr + 60)
            ]["spot_price"]

            spots_3min = grp[
                (grp["seconds_remaining"] >= checkpoint_sr) &
                (grp["seconds_remaining"] <= checkpoint_sr + 180)
            ]["spot_price"]

            spots_5min = grp[
                (grp["seconds_remaining"] >= checkpoint_sr) &
                (grp["seconds_remaining"] <= checkpoint_sr + 300)
            ]["spot_price"]

            if len(spots_1min) < 5 or len(spots_3min) < 10:
                continue

            spot_start_1m = spots_1min.iloc[-1] if len(spots_1min) > 0 else spot
            spot_start_3m = spots_3min.iloc[-1] if len(spots_3min) > 0 else spot
            spot_start_5m = spots_5min.iloc[-1] if len(spots_5min) > 0 else spot

            # Momentum features (returns)
            mom_1m = (spot - spot_start_1m) / strike if spot_start_1m else 0
            mom_3m = (spot - spot_start_3m) / strike if spot_start_3m else 0
            mom_5m = (spot - spot_start_5m) / strike if spot_start_5m else 0

            # Volatility (std of 1-sec returns over last 1 min)
            spot_series = spots_1min.values
            if len(spot_series) > 2:
                returns = np.diff(spot_series) / spot_series[:-1]
                vol_1m = np.std(returns) if len(returns) > 1 else 0
            else:
                vol_1m = 0

            # Distance from strike
            dist = (spot - strike) / strike

            # Book features
            yes_bid = row.get("yes_bid", np.nan)
            yes_ask = row.get("yes_ask", np.nan)
            mid = (yes_bid + yes_ask) / 2 if pd.notna(yes_bid) and pd.notna(yes_ask) else np.nan
            spread = (yes_ask - yes_bid) if pd.notna(yes_bid) and pd.notna(yes_ask) else np.nan

            # Volume
            vol = row.get("volume", 0)

            # Kraken vs Coinbase divergence
            kraken = row.get("kraken_spot", np.nan)
            if pd.notna(kraken) and kraken > 0:
                exchange_div = (spot - kraken) / strike
            else:
                exchange_div = 0

            # Timestamp features
            ts = row.get("timestamp")
            hour_utc = ts.hour if pd.notna(ts) else 12

            rec = {
                "ticker": ticker,
                "checkpoint": label,
                "final_outcome": 1 if outcome == "yes" else 0,
                # Spot features
                "mom_1m": mom_1m,
                "mom_3m": mom_3m,
                "mom_5m": mom_5m,
                "vol_1m": vol_1m,
                "dist_from_strike": dist,
                # Book features
                "mid": mid,
                "spread": spread,
                "volume": vol,
                # Cross-exchange
                "exchange_div": exchange_div,
                # Time
                "hour_utc": hour_utc,
                "sr": checkpoint_sr,
            }
            records.append(rec)

    return pd.DataFrame(records)


def analyze_ml(all_data: dict[str, pd.DataFrame]):
    """Train random forest on spot trajectory features, test out-of-sample."""
    print("\n" + "=" * 90)
    print("2. SPOT TRAJECTORY ML — Random Forest on Price Features")
    print("   Non-overlapping train/test split. Features: momentum, vol, distance, book.")
    print("=" * 90)

    for coin in COINS:
        df = all_data.get(coin)
        if df is None or df.empty:
            continue

        features = extract_ml_features(df)
        if features.empty:
            print(f"\n  {coin}: no features extracted")
            continue

        for checkpoint in ["sr600", "sr450", "sr300"]:
            cp_data = features[features["checkpoint"] == checkpoint].copy()
            if len(cp_data) < 100:
                continue

            # Features
            feat_cols = ["mom_1m", "mom_3m", "mom_5m", "vol_1m",
                         "dist_from_strike", "mid", "spread", "volume",
                         "exchange_div", "hour_utc"]

            cp_data = cp_data.dropna(subset=feat_cols + ["final_outcome"])
            if len(cp_data) < 100:
                continue

            X = cp_data[feat_cols].values
            y = cp_data["final_outcome"].values

            # Non-overlapping split: first 70% train, last 30% test
            split = int(len(X) * 0.7)
            X_train, X_test = X[:split], X[split:]
            y_train, y_test = y[:split], y[split:]

            if len(X_test) < 30 or len(np.unique(y_train)) < 2:
                continue

            # Train random forest
            rf = RandomForestClassifier(
                n_estimators=200, max_depth=5, min_samples_leaf=20,
                random_state=42, n_jobs=-1,
            )
            rf.fit(X_train, y_train)

            # Evaluate
            y_pred = rf.predict(X_test)
            y_prob = rf.predict_proba(X_test)[:, 1]
            acc = accuracy_score(y_test, y_pred)
            try:
                auc = roc_auc_score(y_test, y_prob)
            except ValueError:
                auc = 0.5

            baseline = max(y_test.mean(), 1 - y_test.mean())

            # Feature importance
            importances = sorted(
                zip(feat_cols, rf.feature_importances_),
                key=lambda x: x[1], reverse=True,
            )

            print(f"\n  {coin} @ {checkpoint} "
                  f"(train={len(X_train)}, test={len(X_test)}):")
            print(f"    Accuracy:  {acc:.1%} (baseline: {baseline:.1%}, "
                  f"lift: {acc - baseline:+.1%})")
            print(f"    AUC:       {auc:.3f} (0.5 = random)")
            print(f"    Top features: "
                  + ", ".join(f"{name} ({imp:.3f})" for name, imp in importances[:5]))

            # Simulated trading: only trade when model is confident
            for threshold in [0.60, 0.65, 0.70]:
                confident_yes = y_prob >= threshold
                confident_no = y_prob <= (1 - threshold)

                trades_idx = confident_yes | confident_no
                if trades_idx.sum() < 10:
                    continue

                # Get the actual test rows for pricing
                test_data = cp_data.iloc[split:].copy()
                test_data = test_data[trades_idx.flatten() if hasattr(trades_idx, 'flatten') else trades_idx]

                if len(test_data) < 10:
                    continue

                # Simulate: buy YES when prob > threshold, NO when prob < (1-threshold)
                test_probs = y_prob[trades_idx]
                test_outcomes = y_test[trades_idx]
                test_mids = test_data["mid"].values

                pnls = []
                for prob, actual, mid_price in zip(test_probs, test_outcomes, test_mids):
                    if pd.isna(mid_price):
                        continue
                    if prob >= threshold:
                        # Buy YES
                        entry = min(mid_price + 0.02, 0.95)  # ask ~ mid + spread/2
                        fee = kalshi_fee(entry)
                        pnl = (1 - entry - fee) if actual == 1 else (-entry - fee)
                    else:
                        # Buy NO
                        entry = min(1 - mid_price + 0.02, 0.95)
                        fee = kalshi_fee(entry)
                        pnl = (1 - entry - fee) if actual == 0 else (-entry - fee)
                    pnls.append(pnl)

                if len(pnls) < 5:
                    continue

                pnl_arr = np.array(pnls)
                mean_pnl, lo, hi = bootstrap_ci(pnl_arr)
                wr = sum(1 for p in pnls if p > 0) / len(pnls)

                print(f"    Threshold {threshold:.0%}: "
                      f"{len(pnls)} trades, WR={wr:.1%}, "
                      f"EV=${mean_pnl:+.4f} [{lo:+.4f}, {hi:+.4f}]")

    print()


# ═════════════════════════════════════════════════════════════════════
# 3. KL DIVERGENCE (5m vs 15m)
# ═════════════════════════════════════════════════════════════════════

def analyze_kl_divergence():
    """When PM 5m and 15m implied probabilities diverge, which one is right?"""
    print("\n" + "=" * 90)
    print("3. KL DIVERGENCE — 5m vs 15m PM Pricing Disagreement")
    print("   When 5m and 15m markets disagree, which market is correct?")
    print("=" * 90)

    for coin in COINS:
        pm5m = load_pm(coin, "5m")
        pm15m = load_pm(coin, "15m")

        if pm5m.empty or pm15m.empty:
            print(f"\n  {coin}: insufficient PM data")
            continue

        # Get snapshots with valid book
        snap5m = pm5m[
            (pm5m["row_type"] == "snapshot") &
            (pm5m["up_ask"].notna()) & (pm5m["up_ask"] > 0) & (pm5m["up_ask"] < 1)
        ].copy()
        snap15m = pm15m[
            (pm15m["row_type"] == "snapshot") &
            (pm15m["up_ask"].notna()) & (pm15m["up_ask"] > 0) & (pm15m["up_ask"] < 1)
        ].copy()

        if snap5m.empty or snap15m.empty:
            print(f"\n  {coin}: no valid book data")
            continue

        # Compute implied probability (midprice as proxy)
        snap5m["p_up_5m"] = (snap5m["up_bid"] + snap5m["up_ask"]) / 2
        snap15m["p_up_15m"] = (snap15m["up_bid"] + snap15m["up_ask"]) / 2

        # Get 15m outcomes
        out15m = pm15m[pm15m["row_type"] == "round_end"][["slug", "outcome"]].drop_duplicates("slug")
        out15m = out15m[out15m["outcome"].isin(["up", "down"])]

        # Round timestamps to nearest minute for joining
        snap5m["ts_min"] = snap5m["timestamp"].dt.floor("1min")
        snap15m["ts_min"] = snap15m["timestamp"].dt.floor("1min")

        # Join on timestamp (same minute)
        merged = snap5m[["ts_min", "p_up_5m"]].groupby("ts_min").median().reset_index()
        merged_15m = snap15m[["ts_min", "p_up_15m", "slug"]].copy()
        merged_15m = merged_15m.groupby("ts_min").agg(
            p_up_15m=("p_up_15m", "median"),
            slug=("slug", "first"),
        ).reset_index()

        joined = merged.merge(merged_15m, on="ts_min", how="inner")
        joined = joined.merge(out15m, on="slug", how="inner")
        joined["actual_up"] = (joined["outcome"] == "up").astype(int)

        if len(joined) < 50:
            print(f"\n  {coin}: only {len(joined)} joined observations")
            continue

        # KL divergence proxy: |p_5m - p_15m|
        joined["divergence"] = (joined["p_up_5m"] - joined["p_up_15m"]).abs()

        print(f"\n  {coin}: {len(joined)} observations with both 5m and 15m pricing")
        print(f"    Median divergence: {joined['divergence'].median():.4f}")
        print(f"    Mean divergence:   {joined['divergence'].mean():.4f}")

        # When they disagree significantly, which is right?
        div_bins = [
            ("< 0.02", joined[joined["divergence"] < 0.02]),
            ("0.02-0.05", joined[(joined["divergence"] >= 0.02) & (joined["divergence"] < 0.05)]),
            ("0.05-0.10", joined[(joined["divergence"] >= 0.05) & (joined["divergence"] < 0.10)]),
            ("0.10+", joined[joined["divergence"] >= 0.10]),
        ]

        print(f"\n    {'Divergence':<12} {'N':>6} {'5m Right':>9} {'15m Right':>10} "
              f"{'5m Closer':>10}")
        print(f"    {'─' * 55}")

        for label, subset in div_bins:
            n = len(subset)
            if n < 10:
                print(f"    {label:<12} {n:>6}")
                continue

            # "Right" = closer to actual outcome
            # If actual=up, the higher p_up is "more right"
            subset = subset.copy()
            subset["5m_err"] = (subset["p_up_5m"] - subset["actual_up"]).abs()
            subset["15m_err"] = (subset["p_up_15m"] - subset["actual_up"]).abs()
            five_closer = (subset["5m_err"] < subset["15m_err"]).mean()

            # Which market to follow? Buy based on the one that's more extreme
            subset["follow_5m"] = (
                ((subset["p_up_5m"] > subset["p_up_15m"]) & (subset["actual_up"] == 1)) |
                ((subset["p_up_5m"] < subset["p_up_15m"]) & (subset["actual_up"] == 0))
            ).astype(int)

            five_right = subset["follow_5m"].mean()
            fifteen_right = 1 - five_right

            print(f"    {label:<12} {n:>6} {five_right:>8.1%} {fifteen_right:>9.1%} "
                  f"{five_closer:>9.1%}")

        # Actionable test: when 5m says "more up" than 15m, buy UP on 15m
        high_div = joined[joined["divergence"] >= 0.05].copy()
        if len(high_div) > 20:
            # 5m > 15m means 5m thinks "more up" → buy up on 15m
            high_div["signal_up"] = (high_div["p_up_5m"] > high_div["p_up_15m"])
            high_div["correct"] = (
                (high_div["signal_up"] & (high_div["actual_up"] == 1)) |
                (~high_div["signal_up"] & (high_div["actual_up"] == 0))
            ).astype(int)

            wr = high_div["correct"].mean()
            _, ci_lo, ci_hi = bootstrap_ci(high_div["correct"].values)
            print(f"\n    Divergence > 0.05: Follow 5m signal → "
                  f"{wr:.1%} WR [{ci_lo:.1%}, {ci_hi:.1%}] (n={len(high_div)})")

    print()


# ═════════════════════════════════════════════════════════════════════
# 4. TIME-OF-DAY PATTERNS
# ═════════════════════════════════════════════════════════════════════

def analyze_time_patterns(all_data: dict[str, pd.DataFrame]):
    """Spread widening, mispricing, and directional bias by hour."""
    print("\n" + "=" * 90)
    print("4. TIME-OF-DAY PATTERNS — Spread, Volume, and Directional Bias")
    print("   Focus: overnight (11pm-7am ET) vs daytime")
    print("=" * 90)

    for coin in COINS:
        df = all_data.get(coin)
        if df is None or df.empty:
            continue

        outcomes = get_kalshi_outcomes(df)
        snapshots = df[df["row_type"] == "snapshot"].copy()
        snapshots = snapshots.drop(columns=["outcome"], errors="ignore")
        snapshots = snapshots.merge(outcomes, on="round_ticker", how="inner")
        snapshots["yes_win"] = (snapshots["final_outcome"] == "yes").astype(int)
        snapshots["spread"] = snapshots["yes_ask"] - snapshots["yes_bid"]
        snapshots["hour_utc"] = snapshots["timestamp"].dt.hour

        # ET = UTC-4 (EDT)
        snapshots["hour_et"] = (snapshots["hour_utc"] - 4) % 24
        snapshots["is_overnight"] = (
            (snapshots["hour_et"] >= 23) | (snapshots["hour_et"] < 7)
        )

        # One row per round
        per_round = snapshots.groupby("round_ticker").agg(
            spread=("spread", "median"),
            volume=("volume", "max"),
            yes_win=("yes_win", "first"),
            hour_et=("hour_et", "median"),
            is_overnight=("is_overnight", "first"),
            mid=("yes_bid", lambda x: ((x + snapshots.loc[x.index, "yes_ask"]) / 2).median()),
        ).reset_index()
        per_round["hour_et"] = per_round["hour_et"].round().astype(int)

        print(f"\n  {coin}:")

        # Overnight vs daytime
        overnight = per_round[per_round["is_overnight"]]
        daytime = per_round[~per_round["is_overnight"]]

        if len(overnight) > 10 and len(daytime) > 10:
            print(f"    Overnight (11pm-7am ET): {len(overnight)} rounds")
            print(f"      Median spread: ${overnight['spread'].median():.4f}")
            print(f"      YES rate: {overnight['yes_win'].mean():.1%}")
            print(f"    Daytime (7am-11pm ET): {len(daytime)} rounds")
            print(f"      Median spread: ${daytime['spread'].median():.4f}")
            print(f"      YES rate: {daytime['yes_win'].mean():.1%}")
            spread_ratio = overnight["spread"].median() / daytime["spread"].median()
            print(f"    Spread ratio (overnight/day): {spread_ratio:.2f}x")

        # Per-hour breakdown
        print(f"\n    {'Hour(ET)':<10} {'Rounds':>7} {'Spread':>8} {'YES%':>7} "
              f"{'Volume':>8}")
        print(f"    {'─' * 45}")

        for hour in range(24):
            hr_data = per_round[per_round["hour_et"] == hour]
            n = len(hr_data)
            if n < 5:
                continue
            med_spread = hr_data["spread"].median()
            yes_rate = hr_data["yes_win"].mean()
            med_vol = hr_data["volume"].median()
            marker = " *" if (hour >= 23 or hour < 7) else ""
            print(f"    {hour:>4}:00 ET {n:>7} ${med_spread:>6.4f} {yes_rate:>6.1%} "
                  f"{med_vol:>7.0f}{marker}")

    print()


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    print("Loading Kalshi data...")
    all_data: dict[str, pd.DataFrame] = {}
    for coin in COINS:
        df = load_kalshi(coin)
        if not df.empty:
            n_rounds = df[df["row_type"] == "round_end"]["round_ticker"].nunique()
            print(f"  {coin}: {len(df):,} rows, {n_rounds} rounds")
            all_data[coin] = df
        else:
            print(f"  {coin}: no data")

    print()

    analyze_calibration(all_data)
    analyze_ml(all_data)
    analyze_kl_divergence()
    analyze_time_patterns(all_data)

    print("=" * 90)
    print("DONE")
    print("=" * 90)


if __name__ == "__main__":
    main()
