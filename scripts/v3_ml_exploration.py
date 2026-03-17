"""
V3 ML Exploration — Let the data speak.

Instead of testing hand-picked signals, extract ALL features from the data
and use ML to find what actually predicts outcomes.

Feature universe:
  - Spot-based: distance, momentum (multiple windows), acceleration, volatility
  - Book-based: mid, spread, bid/ask skew, price level
  - Cross-exchange: Coinbase-Kraken divergence, direction agreement
  - Time-based: seconds elapsed, time of day, day of week
  - Cross-platform: PM midpoint, PM-Kalshi price gap
  - Cross-coin: other coins' distance/momentum/book at same time
  - Intra-round: prior snapshots within same round (path features)
  - Volume: round volume, volume rate

Also:
  - Kalshi leads PM analysis (can Kalshi price predict PM outcome?)
  - Full cross-platform arb exploration
"""
from __future__ import annotations

import warnings
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score, TimeSeriesSplit
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.preprocessing import StandardScaler

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
    if not frames:
        return pd.DataFrame()
    all_df = pd.concat(frames, ignore_index=True)
    all_df["timestamp"] = pd.to_datetime(all_df["timestamp"], format="ISO8601", utc=True)
    for col in ["up_bid", "up_ask", "down_bid", "down_ask", "up_midpoint",
                 "spread", "last_trade_price", "spot_price", "kraken_price",
                 "rtds_price", "volume", "seconds_remaining"]:
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce")
    # Fix: up token is actually down token
    all_df["actual_up_prob"] = 1.0 - all_df["up_midpoint"]
    return all_df


# ---------------------------------------------------------------------------
# Part 1: Kalshi Feature Extraction
# ---------------------------------------------------------------------------

