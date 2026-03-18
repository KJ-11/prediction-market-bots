"""
V3 Deep Dive — Two promising leads:
1. ML model for Kalshi (GBM at T+450, p>0.90 → 92.4% WR OOS)
2. PM price-gap trading (Kalshi lower than PM → buy PM DOWN cheap)

For each: stability, realistic execution, failure modes.
"""
from __future__ import annotations

import warnings
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")

PROJECT = Path(__file__).resolve().parent.parent
KALSHI_DIR = PROJECT / "data" / "rounds"
PM_DIR = PROJECT / "data" / "rounds" / "polymarket"
FEE_COEFF = 0.07


def kalshi_fee(price):
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
        df = pd.read_csv(f, engine="python", on_bad_lines="skip")
        df["coin"] = parts[0]
        df["file_duration"] = parts[1]
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    all_df = pd.concat(frames, ignore_index=True)
    all_df["timestamp"] = pd.to_datetime(all_df["timestamp"], format="ISO8601", utc=True)
    for col in ["up_bid", "up_ask", "down_bid", "down_ask", "up_midpoint",
                 "spread", "last_trade_price", "spot_price", "kraken_price",
                 "rtds_price", "volume", "seconds_remaining"]:
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce")
    all_df["actual_up_prob"] = 1.0 - all_df["up_midpoint"]
    return all_df


def extract_features(kalshi, obs_time=450):
    ends = kalshi[kalshi["row_type"] == "round_end"][["round_ticker", "outcome"]].copy()
    ends = ends.drop_duplicates("round_ticker").rename(columns={"outcome": "round_outcome"})
    snaps = kalshi[kalshi["row_type"] == "snapshot"].copy()
    snaps = snaps.merge(ends, on="round_ticker", how="inner")

    obs = snaps[(snaps["seconds_elapsed"] >= obs_time - 5) &
                 (snaps["seconds_elapsed"] <= obs_time + 5)]
    current = obs.sort_values("seconds_elapsed").groupby(
        ["coin", "round_ticker"]).first().reset_index()

    def snap_at(t, label):
        sel = snaps[(snaps["seconds_elapsed"] >= t - 5) & (snaps["seconds_elapsed"] <= t + 5)]
        f = sel.sort_values("seconds_elapsed").groupby(["coin", "round_ticker"]).first().reset_index()
        return f[["coin", "round_ticker", "spot_price", "yes_bid", "yes_ask"]].rename(
            columns={"spot_price": f"spot_{label}", "yes_bid": f"bid_{label}", "yes_ask": f"ask_{label}"})

    for t, label in [(obs_time-30, "30ago"), (obs_time-60, "60ago"),
                      (obs_time-120, "120ago"), (30, "start")]:
        if t >= 25:
            current = current.merge(snap_at(t, label), on=["coin", "round_ticker"], how="left")

    history = snaps[snaps["seconds_elapsed"] <= obs_time]
    rv = history.groupby(["coin", "round_ticker"]).agg(
        intra_vol=("spot_move_pct", "std"),
        spot_range_raw=("spot_price", lambda x: x.max() - x.min()),
    )
    current = current.merge(rv, on=["coin", "round_ticker"], how="left")

    hd = history.copy()
    hd["abs_dist_h"] = (hd["spot_price"] - hd["strike"]).abs() / hd["strike"]
    md = hd.groupby(["coin", "round_ticker"])["abs_dist_h"].max().rename("max_dist_seen")
    current = current.merge(md, on=["coin", "round_ticker"], how="left")

    def count_crosses(g):
        return (g["spot_price"] > g["strike"]).astype(int).diff().abs().sum() / 2
    cc = history.groupby(["coin", "round_ticker"]).apply(count_crosses).rename("strike_crosses")
    current = current.merge(cc, on=["coin", "round_ticker"], how="left")

    df = current
    df["pct_dist"] = (df["spot_price"] - df["strike"]) / df["strike"]
    df["abs_dist"] = df["pct_dist"].abs()
    df["spot_above"] = (df["spot_price"] > df["strike"]).astype(int)
    df["yes_mid"] = (df["yes_bid"] + df["yes_ask"]) / 2
    df["yes_mid"] = df["yes_mid"].where((df["yes_bid"] > 0) & (df["yes_ask"] < 1))
    df["yes_spread"] = (df["yes_ask"] - df["yes_bid"]).where((df["yes_bid"] > 0) & (df["yes_ask"] < 1))
    df["spot_book_agree"] = ((df["spot_above"] == 1) & (df["yes_mid"] > 0.5) |
                              (df["spot_above"] == 0) & (df["yes_mid"] < 0.5)).astype(float)
    df["kc_divergence"] = (df["spot_price"] - df["kraken_spot"]).abs() / df["spot_price"]
    df["kc_sign"] = (df["spot_price"] > df["kraken_spot"]).astype(float)

    if "spot_30ago" in df.columns:
        df["mom_30s"] = (df["spot_price"] - df["spot_30ago"]) / df["spot_30ago"]
    if "spot_60ago" in df.columns:
        df["mom_60s"] = (df["spot_price"] - df["spot_60ago"]) / df["spot_60ago"]
    if "spot_120ago" in df.columns:
        df["mom_120s"] = (df["spot_price"] - df["spot_120ago"]) / df["spot_120ago"]
    if "mom_30s" in df.columns and "mom_60s" in df.columns:
        df["accel"] = df["mom_30s"] - (df["mom_60s"] - df["mom_30s"])
    if "bid_60ago" in df.columns and "ask_60ago" in df.columns:
        df["early_mid"] = (df["bid_60ago"] + df["ask_60ago"]) / 2
        df["book_momentum"] = df["yes_mid"] - df["early_mid"]

    df["spot_range"] = df["spot_range_raw"] / df["strike"]
    df["hour"] = df["timestamp"].dt.hour
    df["target"] = (df["round_outcome"] == "yes").astype(int)
    for c in ["BTC", "ETH", "SOL", "XRP"]:
        df[f"coin_{c}"] = (df["coin"] == c).astype(int)

    return df


