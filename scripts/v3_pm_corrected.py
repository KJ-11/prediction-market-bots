"""PM corrected analysis using last_trade_price instead of broken midpoint."""
from __future__ import annotations
import warnings, numpy as np, pandas as pd
from pathlib import Path
warnings.filterwarnings("ignore")
KALSHI_DIR = Path("data/rounds")
PM_DIR = Path("data/rounds/polymarket")

def load_kalshi():
    frames = []
    for f in sorted(KALSHI_DIR.glob("KX*15M-*.csv")):
        coin = f.name.split("-")[0].replace("KX","").replace("15M","")
        df = pd.read_csv(f, engine="python", on_bad_lines="skip")
        df["coin"] = coin
        frames.append(df)
    a = pd.concat(frames, ignore_index=True)
    a["timestamp"] = pd.to_datetime(a["timestamp"], format="ISO8601", utc=True)
    for c in ["strike","spot_price","yes_bid","yes_ask","seconds_remaining","seconds_elapsed"]:
        a[c] = pd.to_numeric(a[c], errors="coerce")
    return a

def load_pm(dur="15m"):
    frames = []
    for f in sorted(PM_DIR.glob(f"*-{dur}-*.csv")):
        df = pd.read_csv(f, engine="python", on_bad_lines="skip")
        df["coin"] = f.stem.split("-")[0].upper()
        frames.append(df)
    a = pd.concat(frames, ignore_index=True)
    a["timestamp"] = pd.to_datetime(a["timestamp"], format="ISO8601", utc=True)
    for c in ["last_trade_price","spot_price","seconds_remaining","volume",
              "up_bid","up_ask","down_bid","down_ask"]:
        a[c] = pd.to_numeric(a[c], errors="coerce")
    return a

kalshi = load_kalshi()
pm = load_pm("15m")

# Outcomes
pm_ends = pm[pm["row_type"].str.contains("end|resolved", case=False, na=False)]
pm_eu = pm_ends[pm_ends["outcome"].isin(["up","down"])][["slug","outcome"]].drop_duplicates("slug")
pm_eu = pm_eu.rename(columns={"outcome":"pm_outcome"})
pm_snaps = pm[pm["row_type"] == "snapshot"].merge(pm_eu, on="slug", how="inner")

k_snaps = kalshi[kalshi["row_type"] == "snapshot"].copy()
k_snaps["yes_mid"] = (k_snaps["yes_bid"] + k_snaps["yes_ask"]) / 2
k_snaps["round_time"] = k_snaps["timestamp"].dt.floor("15min")

pm_snaps["end_dt"] = pd.to_datetime(pm_snaps["end_date"], utc=True)
pm_snaps["round_time"] = pm_snaps["end_dt"].dt.floor("15min")

print("=" * 60)
print("CROSS-PLATFORM: Kalshi mid vs PM last_trade_price")
print("=" * 60)

for sec_rem in [600, 450, 300, 180, 120]:
    k_at = k_snaps[(k_snaps["seconds_remaining"] >= sec_rem-15) &
                    (k_snaps["seconds_remaining"] <= sec_rem+15) &
                    (k_snaps["yes_bid"] > 0) & (k_snaps["yes_ask"] < 1)]
    k_first = k_at.sort_values("seconds_remaining").groupby(["coin","round_time"]).first().reset_index()

    pm_at = pm_snaps[(pm_snaps["seconds_remaining"] >= sec_rem-15) &
                      (pm_snaps["seconds_remaining"] <= sec_rem+15) &
                      (pm_snaps["last_trade_price"] > 0)]
    pm_first = pm_at.sort_values("seconds_remaining").groupby(["coin","round_time"]).first().reset_index()

    cross = k_first[["coin","round_time","yes_mid"]].merge(
        pm_first[["coin","round_time","last_trade_price","pm_outcome","volume"]],
        on=["coin","round_time"], how="inner")

    if len(cross) < 20:
        print(f"\nT-{sec_rem}s: {len(cross)} matches")
        continue

    cross["k_yes"] = cross["yes_mid"] > 0.5
    cross["pm_up"] = cross["last_trade_price"] > 0.5
    cross["actual_up"] = cross["pm_outcome"] == "up"
    cross["agree"] = cross["k_yes"] == cross["pm_up"]
    cross["gap"] = cross["yes_mid"] - cross["last_trade_price"]

    disagree = cross[~cross["agree"]]

    k_acc = (cross["k_yes"] == cross["actual_up"]).mean()
    pm_acc = (cross["pm_up"] == cross["actual_up"]).mean()

    print(f"\nT-{sec_rem}s: n={len(cross)}, agree={cross['agree'].mean()*100:.1f}%, "
          f"disagree={len(disagree)}")
    print(f"  Kalshi predicts PM: {k_acc*100:.1f}%")
    print(f"  PM predicts itself: {pm_acc*100:.1f}%")
    print(f"  Mean gap (K-PM): {cross['gap'].mean():.3f}, std: {cross['gap'].std():.3f}")

    if len(disagree) >= 10:
        k_right = (disagree["k_yes"] == disagree["actual_up"]).mean()
        pm_right = (disagree["pm_up"] == disagree["actual_up"]).mean()
        print(f"  DISAGREEMENT ({len(disagree)}):")
        print(f"    Kalshi right: {k_right*100:.1f}%")
        print(f"    PM right:     {pm_right*100:.1f}%")

        for gap_t in [0.10, 0.15, 0.20, 0.30]:
            # Kalshi higher → buy UP on PM
            k_hi = cross[cross["gap"] > gap_t]
            if len(k_hi) >= 5:
                wr = k_hi["actual_up"].mean()
                entry = k_hi["last_trade_price"].median()
                p = entry
                fee = p * 0.25 * (p * (1-p))**2
                ev = wr * (1 - entry - fee) - (1 - wr) * (entry + fee)
                m = "V" if ev > 0 else " "
                print(f"    {m} K>{gap_t:.0%} higher, buy PM UP: n={len(k_hi)}, "
                      f"UP wins {wr*100:.0f}%, entry~${entry:.2f}, EV~${ev:.4f}")

            # Kalshi lower → buy DOWN on PM
            k_lo = cross[cross["gap"] < -gap_t]
            if len(k_lo) >= 5:
                wr = (k_lo["actual_up"] == False).mean()
                up_ltp = k_lo["last_trade_price"].median()
                entry = 1 - up_ltp  # DOWN token price ≈ 1 - UP last trade
                p = entry
                fee = p * 0.25 * (p * (1-p))**2
                ev = wr * (1 - entry - fee) - (1 - wr) * (entry + fee)
                m = "V" if ev > 0 else " "
                print(f"    {m} K>{gap_t:.0%} lower, buy PM DOWN: n={len(k_lo)}, "
                      f"DOWN wins {wr*100:.0f}%, entry~${entry:.2f}, EV~${ev:.4f}")

