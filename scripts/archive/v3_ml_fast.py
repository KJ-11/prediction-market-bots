"""
V3 ML Analysis — Vectorized feature extraction + ML + cross-platform.
"""
from __future__ import annotations

import warnings
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.preprocessing import StandardScaler

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


def extract_features_vectorized(kalshi: pd.DataFrame, obs_time: int = 300) -> pd.DataFrame:
    """Vectorized feature extraction at a single observation time point."""
    ends = kalshi[kalshi["row_type"] == "round_end"][["round_ticker", "outcome"]].copy()
    ends = ends.drop_duplicates("round_ticker").rename(columns={"outcome": "round_outcome"})
    snaps = kalshi[kalshi["row_type"] == "snapshot"].copy()
    snaps = snaps.merge(ends, on="round_ticker", how="inner")

    # Bin seconds_elapsed into nearest 30s to enable fast lookups
    snaps["time_bin"] = (snaps["seconds_elapsed"] / 10).round() * 10

    # Get snapshot closest to observation time per round
    obs_snaps = snaps[(snaps["seconds_elapsed"] >= obs_time - 5) &
                       (snaps["seconds_elapsed"] <= obs_time + 5)].copy()
    current = obs_snaps.sort_values("seconds_elapsed").groupby(
        ["coin", "round_ticker"]).first().reset_index()

    # Get earlier snapshots for momentum calculation
    def get_snap_at(target_t, label):
        sel = snaps[(snaps["seconds_elapsed"] >= target_t - 5) &
                     (snaps["seconds_elapsed"] <= target_t + 5)]
        first = sel.sort_values("seconds_elapsed").groupby(
            ["coin", "round_ticker"]).first().reset_index()
        return first[["coin", "round_ticker", "spot_price", "yes_bid", "yes_ask"]].rename(
            columns={"spot_price": f"spot_{label}", "yes_bid": f"bid_{label}", "yes_ask": f"ask_{label}"})

    snap_30ago = get_snap_at(obs_time - 30, "30ago") if obs_time >= 35 else None
    snap_60ago = get_snap_at(obs_time - 60, "60ago") if obs_time >= 65 else None
    snap_120ago = get_snap_at(obs_time - 120, "120ago") if obs_time >= 125 else None
    snap_start = get_snap_at(30, "start")

    # Merge
    df = current.copy()
    for snap_df in [snap_30ago, snap_60ago, snap_120ago, snap_start]:
        if snap_df is not None:
            df = df.merge(snap_df, on=["coin", "round_ticker"], how="left")

    # Per-round volatility from history
    history = snaps[snaps["seconds_elapsed"] <= obs_time]
    round_vol = history.groupby(["coin", "round_ticker"]).agg(
        intra_vol=("spot_move_pct", "std"),
        spot_range_raw=("spot_price", lambda x: x.max() - x.min()),
        n_snaps=("spot_price", "count"),
    )
    df = df.merge(round_vol, on=["coin", "round_ticker"], how="left")

    # Strike crossings
    def count_crosses(g):
        above = g["spot_price"] > g["strike"]
        return above.astype(int).diff().abs().sum() / 2
    cross_counts = history.groupby(["coin", "round_ticker"]).apply(count_crosses).rename("strike_crosses")
    df = df.merge(cross_counts, on=["coin", "round_ticker"], how="left")

    # Max distance seen
    history_with_dist = history.copy()
    history_with_dist["abs_dist_h"] = (history_with_dist["spot_price"] - history_with_dist["strike"]).abs() / history_with_dist["strike"]
    max_dist = history_with_dist.groupby(["coin", "round_ticker"])["abs_dist_h"].max().rename("max_dist_seen")
    df = df.merge(max_dist, on=["coin", "round_ticker"], how="left")

    # Build features
    df["pct_dist"] = (df["spot_price"] - df["strike"]) / df["strike"]
    df["abs_dist"] = df["pct_dist"].abs()
    df["spot_above"] = (df["spot_price"] > df["strike"]).astype(int)
    df["yes_mid"] = (df["yes_bid"] + df["yes_ask"]) / 2
    df["yes_mid"] = df["yes_mid"].where((df["yes_bid"] > 0) & (df["yes_ask"] < 1))
    df["yes_spread"] = (df["yes_ask"] - df["yes_bid"]).where((df["yes_bid"] > 0) & (df["yes_ask"] < 1))
    df["book_prob"] = df["yes_mid"]
    df["spot_book_agree"] = ((df["spot_above"] == 1) & (df["yes_mid"] > 0.5) |
                              (df["spot_above"] == 0) & (df["yes_mid"] < 0.5)).astype(float)
    df["spot_book_agree"] = df["spot_book_agree"].where(df["yes_mid"].notna())

    # Kraken divergence
    df["kc_divergence"] = (df["spot_price"] - df["kraken_spot"]).abs() / df["spot_price"]
    df["kc_sign"] = (df["spot_price"] > df["kraken_spot"]).astype(float)

    # Momentum
    if "spot_30ago" in df.columns:
        df["mom_30s"] = (df["spot_price"] - df["spot_30ago"]) / df["spot_30ago"]
    if "spot_60ago" in df.columns:
        df["mom_60s"] = (df["spot_price"] - df["spot_60ago"]) / df["spot_60ago"]
    if "spot_120ago" in df.columns:
        df["mom_120s"] = (df["spot_price"] - df["spot_120ago"]) / df["spot_120ago"]
    if "mom_30s" in df.columns and "mom_60s" in df.columns:
        df["accel"] = df["mom_30s"] - (df["mom_60s"] - df["mom_30s"])

    # Book momentum
    if "bid_60ago" in df.columns and "ask_60ago" in df.columns:
        df["early_mid"] = (df["bid_60ago"] + df["ask_60ago"]) / 2
        df["book_momentum"] = df["yes_mid"] - df["early_mid"]

    # Spot range relative to strike
    df["spot_range"] = df["spot_range_raw"] / df["strike"]

    # Time features
    df["hour"] = df["timestamp"].dt.hour
    df["seconds_elapsed_feat"] = obs_time

    # Target
    df["target"] = (df["round_outcome"] == "yes").astype(int)

    # Coin dummies
    for c in ["BTC", "ETH", "SOL", "XRP"]:
        df[f"coin_{c}"] = (df["coin"] == c).astype(int)

    return df