FEATURE_COLS = [
    "pct_dist", "abs_dist", "spot_above", "yes_mid", "yes_spread",
    "spot_book_agree", "kc_divergence", "kc_sign",
    "mom_30s", "mom_60s", "mom_120s", "accel",
    "intra_vol", "spot_range", "book_momentum",
    "volume", "hour", "strike_crosses", "max_dist_seen",
    "coin_BTC", "coin_ETH", "coin_SOL", "coin_XRP",
]


def part1_ml_deep_dive(kalshi):
    """Deep dive into GBM model for Kalshi trading."""
    out = []
    out.append("=" * 70)
    out.append("PART 1: ML MODEL DEEP DIVE (Kalshi)")
    out.append("=" * 70)

    # Extract at multiple time points for richer analysis
    for obs_t in [300, 450]:
        out.append(f"\n{'='*50}")
        out.append(f"Observation at T+{obs_t}s")
        out.append(f"{'='*50}")

        feat = extract_features(kalshi, obs_t)
        feat = feat.dropna(subset=["pct_dist", "target"])
        fcols = [c for c in FEATURE_COLS if c in feat.columns]

        X = feat[fcols].copy()
        y = feat["target"].values
        for col in X.columns:
            X[col] = X[col].fillna(X[col].median())

        # Time-series split: train on days 1-6, test on days 7-9
        feat_sorted = feat.sort_values("file_date")
        dates = sorted(feat_sorted["file_date"].unique())
        split_date = dates[int(len(dates) * 0.67)]
        train_mask = feat_sorted["file_date"] <= split_date
        test_mask = ~train_mask

        train = feat_sorted[train_mask]
        test = feat_sorted[test_mask]
        X_train = train[fcols].fillna(train[fcols].median())
        X_test = test[fcols].fillna(train[fcols].median())
        y_train, y_test = train["target"].values, test["target"].values

        out.append(f"\nTrain: {len(train)} samples ({train['file_date'].min()} to {train['file_date'].max()})")
        out.append(f"Test: {len(test)} samples ({test['file_date'].min()} to {test['file_date'].max()})")

        gb = GradientBoostingClassifier(n_estimators=200, max_depth=4, learning_rate=0.05,
                                         subsample=0.8, random_state=42)
        gb.fit(X_train, y_train)
        probs = gb.predict_proba(X_test)[:, 1]

        try:
            oos_auc = roc_auc_score(y_test, probs)
            out.append(f"OOS AUC: {oos_auc:.4f}")
        except Exception:
            out.append("OOS AUC: failed")
            continue

        test_df = test.copy()
        test_df["model_prob"] = probs

        # --- 1a. What do high-confidence predictions look like? ---
        out.append(f"\n### What does the model see at p>0.85?\n")
        high_yes = test_df[test_df["model_prob"] > 0.85]
        high_no = test_df[test_df["model_prob"] < 0.15]

        for label, subset, target_val in [("YES bets (p>0.85)", high_yes, 1),
                                           ("NO bets (p<0.15)", high_no, 0)]:
            if len(subset) < 5:
                continue
            wr = (subset["target"] == target_val).mean()
            out.append(f"\n{label}: n={len(subset)}, WR={wr*100:.1f}%")
            out.append(f"  abs_dist: mean={subset['abs_dist'].mean()*100:.3f}%, "
                       f"median={subset['abs_dist'].median()*100:.3f}%")
            out.append(f"  yes_mid: mean={subset['yes_mid'].mean():.3f}, "
                       f"median={subset['yes_mid'].median():.3f}")
            out.append(f"  spot_above: {subset['spot_above'].mean()*100:.0f}%")
            out.append(f"  mom_120s: mean={subset['mom_120s'].mean()*100:.4f}%")
            out.append(f"  intra_vol: mean={subset['intra_vol'].mean():.6f}")
            out.append(f"  strike_crosses: mean={subset['strike_crosses'].mean():.1f}")
            out.append(f"  Coins: {subset['coin'].value_counts().to_dict()}")

            # Entry prices
            if target_val == 1:
                prices = subset["yes_ask"]
            else:
                prices = subset["no_ask"].fillna(1 - subset["yes_bid"])
            prices = prices[prices > 0.01]
            if len(prices) > 0:
                out.append(f"  Entry ask: median=${prices.median():.3f}, mean=${prices.mean():.3f}")

        # --- 1b. Day-by-day OOS performance ---
        out.append(f"\n### Day-by-Day OOS Performance\n")
        for thresh in [0.85, 0.90]:
            out.append(f"\nThreshold: p>{thresh}")
            for date in sorted(test_df["file_date"].unique()):
                day = test_df[test_df["file_date"] == date]
                yes_b = day[day["model_prob"] > thresh]
                no_b = day[day["model_prob"] < (1 - thresh)]
                n = len(yes_b) + len(no_b)
                if n == 0:
                    out.append(f"  {date}: 0 trades")
                    continue

                wins = (yes_b["target"] == 1).sum() + (no_b["target"] == 0).sum()
                wr = wins / n

                # Compute PnL
                pnl = 0
                for _, row in yes_b.iterrows():
                    ask = row["yes_ask"] if not pd.isna(row["yes_ask"]) and row["yes_ask"] > 0.01 else 0.85
                    fee = kalshi_fee(ask)
                    pnl += (1 - ask - fee) if row["target"] == 1 else -(ask + fee)
                for _, row in no_b.iterrows():
                    na = row["no_ask"] if not pd.isna(row.get("no_ask")) and row.get("no_ask", 0) > 0.01 else max(0.1, 1 - row["yes_bid"])
                    fee = kalshi_fee(na)
                    pnl += (1 - na - fee) if row["target"] == 0 else -(na + fee)

                out.append(f"  {date}: {n} trades ({len(yes_b)}Y/{len(no_b)}N), "
                           f"WR={wr*100:.0f}%, PnL=${pnl:.2f}")

        # --- 1c. Does the model add value beyond yes_mid alone? ---
        out.append(f"\n### Model vs Simple Rules\n")

        # Simple rule: bet favored side when |dist| > threshold
        for rule_label, rule_fn in [
            ("dist>0.15%", lambda df: df["abs_dist"] > 0.0015),
            ("dist>0.20%", lambda df: df["abs_dist"] > 0.002),
            ("dist>0.30%", lambda df: df["abs_dist"] > 0.003),
            ("yes_mid>0.85", lambda df: (df["yes_mid"] > 0.85) | (df["yes_mid"] < 0.15)),
            ("GBM p>0.85", lambda df: (df["model_prob"] > 0.85) | (df["model_prob"] < 0.15)),
            ("GBM p>0.90", lambda df: (df["model_prob"] > 0.90) | (df["model_prob"] < 0.10)),
        ]:
            mask = rule_fn(test_df)
            sel = test_df[mask]
            if len(sel) < 5:
                continue

            # Determine bet direction
            if "model_prob" in rule_label:
                yes_mask = sel["model_prob"] > 0.5
            else:
                yes_mask = sel["spot_above"] == 1

            wins = ((yes_mask & (sel["target"] == 1)) | (~yes_mask & (sel["target"] == 0))).sum()
            wr = wins / len(sel)

            # EV
            evs = []
            for i, (_, row) in enumerate(sel.iterrows()):
                is_yes = yes_mask.iloc[i] if hasattr(yes_mask, 'iloc') else yes_mask.values[i]
                if is_yes:
                    ask = row["yes_ask"] if not pd.isna(row["yes_ask"]) and row["yes_ask"] > 0.01 else 0.85
                    fee = kalshi_fee(ask)
                    evs.append((1 - ask - fee) if row["target"] == 1 else -(ask + fee))
                else:
                    na = row.get("no_ask", np.nan)
                    if pd.isna(na) or na <= 0.01:
                        na = max(0.1, 1 - row["yes_bid"]) if not pd.isna(row["yes_bid"]) else 0.85
                    fee = kalshi_fee(na)
                    evs.append((1 - na - fee) if row["target"] == 0 else -(na + fee))

            avg_ev = np.mean(evs)
            marker = "✓" if avg_ev > 0 else " "
            out.append(f"  {marker} {rule_label:20s}: n={len(sel):4d}, WR={wr*100:.1f}%, "
                       f"EV=${avg_ev:.4f}, PnL=${sum(evs):.2f}")

        # --- 1d. What if we combine model + price cap? ---
        out.append(f"\n### GBM + Price Cap (only enter when ask is cheap)\n")
        for thresh in [0.80, 0.85, 0.90]:
            for max_ask in [0.80, 0.85, 0.90, 0.95]:
                yes_b = test_df[(test_df["model_prob"] > thresh) & (test_df["yes_ask"] <= max_ask) & (test_df["yes_ask"] > 0.01)]
                no_b = test_df[(test_df["model_prob"] < (1 - thresh))]
                # For no bets, compute no_ask
                no_b = no_b.copy()
                no_b["no_ask_calc"] = no_b["no_ask"].fillna(1 - no_b["yes_bid"])
                no_b = no_b[(no_b["no_ask_calc"] <= max_ask) & (no_b["no_ask_calc"] > 0.01)]

                n = len(yes_b) + len(no_b)
                if n < 5:
                    continue

                wins = (yes_b["target"] == 1).sum() + (no_b["target"] == 0).sum()
                wr = wins / n

                evs = []
                for _, row in yes_b.iterrows():
                    fee = kalshi_fee(row["yes_ask"])
                    evs.append((1 - row["yes_ask"] - fee) if row["target"] == 1 else -(row["yes_ask"] + fee))
                for _, row in no_b.iterrows():
                    na = row["no_ask_calc"]
                    fee = kalshi_fee(na)
                    evs.append((1 - na - fee) if row["target"] == 0 else -(na + fee))

                avg_ev = np.mean(evs)
                daily = n / test_df["file_date"].nunique()
                marker = "✓" if avg_ev > 0 else " "
                out.append(f"  {marker} p>{thresh}, ask≤${max_ask}: n={n}, WR={wr*100:.1f}%, "
                           f"EV=${avg_ev:.4f}, PnL=${sum(evs):.2f}, {daily:.1f}/day")

    return "\n".join(out)