def extract_kalshi_features(kalshi: pd.DataFrame) -> pd.DataFrame:
    """Extract rich features for each round at a specific observation point.

    For each round, we sample at multiple time points and build features
    from current state + history within the round.
    """
    ends = kalshi[kalshi["row_type"] == "round_end"][["round_ticker", "outcome"]].copy()
    ends = ends.drop_duplicates("round_ticker").rename(columns={"outcome": "round_outcome"})
    snaps = kalshi[kalshi["row_type"] == "snapshot"].copy()
    snaps = snaps.merge(ends, on="round_ticker", how="inner")

    # We'll extract features at observation points: every 30s from T+120 to T+600
    observation_points = list(range(120, 601, 30))

    all_features = []

    for coin in sorted(snaps["coin"].unique()):
        c_snaps = snaps[snaps["coin"] == coin].copy()
        rounds = c_snaps["round_ticker"].unique()

        for rt in rounds:
            r = c_snaps[c_snaps["round_ticker"] == rt].sort_values("seconds_elapsed")
            if len(r) < 20:
                continue

            outcome = r.iloc[0]["round_outcome"]
            if outcome not in ("yes", "no"):
                continue

            target = 1 if outcome == "yes" else 0
            strike = r.iloc[0]["strike"]
            file_date = r.iloc[0]["file_date"]
            ts = r.iloc[0]["timestamp"]

            for obs_t in observation_points:
                # Find snapshot closest to obs_t
                closest_idx = (r["seconds_elapsed"] - obs_t).abs().idxmin()
                row = r.loc[closest_idx]
                if abs(row["seconds_elapsed"] - obs_t) > 10:
                    continue

                spot = row["spot_price"]
                kraken = row["kraken_spot"]
                yes_bid = row["yes_bid"]
                yes_ask = row["yes_ask"]
                no_bid = row["no_bid"]
                no_ask = row["no_ask"]

                if pd.isna(spot) or pd.isna(strike) or strike == 0:
                    continue

                # --- Current state features ---
                pct_dist = (spot - strike) / strike
                abs_dist = abs(pct_dist)
                spot_above = 1 if spot > strike else 0

                yes_mid = (yes_bid + yes_ask) / 2 if yes_bid > 0 and yes_ask > 0 and yes_ask < 1 else np.nan
                yes_spread = yes_ask - yes_bid if yes_bid > 0 and yes_ask > 0 else np.nan

                # Book-implied probability
                book_prob = yes_mid if not pd.isna(yes_mid) else np.nan

                # Spot vs book agreement
                spot_says_yes = spot > strike
                book_says_yes = yes_mid > 0.5 if not pd.isna(yes_mid) else None
                agree = 1 if (spot_says_yes == book_says_yes) else 0 if book_says_yes is not None else np.nan

                # Kraken divergence
                kc_div = abs(spot - kraken) / spot if not pd.isna(kraken) and kraken > 0 else np.nan
                kc_sign = 1 if (not pd.isna(kraken) and spot > kraken) else (0 if not pd.isna(kraken) else np.nan)

                # --- Historical features (within this round) ---
                history = r[r["seconds_elapsed"] <= obs_t]
                early = r[r["seconds_elapsed"] <= max(30, obs_t - 60)]

                # Momentum: spot change over last 30s, 60s, 120s
                def spot_at(t):
                    sel = r[(r["seconds_elapsed"] >= t - 5) & (r["seconds_elapsed"] <= t + 5)]
                    return sel["spot_price"].iloc[0] if len(sel) > 0 else np.nan

                spot_30ago = spot_at(obs_t - 30)
                spot_60ago = spot_at(obs_t - 60)
                spot_120ago = spot_at(obs_t - 120)

                mom_30 = (spot - spot_30ago) / spot_30ago if not pd.isna(spot_30ago) and spot_30ago > 0 else np.nan
                mom_60 = (spot - spot_60ago) / spot_60ago if not pd.isna(spot_60ago) and spot_60ago > 0 else np.nan
                mom_120 = (spot - spot_120ago) / spot_120ago if not pd.isna(spot_120ago) and spot_120ago > 0 else np.nan

                # Acceleration
                if not pd.isna(mom_30) and not pd.isna(mom_60):
                    accel = mom_30 - (mom_60 - mom_30)  # change in momentum
                else:
                    accel = np.nan

                # Volatility: std of spot_move_pct in history
                if len(history) > 5:
                    vol = history["spot_move_pct"].std()
                    spot_range = (history["spot_price"].max() - history["spot_price"].min()) / strike
                else:
                    vol = np.nan
                    spot_range = np.nan

                # Book momentum: how has yes_mid changed?
                if len(early) > 5:
                    early_valid = early[(early["yes_bid"] > 0) & (early["yes_ask"] < 1)]
                    if len(early_valid) > 0:
                        early_mid = (early_valid.iloc[-1]["yes_bid"] + early_valid.iloc[-1]["yes_ask"]) / 2
                        book_mom = (yes_mid - early_mid) if not pd.isna(yes_mid) else np.nan
                    else:
                        book_mom = np.nan
                else:
                    book_mom = np.nan

                # Volume
                vol_now = row["volume"] if not pd.isna(row["volume"]) else 0

                # Time features
                hour = ts.hour
                minute = ts.minute

                # Did spot cross strike during this round?
                if len(history) > 5:
                    crosses = ((history["spot_price"] > strike).astype(int).diff().abs().sum()) / 2
                else:
                    crosses = np.nan

                # Max distance seen so far
                if len(history) > 5:
                    max_dist = ((history["spot_price"] - strike).abs() / strike).max()
                else:
                    max_dist = np.nan

                features = {
                    "round_ticker": rt,
                    "coin": coin,
                    "file_date": file_date,
                    "obs_time": obs_t,
                    "target": target,
                    # Current state
                    "pct_dist": pct_dist,
                    "abs_dist": abs_dist,
                    "spot_above": spot_above,
                    "yes_mid": yes_mid,
                    "yes_spread": yes_spread,
                    "book_prob": book_prob,
                    "spot_book_agree": agree,
                    "kc_divergence": kc_div,
                    "kc_sign": kc_sign,
                    # Momentum
                    "mom_30s": mom_30,
                    "mom_60s": mom_60,
                    "mom_120s": mom_120,
                    "accel": accel,
                    # Volatility
                    "intra_vol": vol,
                    "spot_range": spot_range,
                    # Book dynamics
                    "book_momentum": book_mom,
                    # Volume
                    "volume": vol_now,
                    # Time
                    "hour": hour,
                    "seconds_elapsed": obs_t,
                    # Path features
                    "strike_crosses": crosses,
                    "max_dist_seen": max_dist,
                }

                all_features.append(features)

    return pd.DataFrame(all_features)