# PM-only analysis: can we beat PM's own pricing?
print("\n" + "=" * 60)
print("PM-ONLY: Trade PM based on PM's own miscalibration")
print("=" * 60)

print("\nPM calibration shows 0.30-0.80 range is underpriced by 5-8%.")
print("If we buy the favored side when LTP is 0.50-0.80, do we profit?\n")

for sec_lo, sec_hi in [(400, 600), (200, 400), (60, 200)]:
    sel = pm_snaps[(pm_snaps["seconds_remaining"] >= sec_lo) &
                    (pm_snaps["seconds_remaining"] < sec_hi)]
    first = sel.sort_values("seconds_remaining", ascending=False).groupby("slug").first().reset_index()
    valid = first[(first["last_trade_price"] > 0) & (first["last_trade_price"].notna())].copy()
    valid["actual_up"] = (valid["pm_outcome"] == "up").astype(float)

    print(f"T-{sec_hi}s to T-{sec_lo}s (n={len(valid)}):")

    for lo, hi in [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90)]:
        # Buy UP when LTP > threshold
        up_bet = valid[(valid["last_trade_price"] >= lo) & (valid["last_trade_price"] < hi)]
        if len(up_bet) < 10:
            continue
        wr = up_bet["actual_up"].mean()
        entry = up_bet["last_trade_price"].median()
        p = entry
        fee = p * 0.25 * (p * (1-p))**2
        ev_up = wr * (1 - entry - fee) - (1 - wr) * (entry + fee)

        # Buy DOWN when (1-LTP) in range (= LTP in 1-hi to 1-lo)
        dn_bet = valid[(valid["last_trade_price"] >= (1-hi)) & (valid["last_trade_price"] < (1-lo))]
        if len(dn_bet) >= 10:
            wr_dn = (dn_bet["actual_up"] == False).mean()
            entry_dn = 1 - dn_bet["last_trade_price"].median()
            p_dn = entry_dn
            fee_dn = p_dn * 0.25 * (p_dn * (1-p_dn))**2
            ev_dn = wr_dn * (1 - entry_dn - fee_dn) - (1 - wr_dn) * (entry_dn + fee_dn)
        else:
            wr_dn, ev_dn, entry_dn = 0, 0, 0

        total_n = len(up_bet) + len(dn_bet)
        combined_wr = (up_bet["actual_up"].sum() + (dn_bet["actual_up"] == False).sum()) / total_n if total_n > 0 else 0
        combined_ev = (ev_up * len(up_bet) + ev_dn * len(dn_bet)) / total_n if total_n > 0 else 0

        m = "V" if combined_ev > 0 else " "
        print(f"  {m} favored in ${lo:.0%}-${hi:.0%}: n={total_n} ({len(up_bet)}U+{len(dn_bet)}D), "
              f"WR={combined_wr*100:.1f}%, EV≈${combined_ev:.4f}, "
              f"entry~${entry:.2f}")

# What about just beating the base rate with volume info?
print("\n" + "=" * 60)
print("PM VOLUME as signal — do high-volume rounds behave differently?")
print("=" * 60)

mid = pm_snaps[(pm_snaps["seconds_remaining"] >= 400) & (pm_snaps["seconds_remaining"] <= 600)]
first = mid.sort_values("seconds_remaining").groupby("slug").first().reset_index()
valid = first[first["volume"] > 0].copy()
valid["actual_up"] = (valid["pm_outcome"] == "up").astype(float)
valid["vol_q"] = pd.qcut(valid["volume"], 4, labels=["Q1_low","Q2","Q3","Q4_high"], duplicates="drop")

for q in ["Q1_low","Q2","Q3","Q4_high"]:
    sel = valid[valid["vol_q"] == q]
    if len(sel) < 10:
        continue
    up_rate = sel["actual_up"].mean()
    med_vol = sel["volume"].median()
    print(f"  {q}: n={len(sel)}, UP rate={up_rate*100:.1f}%, median vol=${med_vol:.0f}")