def part2_pm_deep_dive(kalshi, pm):
    """Deep dive into PM price-gap trading."""
    out = []
    out.append("\n\n" + "=" * 70)
    out.append("PART 2: PM PRICE-GAP TRADING DEEP DIVE")
    out.append("=" * 70)

    k_snaps = kalshi[kalshi["row_type"] == "snapshot"].copy()
    k_snaps["yes_mid"] = (k_snaps["yes_bid"] + k_snaps["yes_ask"]) / 2
    k_snaps["round_time"] = k_snaps["timestamp"].dt.floor("15min")

    pm_15m = pm[pm["file_duration"] == "15m"].copy()
    pm_ends = pm_15m[pm_15m["row_type"].str.contains("end|resolved", case=False, na=False)]
    pm_ends_u = pm_ends[pm_ends["outcome"].isin(["up", "down"])][["slug", "outcome"]].drop_duplicates("slug")
    pm_ends_u = pm_ends_u.rename(columns={"outcome": "pm_outcome"})
    pm_snaps = pm_15m[pm_15m["row_type"] == "snapshot"].copy()
    pm_snaps = pm_snaps.merge(pm_ends_u, on="slug", how="inner")
    pm_snaps["end_dt"] = pd.to_datetime(pm_snaps["end_date"], utc=True)
    pm_snaps["round_time"] = pm_snaps["end_dt"].dt.floor("15min")
    pm_snaps["coin"] = pm_snaps["coin"].str.upper()

    # --- 2a. Validate PM entry prices are real ---
    out.append("\n### 2a. PM Book Quality — Are these prices real?\n")

    for sec_rem in [300, 180]:
        pm_at = pm_snaps[(pm_snaps["seconds_remaining"] >= sec_rem - 15) &
                          (pm_snaps["seconds_remaining"] <= sec_rem + 15)]
        first = pm_at.sort_values("seconds_remaining").groupby(
            ["coin", "round_time"]).first().reset_index()

        out.append(f"\nAt T-{sec_rem}s:")
        out.append(f"  Total round-snapshots: {len(first)}")

        # Check how many have tradeable books on BOTH sides
        # Remember: "up" in data = real DOWN token, "down" in data = real UP token
        quoted_up = first[(first["down_bid"] > 0.05) & (first["down_ask"] < 0.95)]  # real UP
        quoted_down = first[(first["up_bid"] > 0.05) & (first["up_ask"] < 0.95)]  # real DOWN
        quoted_both = first[(first["down_bid"] > 0.05) & (first["down_ask"] < 0.95) &
                             (first["up_bid"] > 0.05) & (first["up_ask"] < 0.95)]

        out.append(f"  Real UP token quoted: {len(quoted_up)} ({100*len(quoted_up)/max(1,len(first)):.0f}%)")
        out.append(f"  Real DOWN token quoted: {len(quoted_down)} ({100*len(quoted_down)/max(1,len(first)):.0f}%)")
        out.append(f"  Both quoted: {len(quoted_both)} ({100*len(quoted_both)/max(1,len(first)):.0f}%)")

        if len(quoted_both) > 0:
            out.append(f"  Real UP spread: ${(quoted_both['down_ask'] - quoted_both['down_bid']).median():.3f} median")
            out.append(f"  Real DOWN spread: ${(quoted_both['up_ask'] - quoted_both['up_bid']).median():.3f} median")
            out.append(f"  Real UP ask: ${quoted_both['down_ask'].median():.3f} median")
            out.append(f"  Real DOWN ask: ${quoted_both['up_ask'].median():.3f} median")

    # --- 2b. Price gap with validated book data ---
    out.append("\n### 2b. Price Gap Trading with Real PM Book Prices\n")
    out.append("Focus: when Kalshi and PM disagree, trade the PM side that Kalshi predicts.\n"
               "Only count trades where PM has a real quoted book.\n")

    for sec_rem in [450, 300, 180]:
        k_at = k_snaps[(k_snaps["seconds_remaining"] >= sec_rem - 15) &
                        (k_snaps["seconds_remaining"] <= sec_rem + 15) &
                        (k_snaps["yes_bid"] > 0) & (k_snaps["yes_ask"] < 1)]
        k_first = k_at.sort_values("seconds_remaining").groupby(
            ["coin", "round_time"]).first().reset_index()

        pm_at = pm_snaps[(pm_snaps["seconds_remaining"] >= sec_rem - 15) &
                          (pm_snaps["seconds_remaining"] <= sec_rem + 15)]
        pm_first = pm_at.sort_values("seconds_remaining").groupby(
            ["coin", "round_time"]).first().reset_index()

        cross = k_first[["coin", "round_time", "yes_mid"]].merge(
            pm_first[["coin", "round_time", "actual_up_prob", "pm_outcome",
                       "up_bid", "up_ask", "down_bid", "down_ask", "volume"]],
            on=["coin", "round_time"], how="inner"
        )
        if len(cross) < 10:
            continue

        cross["k_yes"] = cross["yes_mid"] > 0.5
        cross["pm_up"] = cross["actual_up_prob"] > 0.5
        cross["actual_up"] = cross["pm_outcome"] == "up"
        cross["gap"] = cross["yes_mid"] - cross["actual_up_prob"]

        out.append(f"\n--- T-{sec_rem}s (n={len(cross)} matched) ---\n")

        # Strategy: when gap < -X (Kalshi says NO, PM says UP-ish),
        # buy real DOWN on PM = buy "up" token in our data
        out.append("When Kalshi < PM (buy PM DOWN = buy 'up' token in data):")
        for gap in [0.05, 0.10, 0.15, 0.20]:
            sel = cross[(cross["gap"] < -gap) &
                         (cross["up_bid"] > 0.01) & (cross["up_ask"] < 0.99)]
            if len(sel) < 3:
                continue

            # Real DOWN token = "up" in data
            entry = sel["up_ask"]
            valid = sel[entry < 0.95]
            if len(valid) < 3:
                continue

            wr = (valid["actual_up"] == False).mean()
            med_entry = valid["up_ask"].median()
            med_bid = valid["up_bid"].median()
            spread = med_entry - med_bid
            med_vol = valid["volume"].median()

            # PM fee ≈ 2% (simplification)
            fee = 0.02 * med_entry
            ev = wr * (1 - med_entry - fee) - (1 - wr) * (med_entry + fee)
            marker = "✓" if ev > 0 else " "

            out.append(f"  {marker} gap<-{gap:.0%}: n={len(valid)}, WR(DOWN)={wr*100:.1f}%, "
                       f"ask=${med_entry:.3f}, bid=${med_bid:.3f}, spread=${spread:.3f}, "
                       f"vol=${med_vol:.0f}, EV≈${ev:.4f}")

            # Per-coin
            for coin in sorted(valid["coin"].unique()):
                c = valid[valid["coin"] == coin]
                if len(c) >= 2:
                    cwr = (c["actual_up"] == False).mean()
                    out.append(f"      {coin}: n={len(c)}, WR={cwr*100:.0f}%")

        # Other direction: gap > X (Kalshi YES, PM DOWN)
        out.append("\nWhen Kalshi > PM (buy PM UP = buy 'down' token in data):")
        for gap in [0.05, 0.10, 0.15, 0.20]:
            sel = cross[(cross["gap"] > gap) &
                         (cross["down_bid"] > 0.01) & (cross["down_ask"] < 0.99)]
            if len(sel) < 3:
                continue

            entry = sel["down_ask"]
            valid = sel[entry < 0.95]
            if len(valid) < 3:
                continue

            wr = valid["actual_up"].mean()
            med_entry = valid["down_ask"].median()
            med_bid = valid["down_bid"].median()
            spread = med_entry - med_bid
            med_vol = valid["volume"].median()

            fee = 0.02 * med_entry
            ev = wr * (1 - med_entry - fee) - (1 - wr) * (med_entry + fee)
            marker = "✓" if ev > 0 else " "

            out.append(f"  {marker} gap>{gap:.0%}: n={len(valid)}, WR(UP)={wr*100:.1f}%, "
                       f"ask=${med_entry:.3f}, bid=${med_bid:.3f}, spread=${spread:.3f}, "
                       f"vol=${med_vol:.0f}, EV≈${ev:.4f}")

    # --- 2c. Day-by-day for best PM strategy ---
    out.append("\n### 2c. Day-by-Day PM Strategy (T-300s, gap<-10%)\n")

    k_at = k_snaps[(k_snaps["seconds_remaining"] >= 285) & (k_snaps["seconds_remaining"] <= 315) &
                    (k_snaps["yes_bid"] > 0) & (k_snaps["yes_ask"] < 1)]
    k_first = k_at.sort_values("seconds_remaining").groupby(["coin", "round_time"]).first().reset_index()

    pm_at = pm_snaps[(pm_snaps["seconds_remaining"] >= 285) & (pm_snaps["seconds_remaining"] <= 315)]
    pm_first = pm_at.sort_values("seconds_remaining").groupby(["coin", "round_time"]).first().reset_index()

    cross = k_first[["coin", "round_time", "yes_mid"]].merge(
        pm_first[["coin", "round_time", "actual_up_prob", "pm_outcome",
                   "up_bid", "up_ask", "volume"]],
        on=["coin", "round_time"], how="inner"
    )
    cross["gap"] = cross["yes_mid"] - cross["actual_up_prob"]
    cross["actual_up"] = cross["pm_outcome"] == "up"
    cross["date"] = cross["round_time"].dt.strftime("%Y-%m-%d")

    sel = cross[(cross["gap"] < -0.10) & (cross["up_ask"] > 0.01) & (cross["up_ask"] < 0.95)]
    if len(sel) > 0:
        for date in sorted(sel["date"].unique()):
            d = sel[sel["date"] == date]
            wr = (d["actual_up"] == False).mean()
            med_ask = d["up_ask"].median()
            fee = 0.02 * med_ask
            ev = wr * (1 - med_ask - fee) - (1 - wr) * (med_ask + fee)
            out.append(f"  {date}: n={len(d)}, WR(DOWN)={wr*100:.0f}%, "
                       f"ask=${med_ask:.3f}, EV≈${ev:.4f}")

    # --- 2d. PM 5m markets (more data) ---
    out.append("\n### 2d. PM 5m — Same price-gap analysis with Kalshi\n")
    out.append("PM 5m has way more rounds. Can Kalshi 15m mid predict PM 5m outcomes?\n")

    pm_5m = pm[pm["file_duration"] == "5m"].copy()
    pm_5m_ends = pm_5m[pm_5m["row_type"].str.contains("end|resolved", case=False, na=False)]
    pm_5m_ends_u = pm_5m_ends[pm_5m_ends["outcome"].isin(["up", "down"])][["slug", "outcome"]].drop_duplicates("slug")
    pm_5m_ends_u = pm_5m_ends_u.rename(columns={"outcome": "pm_outcome"})
    pm_5m_snaps = pm_5m[pm_5m["row_type"] == "snapshot"].copy()
    pm_5m_snaps = pm_5m_snaps.merge(pm_5m_ends_u, on="slug", how="inner")
    pm_5m_snaps["end_dt"] = pd.to_datetime(pm_5m_snaps["end_date"], utc=True)
    pm_5m_snaps["round_time_5m"] = pm_5m_snaps["end_dt"].dt.floor("5min")
    pm_5m_snaps["coin"] = pm_5m_snaps["coin"].str.upper()

    # Get Kalshi mid at any point — match to closest PM 5m round
    # Kalshi snapshot timestamp → floor to 5min → match PM 5m round_time
    k_recent = k_snaps[(k_snaps["seconds_remaining"] >= 200) & (k_snaps["seconds_remaining"] <= 500) &
                        (k_snaps["yes_bid"] > 0) & (k_snaps["yes_ask"] < 1)]
    k_r = k_recent.sort_values("seconds_remaining").groupby(
        ["coin", "round_ticker"]).first().reset_index()
    k_r["round_time_5m"] = k_r["timestamp"].dt.floor("5min")

    pm_5m_at = pm_5m_snaps[(pm_5m_snaps["seconds_remaining"] >= 100) &
                             (pm_5m_snaps["seconds_remaining"] <= 250)]
    pm_5m_first = pm_5m_at.sort_values("seconds_remaining").groupby(
        ["coin", "round_time_5m"]).first().reset_index()

    cross5 = k_r[["coin", "round_time_5m", "yes_mid"]].merge(
        pm_5m_first[["coin", "round_time_5m", "actual_up_prob", "pm_outcome",
                       "up_bid", "up_ask", "down_bid", "down_ask"]],
        on=["coin", "round_time_5m"], how="inner"
    )

    if len(cross5) > 20:
        cross5["gap"] = cross5["yes_mid"] - cross5["actual_up_prob"]
        cross5["actual_up"] = cross5["pm_outcome"] == "up"

        out.append(f"\nMatched Kalshi-vs-PM-5m rounds: {len(cross5)}")
        out.append(f"Agreement (both >0.5): {((cross5['yes_mid'] > 0.5) == (cross5['actual_up_prob'] > 0.5)).mean()*100:.1f}%")

        for gap in [0.05, 0.10, 0.15]:
            # Buy PM 5m DOWN when Kalshi says NO more strongly
            sel = cross5[(cross5["gap"] < -gap) &
                          (cross5["up_ask"] > 0.01) & (cross5["up_ask"] < 0.95)]
            if len(sel) >= 5:
                wr = (sel["actual_up"] == False).mean()
                med = sel["up_ask"].median()
                fee = 0.02 * med
                ev = wr * (1 - med - fee) - (1 - wr) * (med + fee)
                marker = "✓" if ev > 0 else " "
                out.append(f"  {marker} 5m gap<-{gap:.0%}: n={len(sel)}, WR(DOWN)={wr*100:.1f}%, "
                           f"ask=${med:.3f}, EV≈${ev:.4f}")

            # Buy PM 5m UP when Kalshi says YES more strongly
            sel = cross5[(cross5["gap"] > gap) &
                          (cross5["down_ask"] > 0.01) & (cross5["down_ask"] < 0.95)]
            if len(sel) >= 5:
                wr = sel["actual_up"].mean()
                med = sel["down_ask"].median()
                fee = 0.02 * med
                ev = wr * (1 - med - fee) - (1 - wr) * (med + fee)
                marker = "✓" if ev > 0 else " "
                out.append(f"  {marker} 5m gap>{gap:.0%}: n={len(sel)}, WR(UP)={wr*100:.1f}%, "
                           f"ask=${med:.3f}, EV≈${ev:.4f}")
    else:
        out.append(f"Only {len(cross5)} matches — insufficient")

    return "\n".join(out)


def main():
    print("Loading data...")
    kalshi = load_kalshi()
    pm = load_pm()

    print("\nPart 1: ML deep dive...")
    p1 = part1_ml_deep_dive(kalshi)
    print(p1)

    print("\nPart 2: PM deep dive...")
    p2 = part2_pm_deep_dive(kalshi, pm)
    print(p2)

    out_file = PROJECT / "research" / "v3-deep-dive.md"
    out_file.write_text(f"# V3 Deep Dive\n\n{p1}\n\n{p2}\n")
    print(f"\nWritten to {out_file}")


if __name__ == "__main__":
    main()