# ---------------------------------------------------------------------------
# Part 2: ML Analysis
# ---------------------------------------------------------------------------

def run_ml_analysis(feat_df: pd.DataFrame) -> str:
    out = []
    out.append("=" * 70)
    out.append("ML ANALYSIS: What features actually predict outcomes?")
    out.append("=" * 70)

    feature_cols = [
        "pct_dist", "abs_dist", "spot_above", "yes_mid", "yes_spread",
        "spot_book_agree", "kc_divergence", "kc_sign",
        "mom_30s", "mom_60s", "mom_120s", "accel",
        "intra_vol", "spot_range", "book_momentum",
        "volume", "hour", "seconds_elapsed",
        "strike_crosses", "max_dist_seen",
    ]

    # Analyze at different time points
    for obs_time in [180, 300, 450]:
        out.append(f"\n### Observation at T+{obs_time}s\n")

        df = feat_df[feat_df["obs_time"] == obs_time].copy()
        if len(df) < 100:
            out.append(f"Only {len(df)} samples — skipping")
            continue

        # Drop rows with too many NaNs
        df = df.dropna(subset=["pct_dist", "target"])
        X = df[feature_cols].copy()
        y = df["target"].values

        # Fill NaN with median
        for col in X.columns:
            X[col] = X[col].fillna(X[col].median())

        out.append(f"Samples: {len(df)}, Features: {len(feature_cols)}")
        out.append(f"Base rate: {y.mean()*100:.1f}% yes\n")

        # --- Logistic Regression (interpretable) ---
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        lr = LogisticRegression(max_iter=1000, C=0.1)
        lr_scores = cross_val_score(lr, X_scaled, y, cv=5, scoring="roc_auc")
        out.append(f"Logistic Regression AUC: {lr_scores.mean():.4f} ± {lr_scores.std():.4f}")

        # Fit on all data for feature importance
        lr.fit(X_scaled, y)
        coef_df = pd.DataFrame({
            "feature": feature_cols,
            "coef": lr.coef_[0],
            "abs_coef": np.abs(lr.coef_[0]),
        }).sort_values("abs_coef", ascending=False)
        out.append("\nLogistic Regression coefficients (top 10):")
        for _, row in coef_df.head(10).iterrows():
            direction = "→YES" if row["coef"] > 0 else "→NO"
            out.append(f"  {row['feature']:20s}: {row['coef']:+.4f} {direction}")

        # --- Gradient Boosting (nonlinear) ---
        gb = GradientBoostingClassifier(n_estimators=100, max_depth=3, learning_rate=0.1,
                                         subsample=0.8, random_state=42)
        gb_scores = cross_val_score(gb, X, y, cv=5, scoring="roc_auc")
        out.append(f"\nGradient Boosting AUC: {gb_scores.mean():.4f} ± {gb_scores.std():.4f}")

        # Feature importance
        gb.fit(X, y)
        imp_df = pd.DataFrame({
            "feature": feature_cols,
            "importance": gb.feature_importances_,
        }).sort_values("importance", ascending=False)
        out.append("\nGBM Feature Importance (top 10):")
        for _, row in imp_df.head(10).iterrows():
            bar = "█" * int(row["importance"] * 100)
            out.append(f"  {row['feature']:20s}: {row['importance']:.4f} {bar}")

        # --- Calibration: how well does the model's probability match reality? ---
        gb_probs = gb.predict_proba(X)[:, 1]
        brier = brier_score_loss(y, gb_probs)
        # Compare to market's calibration
        market_probs = df["yes_mid"].fillna(0.5).values
        market_brier = brier_score_loss(y, market_probs)
        out.append(f"\nBrier Score — GBM: {brier:.4f}, Market: {market_brier:.4f}")
        if brier < market_brier:
            out.append(f"  ✓ GBM beats market by {(market_brier-brier):.4f}")
        else:
            out.append(f"  ✗ Market is better calibrated by {(brier-market_brier):.4f}")

        # --- Per-coin analysis ---
        out.append("\nPer-coin GBM AUC:")
        for coin in sorted(df["coin"].unique()):
            c = df[df["coin"] == coin]
            if len(c) < 50:
                continue
            Xc = c[feature_cols].copy()
            yc = c["target"].values
            for col in Xc.columns:
                Xc[col] = Xc[col].fillna(Xc[col].median())
            try:
                scores = cross_val_score(gb, Xc, yc, cv=min(5, len(c)//10), scoring="roc_auc")
                out.append(f"  {coin}: AUC={scores.mean():.4f} ± {scores.std():.4f} (n={len(c)})")
            except Exception:
                out.append(f"  {coin}: failed (n={len(c)})")

        # --- Can the model find profitable trades? ---
        out.append("\n### Simulated Trading with GBM Predictions\n")
        # Use time-series split: train on first 70%, test on last 30%
        df_sorted = df.sort_values("file_date")
        split_idx = int(len(df_sorted) * 0.7)
        train = df_sorted.iloc[:split_idx]
        test = df_sorted.iloc[split_idx:]

        X_train = train[feature_cols].copy()
        y_train = train["target"].values
        X_test = test[feature_cols].copy()
        y_test = test["target"].values

        for col in X_train.columns:
            med = X_train[col].median()
            X_train[col] = X_train[col].fillna(med)
            X_test[col] = X_test[col].fillna(med)

        gb2 = GradientBoostingClassifier(n_estimators=100, max_depth=3, learning_rate=0.1,
                                          subsample=0.8, random_state=42)
        gb2.fit(X_train, y_train)
        test_probs = gb2.predict_proba(X_test)[:, 1]
        test_auc = roc_auc_score(y_test, test_probs)
        out.append(f"Out-of-sample AUC: {test_auc:.4f} (train {len(train)}, test {len(test)})")

        # Simulate: bet YES when model says >threshold, NO when <threshold
        test_df = test.copy()
        test_df["model_prob"] = test_probs

        for threshold in [0.60, 0.65, 0.70, 0.75, 0.80, 0.85]:
            # Bet YES when model says > threshold
            yes_bets = test_df[test_df["model_prob"] > threshold]
            no_bets = test_df[test_df["model_prob"] < (1 - threshold)]

            total_bets = len(yes_bets) + len(no_bets)
            if total_bets < 5:
                continue

            yes_wins = (yes_bets["target"] == 1).sum()
            no_wins = (no_bets["target"] == 0).sum()
            total_wins = yes_wins + no_wins
            wr = total_wins / total_bets

            # Estimate EV using the actual ask prices
            yes_ev_list = []
            for _, row in yes_bets.iterrows():
                ask = row["yes_ask"] if not pd.isna(row.get("yes_ask", np.nan)) else (row["yes_mid"] + 0.01 if not pd.isna(row["yes_mid"]) else 0.85)
                fee = kalshi_fee(ask) if ask > 0 else 0.01
                won = row["target"] == 1
                ev = (1 - ask - fee) if won else -(ask + fee)
                yes_ev_list.append(ev)

            no_ev_list = []
            for _, row in no_bets.iterrows():
                no_ask = row.get("no_ask", np.nan)
                if pd.isna(no_ask) or no_ask <= 0:
                    no_ask = 1 - row["yes_bid"] if not pd.isna(row.get("yes_bid", np.nan)) and row.get("yes_bid", 0) > 0 else 0.85
                fee = kalshi_fee(no_ask)
                won = row["target"] == 0
                ev = (1 - no_ask - fee) if won else -(no_ask + fee)
                no_ev_list.append(ev)

            all_ev = yes_ev_list + no_ev_list
            avg_ev = np.mean(all_ev)
            total_pnl = np.sum(all_ev)

            marker = "✓" if avg_ev > 0 else " "
            out.append(f"  {marker} threshold={threshold}: {total_bets} trades ({len(yes_bets)}Y/{len(no_bets)}N), "
                       f"WR={wr*100:.1f}%, avg_EV=${avg_ev:.4f}, total_PnL=${total_pnl:.2f}")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Part 3: Kalshi → PM Cross-Platform Signal
# ---------------------------------------------------------------------------

def cross_platform_analysis(kalshi: pd.DataFrame, pm: pd.DataFrame) -> str:
    out = []
    out.append("\n" + "=" * 70)
    out.append("CROSS-PLATFORM: Kalshi leads PM?")
    out.append("=" * 70)
    out.append("\nKalshi has tighter spreads and higher volume → more efficient.\n"
               "Hypothesis: Kalshi price moves first, PM follows with a lag.\n"
               "If true: when Kalshi mid says YES but PM mid says NO, Kalshi is right.\n"
               "We could BUY the cheap PM token that Kalshi says should win.\n")

    # Get Kalshi snapshots with book data
    k_ends = kalshi[kalshi["row_type"] == "round_end"][["round_ticker", "outcome"]].copy()
    k_ends = k_ends.drop_duplicates("round_ticker").rename(columns={"outcome": "round_outcome"})
    k_snaps = kalshi[kalshi["row_type"] == "snapshot"].copy()
    k_snaps = k_snaps.merge(k_ends, on="round_ticker", how="inner")
    k_snaps["yes_mid"] = (k_snaps["yes_bid"] + k_snaps["yes_ask"]) / 2

    # Get PM 15m snapshots with outcomes
    pm_15m = pm[pm["file_duration"] == "15m"].copy()
    pm_ends = pm_15m[pm_15m["row_type"].str.contains("end|resolved", case=False, na=False)]
    pm_ends_u = pm_ends[pm_ends["outcome"].isin(["up", "down"])][["slug", "outcome"]].drop_duplicates("slug")
    pm_ends_u = pm_ends_u.rename(columns={"outcome": "pm_outcome"})
    pm_snaps = pm_15m[pm_15m["row_type"] == "snapshot"].copy()
    pm_snaps = pm_snaps.merge(pm_ends_u, on="slug", how="inner")

    # Align by: coin + round close time (floor to 15 min)
    k_snaps["round_time"] = k_snaps["timestamp"].dt.floor("15min")
    pm_snaps["end_dt"] = pd.to_datetime(pm_snaps["end_date"], utc=True)
    pm_snaps["round_time"] = pm_snaps["end_dt"].dt.floor("15min")
    pm_snaps["coin"] = pm_snaps["coin"].str.upper()

    # For each matched round + time point, compare prices
    out.append("\n### Time-aligned price comparison\n")

    # Sample at various seconds_remaining values
    for sec_rem in [600, 450, 300, 180, 120, 60]:
        k_at = k_snaps[(k_snaps["seconds_remaining"] >= sec_rem - 15) &
                        (k_snaps["seconds_remaining"] <= sec_rem + 15) &
                        (k_snaps["yes_bid"] > 0) & (k_snaps["yes_ask"] < 1)]
        k_first = k_at.sort_values("seconds_remaining").groupby(
            ["coin", "round_time"]).first().reset_index()

        pm_at = pm_snaps[(pm_snaps["seconds_remaining"] >= sec_rem - 15) &
                          (pm_snaps["seconds_remaining"] <= sec_rem + 15) &
                          (pm_snaps["up_bid"] > 0.05) & (pm_snaps["up_ask"] < 0.95)]
        pm_first = pm_at.sort_values("seconds_remaining").groupby(
            ["coin", "round_time"]).first().reset_index()

        k_sig = k_first[["coin", "round_time", "yes_mid", "round_outcome"]].rename(
            columns={"yes_mid": "k_mid"})
        pm_sig = pm_first[["coin", "round_time", "actual_up_prob", "pm_outcome"]].rename(
            columns={"actual_up_prob": "pm_mid"})

        cross = k_sig.merge(pm_sig, on=["coin", "round_time"], how="inner")
        if len(cross) < 20:
            out.append(f"  T-{sec_rem}s: only {len(cross)} matched — skipping")
            continue

        cross["k_says_yes"] = cross["k_mid"] > 0.5
        cross["pm_says_up"] = cross["pm_mid"] > 0.5
        cross["actual_yes"] = cross["round_outcome"] == "yes"
        cross["actual_up"] = cross["pm_outcome"] == "up"

        # Overall accuracy of each platform predicting its own outcome
        k_acc = (cross["k_says_yes"] == cross["actual_yes"]).mean()
        pm_acc = (cross["pm_says_up"] == cross["actual_up"]).mean()

        # Agreement
        cross["agree"] = cross["k_says_yes"] == cross["pm_says_up"]
        agree_pct = cross["agree"].mean()

        # When they disagree, who's right about THEIR OWN outcome?
        disagree = cross[~cross["agree"]]

        out.append(f"  T-{sec_rem}s: n={len(cross)}, "
                   f"Kalshi acc={k_acc*100:.1f}%, PM acc={pm_acc*100:.1f}%, "
                   f"agree={agree_pct*100:.1f}%, disagree={len(disagree)}")

        if len(disagree) >= 10:
            # KEY: Can Kalshi predict PM outcome?
            k_predicts_pm = (disagree["k_says_yes"] == disagree["actual_up"]).mean()
            pm_predicts_k = (disagree["pm_says_up"] == disagree["actual_yes"]).mean()
            out.append(f"    When they disagree:")
            out.append(f"      Kalshi predicts PM outcome: {k_predicts_pm*100:.1f}%")
            out.append(f"      PM predicts Kalshi outcome: {pm_predicts_k*100:.1f}%")

            # TRADING SIGNAL: Buy PM's underpriced side based on Kalshi's signal
            # When Kalshi says YES but PM says DOWN (PM is cheap on UP side):
            k_yes_pm_down = disagree[disagree["k_says_yes"] & ~disagree["pm_says_up"]]
            k_no_pm_up = disagree[~disagree["k_says_yes"] & disagree["pm_says_up"]]

            if len(k_yes_pm_down) >= 3:
                wr = k_yes_pm_down["actual_up"].mean()
                # We'd buy PM UP token. The corrected up_prob is < 0.5 (PM says down),
                # so the UP token is cheap.
                pm_price = 1 - k_yes_pm_down["pm_mid"].median()  # what we'd pay for UP token
                out.append(f"      Kalshi=YES, PM=DOWN: {len(k_yes_pm_down)} cases, "
                           f"PM outcome=UP {wr*100:.0f}% of time, PM up_price≈${pm_price:.2f}")

            if len(k_no_pm_up) >= 3:
                wr = (1 - k_no_pm_up["actual_up"]).mean()  # want PM to go DOWN
                pm_price = k_no_pm_up["pm_mid"].median()  # we'd buy DOWN token
                out.append(f"      Kalshi=NO, PM=UP: {len(k_no_pm_up)} cases, "
                           f"PM outcome=DOWN {wr*100:.0f}% of time, PM down_price≈${pm_price:.2f}")

    # --- Price gap analysis ---
    out.append("\n### Kalshi-PM Price Gap\n")
    out.append("When Kalshi prices a round much higher/lower than PM, who's right?\n")

    for sec_rem in [450, 300, 180]:
        k_at = k_snaps[(k_snaps["seconds_remaining"] >= sec_rem - 15) &
                        (k_snaps["seconds_remaining"] <= sec_rem + 15) &
                        (k_snaps["yes_bid"] > 0) & (k_snaps["yes_ask"] < 1)]
        k_first = k_at.sort_values("seconds_remaining").groupby(
            ["coin", "round_time"]).first().reset_index()

        pm_at = pm_snaps[(pm_snaps["seconds_remaining"] >= sec_rem - 15) &
                          (pm_snaps["seconds_remaining"] <= sec_rem + 15) &
                          (pm_snaps["up_bid"] > 0.05) & (pm_snaps["up_ask"] < 0.95)]
        pm_first = pm_at.sort_values("seconds_remaining").groupby(
            ["coin", "round_time"]).first().reset_index()

        k_sig = k_first[["coin", "round_time", "yes_mid", "round_outcome"]]
        pm_sig = pm_first[["coin", "round_time", "actual_up_prob", "pm_outcome"]]

        cross = k_sig.merge(pm_sig, on=["coin", "round_time"], how="inner")
        if len(cross) < 20:
            continue

        cross["price_gap"] = cross["yes_mid"] - cross["actual_up_prob"]
        cross["abs_gap"] = cross["price_gap"].abs()
        cross["actual_yes"] = cross["round_outcome"] == "yes"

        # When Kalshi is much higher than PM (gap > 0.10):
        # Kalshi says more likely YES than PM does
        for gap_thresh in [0.05, 0.10, 0.15, 0.20]:
            k_higher = cross[cross["price_gap"] > gap_thresh]
            k_lower = cross[cross["price_gap"] < -gap_thresh]

            if len(k_higher) >= 5:
                yes_rate = k_higher["actual_yes"].mean()
                out.append(f"  T-{sec_rem}s, Kalshi>{gap_thresh:.0%} higher than PM (n={len(k_higher)}): "
                           f"actual YES={yes_rate*100:.1f}% "
                           f"(Kalshi mid={k_higher['yes_mid'].median():.2f}, PM mid={k_higher['actual_up_prob'].median():.2f})")

            if len(k_lower) >= 5:
                yes_rate = k_lower["actual_yes"].mean()
                out.append(f"  T-{sec_rem}s, Kalshi>{gap_thresh:.0%} LOWER than PM (n={len(k_lower)}): "
                           f"actual YES={yes_rate*100:.1f}% "
                           f"(Kalshi mid={k_lower['yes_mid'].median():.2f}, PM mid={k_lower['actual_up_prob'].median():.2f})")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Part 4: PM-specific trading analysis
# ---------------------------------------------------------------------------

def pm_trading_analysis(pm: pd.DataFrame) -> str:
    out = []
    out.append("\n" + "=" * 70)
    out.append("PM TRADING ANALYSIS — Can we trade on Polymarket directly?")
    out.append("=" * 70)

    for dur in ["5m", "15m"]:
        out.append(f"\n### PM {dur}\n")
        pm_dur = pm[pm["file_duration"] == dur].copy()
        ends = pm_dur[pm_dur["row_type"].str.contains("end|resolved", case=False, na=False)]
        ends_u = ends[ends["outcome"].isin(["up", "down"])][["slug", "outcome"]].drop_duplicates("slug")
        ends_u = ends_u.rename(columns={"outcome": "round_outcome"})
        snaps = pm_dur[pm_dur["row_type"] == "snapshot"].copy()
        snaps = snaps.merge(ends_u, on="slug", how="inner")

        if len(ends_u) < 50:
            out.append(f"Only {len(ends_u)} resolved rounds")
            continue

        out.append(f"Resolved rounds: {len(ends_u)}")
        out.append(f"Base rate: {(ends_u['round_outcome'] == 'up').mean()*100:.1f}% up\n")

        # Corrected calibration at mid-round
        if dur == "15m":
            mid = snaps[(snaps["seconds_remaining"] >= 300) & (snaps["seconds_remaining"] <= 600)]
        else:
            mid = snaps[(snaps["seconds_remaining"] >= 100) & (snaps["seconds_remaining"] <= 200)]

        quoted = mid[(mid["up_bid"] > 0.05) & (mid["up_ask"] < 0.95)]
        first = quoted.sort_values("seconds_remaining").groupby("slug").first().reset_index()

        if len(first) < 20:
            out.append("Insufficient quoted mid-round data")
            continue

        first["actual_up"] = (first["round_outcome"] == "up").astype(float)

        # The corrected UP probability
        first["up_prob"] = first["actual_up_prob"]

        # What if we buy the favored side at the "ask"?
        # Since tokens are inverted:
        #   Real UP token = what collector calls "down" token
        #   Real DOWN token = what collector calls "up" token
        # To buy real UP: buy down_ask (in our data)
        # To buy real DOWN: buy up_ask (in our data)
        first["real_up_ask"] = first["down_ask"]  # buying real UP
        first["real_down_ask"] = first["up_ask"]  # buying real DOWN

        first["bet_up"] = first["up_prob"] > 0.5
        first["entry_price"] = np.where(
            first["bet_up"],
            first["real_up_ask"],
            first["real_down_ask"]
        )
        first["won"] = np.where(
            first["bet_up"],
            first["round_outcome"] == "up",
            first["round_outcome"] == "down"
        )

        # Filter to quoted entries
        tradeable = first[(first["entry_price"] > 0.05) & (first["entry_price"] < 0.95)]
        if len(tradeable) < 10:
            out.append("Insufficient tradeable entries")
            continue

        wr = tradeable["won"].mean()
        med_price = tradeable["entry_price"].median()
        # PM fees are different from Kalshi — typically ~2% of position
        pm_fee = 0.02 * med_price  # approximate
        ev = wr * (1 - med_price - pm_fee) - (1 - wr) * (med_price + pm_fee)

        out.append(f"Buy favored side at mid-round:")
        out.append(f"  Tradeable entries: {len(tradeable)}")
        out.append(f"  WR: {wr*100:.1f}%")
        out.append(f"  Median entry: ${med_price:.3f}")
        out.append(f"  Est. EV/trade: ${ev:.4f}")

        # By confidence level
        for lo, hi in [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 1.0)]:
            conf_sel = tradeable[
                ((tradeable["bet_up"]) & (tradeable["up_prob"] >= lo) & (tradeable["up_prob"] < hi)) |
                ((~tradeable["bet_up"]) & ((1 - tradeable["up_prob"]) >= lo) & ((1 - tradeable["up_prob"]) < hi))
            ]
            if len(conf_sel) >= 5:
                cwr = conf_sel["won"].mean()
                cmed = conf_sel["entry_price"].median()
                cfee = 0.02 * cmed
                cev = cwr * (1 - cmed - cfee) - (1 - cwr) * (cmed + cfee)
                out.append(f"  Confidence {lo:.0%}-{hi:.0%}: n={len(conf_sel)}, "
                           f"WR={cwr*100:.1f}%, med=${cmed:.3f}, EV=${cev:.4f}")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading data...")
    kalshi = load_kalshi()
    pm = load_pm()
    print(f"  Kalshi: {len(kalshi):,} rows")
    print(f"  PM: {len(pm):,} rows")

    print("\nExtracting features (this may take a few minutes)...")
    feat_df = extract_kalshi_features(kalshi)
    print(f"  Extracted {len(feat_df):,} feature rows from {feat_df['round_ticker'].nunique()} rounds")

    # Add ask prices back for trading sim
    snaps = kalshi[kalshi["row_type"] == "snapshot"]
    ask_data = []
    for _, row in feat_df.iterrows():
        rt = row["round_ticker"]
        obs = row["obs_time"]
        r = snaps[(snaps["round_ticker"] == rt) &
                   (snaps["seconds_elapsed"] >= obs - 5) &
                   (snaps["seconds_elapsed"] <= obs + 5)]
        if len(r) > 0:
            ask_data.append({"yes_ask": r.iloc[0]["yes_ask"],
                            "yes_bid": r.iloc[0]["yes_bid"],
                            "no_ask": r.iloc[0]["no_ask"]})
        else:
            ask_data.append({"yes_ask": np.nan, "yes_bid": np.nan, "no_ask": np.nan})
    ask_df = pd.DataFrame(ask_data)
    feat_df["yes_ask"] = ask_df["yes_ask"].values
    feat_df["yes_bid"] = ask_df["yes_bid"].values
    feat_df["no_ask"] = ask_df["no_ask"].values

    print("\nRunning ML analysis...")
    ml_results = run_ml_analysis(feat_df)
    print(ml_results)

    print("\nRunning cross-platform analysis...")
    cross_results = cross_platform_analysis(kalshi, pm)
    print(cross_results)

    print("\nRunning PM trading analysis...")
    pm_results = pm_trading_analysis(pm)
    print(pm_results)

    # Save results
    out_file = PROJECT / "research" / "v3-ml-analysis.md"
    out_file.write_text(f"# V3 ML & Cross-Platform Analysis\n\n{ml_results}\n\n{cross_results}\n\n{pm_results}\n")
    print(f"\nResults written to {out_file}")


if __name__ == "__main__":
    main()