def run_ml(feat_df: pd.DataFrame, obs_time: int) -> str:
    out = []
    out.append(f"\n### ML at T+{obs_time}s\n")

    feature_cols = [c for c in [
        "pct_dist", "abs_dist", "spot_above", "yes_mid", "yes_spread",
        "spot_book_agree", "kc_divergence", "kc_sign",
        "mom_30s", "mom_60s", "mom_120s", "accel",
        "intra_vol", "spot_range", "book_momentum",
        "volume", "hour",
        "strike_crosses", "max_dist_seen",
        "coin_BTC", "coin_ETH", "coin_SOL", "coin_XRP",
    ] if c in feat_df.columns]

    df = feat_df.dropna(subset=["pct_dist", "target"]).copy()
    X = df[feature_cols].copy()
    y = df["target"].values

    for col in X.columns:
        X[col] = X[col].fillna(X[col].median())

    out.append(f"Samples: {len(df)}, Features: {len(feature_cols)}, Base rate: {y.mean()*100:.1f}% yes\n")

    # Logistic Regression
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    lr = LogisticRegression(max_iter=1000, C=0.1)
    lr_scores = cross_val_score(lr, X_scaled, y, cv=5, scoring="roc_auc")
    lr.fit(X_scaled, y)
    out.append(f"Logistic Regression AUC: {lr_scores.mean():.4f} ± {lr_scores.std():.4f}")

    coefs = pd.Series(lr.coef_[0], index=feature_cols).sort_values(key=abs, ascending=False)
    out.append("\nLR coefficients (top 10):")
    for feat, coef in coefs.head(10).items():
        out.append(f"  {feat:22s}: {coef:+.4f}")

    # GBM
    gb = GradientBoostingClassifier(n_estimators=200, max_depth=4, learning_rate=0.05,
                                     subsample=0.8, random_state=42)
    gb_scores = cross_val_score(gb, X, y, cv=5, scoring="roc_auc")
    gb.fit(X, y)
    out.append(f"\nGBM AUC: {gb_scores.mean():.4f} ± {gb_scores.std():.4f}")

    imp = pd.Series(gb.feature_importances_, index=feature_cols).sort_values(ascending=False)
    out.append("\nGBM Feature Importance (top 10):")
    for feat, importance in imp.head(10).items():
        bar = "█" * int(importance * 100)
        out.append(f"  {feat:22s}: {importance:.4f} {bar}")

    # Calibration comparison
    gb_probs = gb.predict_proba(X)[:, 1]
    brier_gb = brier_score_loss(y, gb_probs)
    market_probs = df["yes_mid"].fillna(0.5).values
    brier_market = brier_score_loss(y, market_probs)
    out.append(f"\nBrier Score — GBM: {brier_gb:.4f}, Market: {brier_market:.4f}")
    delta = brier_market - brier_gb
    out.append(f"  {'✓ GBM beats market' if delta > 0 else '✗ Market beats GBM'} by {abs(delta):.4f}")

    # Out-of-sample trading simulation
    out.append("\n### Out-of-Sample Trading Simulation\n")
    df_sorted = df.sort_values("file_date")
    split = int(len(df_sorted) * 0.7)
    train, test = df_sorted.iloc[:split], df_sorted.iloc[split:]

    X_train = train[feature_cols].fillna(train[feature_cols].median())
    X_test = test[feature_cols].fillna(train[feature_cols].median())
    y_train, y_test = train["target"].values, test["target"].values

    gb2 = GradientBoostingClassifier(n_estimators=200, max_depth=4, learning_rate=0.05,
                                      subsample=0.8, random_state=42)
    gb2.fit(X_train, y_train)
    probs = gb2.predict_proba(X_test)[:, 1]
    oos_auc = roc_auc_score(y_test, probs)
    out.append(f"Out-of-sample AUC: {oos_auc:.4f} (train={len(train)}, test={len(test)})")

    # Simulate trading: bet YES if model > thresh, NO if model < (1-thresh)
    test_df = test.copy()
    test_df["model_prob"] = probs

    for thresh in [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
        yes_bets = test_df[test_df["model_prob"] > thresh]
        no_bets = test_df[test_df["model_prob"] < (1 - thresh)]
        total = len(yes_bets) + len(no_bets)
        if total < 5:
            continue

        yes_wins = (yes_bets["target"] == 1).sum()
        no_wins = (no_bets["target"] == 0).sum()
        wr = (yes_wins + no_wins) / total

        # EV calculation
        evs = []
        for _, row in yes_bets.iterrows():
            ask = row["yes_ask"] if not pd.isna(row.get("yes_ask")) else 0.85
            if ask <= 0.01 or ask >= 1:
                ask = 0.85
            fee = kalshi_fee(ask)
            won = row["target"] == 1
            evs.append((1 - ask - fee) if won else -(ask + fee))

        for _, row in no_bets.iterrows():
            no_ask = row.get("no_ask", np.nan)
            if pd.isna(no_ask) or no_ask <= 0.01 or no_ask >= 1:
                yb = row.get("yes_bid", 0.5)
                no_ask = max(0.1, 1 - yb) if not pd.isna(yb) else 0.85
            fee = kalshi_fee(no_ask)
            won = row["target"] == 0
            evs.append((1 - no_ask - fee) if won else -(no_ask + fee))

        avg_ev = np.mean(evs)
        total_pnl = np.sum(evs)
        n_days = test_df["file_date"].nunique()
        daily = total / max(1, n_days)

        marker = "✓" if avg_ev > 0 else " "
        out.append(f"  {marker} p>{thresh:.2f}: {total} trades ({len(yes_bets)}Y/{len(no_bets)}N), "
                   f"WR={wr*100:.1f}%, EV=${avg_ev:.4f}, "
                   f"PnL=${total_pnl:.2f}, {daily:.1f}/day")

    # Per-coin OOS
    out.append("\n### Per-coin OOS AUC:")
    for coin in sorted(test_df["coin"].unique()):
        ct = test_df[test_df["coin"] == coin]
        if len(ct) < 20:
            continue
        Xct = ct[feature_cols].fillna(train[feature_cols].median())
        try:
            auc = roc_auc_score(ct["target"].values, gb2.predict_proba(Xct)[:, 1])
            out.append(f"  {coin}: AUC={auc:.4f} (n={len(ct)})")
        except Exception:
            pass

    return "\n".join(out)


def cross_platform_analysis(kalshi: pd.DataFrame, pm: pd.DataFrame) -> str:
    out = []
    out.append("\n" + "=" * 70)
    out.append("KALSHI LEADS PM — Can Kalshi price predict PM outcomes?")
    out.append("=" * 70)

    k_ends = kalshi[kalshi["row_type"] == "round_end"][["round_ticker", "outcome", "coin", "timestamp"]].copy()
    k_ends = k_ends.drop_duplicates("round_ticker")
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

    for sec_rem in [600, 450, 300, 180, 120, 60]:
        # Kalshi mid at this time
        k_at = k_snaps[(k_snaps["seconds_remaining"] >= sec_rem - 15) &
                        (k_snaps["seconds_remaining"] <= sec_rem + 15) &
                        (k_snaps["yes_bid"] > 0) & (k_snaps["yes_ask"] < 1)]
        k_first = k_at.sort_values("seconds_remaining").groupby(
            ["coin", "round_time"]).first().reset_index()

        # PM mid at this time
        pm_at = pm_snaps[(pm_snaps["seconds_remaining"] >= sec_rem - 15) &
                          (pm_snaps["seconds_remaining"] <= sec_rem + 15) &
                          (pm_snaps["up_bid"] > 0.05) & (pm_snaps["up_ask"] < 0.95)]
        pm_first = pm_at.sort_values("seconds_remaining").groupby(
            ["coin", "round_time"]).first().reset_index()

        cross = k_first[["coin", "round_time", "yes_mid"]].merge(
            pm_first[["coin", "round_time", "actual_up_prob", "pm_outcome",
                       "up_ask", "down_ask", "up_bid", "down_bid"]],
            on=["coin", "round_time"], how="inner"
        )

        if len(cross) < 20:
            out.append(f"\nT-{sec_rem}s: {len(cross)} matches — too few")
            continue

        cross["k_says_yes"] = cross["yes_mid"] > 0.5
        cross["pm_says_up"] = cross["actual_up_prob"] > 0.5
        cross["actual_up"] = cross["pm_outcome"] == "up"
        cross["agree"] = cross["k_says_yes"] == cross["pm_says_up"]
        disagree = cross[~cross["agree"]]

        k_predicts_pm = (cross["k_says_yes"] == cross["actual_up"]).mean()
        pm_acc = (cross["pm_says_up"] == cross["actual_up"]).mean()

        out.append(f"\nT-{sec_rem}s: n={len(cross)}, agree={cross['agree'].mean()*100:.1f}%, "
                   f"disagree={len(disagree)}")
        out.append(f"  Kalshi predicts PM outcome: {k_predicts_pm*100:.1f}%")
        out.append(f"  PM predicts own outcome: {pm_acc*100:.1f}%")

        if len(disagree) >= 5:
            k_right = (disagree["k_says_yes"] == disagree["actual_up"]).mean()
            pm_right = (disagree["pm_says_up"] == disagree["actual_up"]).mean()
            out.append(f"  DISAGREEMENTS (n={len(disagree)}):")
            out.append(f"    Kalshi right about PM: {k_right*100:.1f}%")
            out.append(f"    PM right about itself: {pm_right*100:.1f}%")

            # Trading opportunity: when Kalshi disagrees with PM, trade PM
            # Kalshi=YES, PM=DOWN → buy PM UP token (= down_ask in our inverted data)
            k_yes_pm_down = disagree[disagree["k_says_yes"] & ~disagree["pm_says_up"]]
            k_no_pm_up = disagree[~disagree["k_says_yes"] & disagree["pm_says_up"]]

            if len(k_yes_pm_down) >= 3:
                wr = k_yes_pm_down["actual_up"].mean()
                # Buy REAL UP = buy what's labeled "down" in our data
                entry = k_yes_pm_down["down_ask"].median()
                out.append(f"    Kalshi=YES, PM=DOWN ({len(k_yes_pm_down)} cases): "
                           f"PM actually UP {wr*100:.0f}%, entry≈${entry:.3f}")

            if len(k_no_pm_up) >= 3:
                wr = (k_no_pm_up["actual_up"] == False).mean()
                entry = k_no_pm_up["up_ask"].median()  # buy real DOWN = buy "up" in data
                out.append(f"    Kalshi=NO, PM=UP ({len(k_no_pm_up)} cases): "
                           f"PM actually DOWN {wr*100:.0f}%, entry≈${entry:.3f}")

    # Price gap analysis
    out.append("\n\n### Price Gap → PM Outcome\n")
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

        cross = k_first[["coin", "round_time", "yes_mid"]].merge(
            pm_first[["coin", "round_time", "actual_up_prob", "pm_outcome",
                       "down_ask", "up_ask"]],
            on=["coin", "round_time"], how="inner"
        )
        if len(cross) < 20:
            continue

        cross["gap"] = cross["yes_mid"] - cross["actual_up_prob"]
        cross["actual_up"] = cross["pm_outcome"] == "up"

        for thresh in [0.05, 0.10, 0.15, 0.20, 0.30]:
            # Kalshi much higher → bet UP on PM
            higher = cross[cross["gap"] > thresh]
            if len(higher) >= 5:
                wr = higher["actual_up"].mean()
                entry = higher["down_ask"].median()  # real UP token
                # PM fee ~2%
                fee = 0.02 * entry if entry > 0 else 0.01
                ev = wr * (1 - entry - fee) - (1 - wr) * (entry + fee) if entry > 0 else np.nan
                marker = "✓" if ev and ev > 0 else " "
                out.append(f"  {marker} T-{sec_rem}s, gap>{thresh:.0%} ({len(higher)}): "
                           f"PM UP {wr*100:.0f}%, entry=${entry:.3f}, EV≈${ev:.4f}")

            # Kalshi much lower → bet DOWN on PM
            lower = cross[cross["gap"] < -thresh]
            if len(lower) >= 5:
                wr = (lower["actual_up"] == False).mean()
                entry = lower["up_ask"].median()  # real DOWN token
                fee = 0.02 * entry if entry > 0 else 0.01
                ev = wr * (1 - entry - fee) - (1 - wr) * (entry + fee) if entry > 0 else np.nan
                marker = "✓" if ev and ev > 0 else " "
                out.append(f"  {marker} T-{sec_rem}s, gap<-{thresh:.0%} ({len(lower)}): "
                           f"PM DOWN {wr*100:.0f}%, entry=${entry:.3f}, EV≈${ev:.4f}")

    return "\n".join(out)


def main():
    print("Loading data...")
    kalshi = load_kalshi()
    pm = load_pm()

    all_ml = []
    for obs_t in [180, 300, 450]:
        print(f"\nExtracting features at T+{obs_t}...")
        feat = extract_features_vectorized(kalshi, obs_t)
        print(f"  {len(feat)} samples")
        print(f"\nRunning ML at T+{obs_t}...")
        ml = run_ml(feat, obs_t)
        print(ml)
        all_ml.append(ml)

    print("\nCross-platform analysis...")
    cross = cross_platform_analysis(kalshi, pm)
    print(cross)

    out_file = PROJECT / "research" / "v3-ml-analysis.md"
    out_file.write_text("# V3 ML & Cross-Platform Analysis\n\n" + "\n".join(all_ml) + "\n" + cross + "\n")
    print(f"\nWritten to {out_file}")


if __name__ == "__main__":
    main()
