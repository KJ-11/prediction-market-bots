"""Quick verification: is PM data inverted?

Check: when outcome="up", does the up_token price go HIGH (correct) or LOW (inverted)?
Also check: does spot price actually go up when outcome="up"?
"""
from __future__ import annotations

import pandas as pd
from pathlib import Path

PM_DIR = Path("data/rounds/polymarket")

for dur in ["5m", "15m"]:
    print(f"\n{'='*60}")
    print(f"PM {dur} Markets")
    print(f"{'='*60}")

    frames = []
    for f in sorted(PM_DIR.glob(f"*-{dur}-*.csv")):
        df = pd.read_csv(f, engine="python", on_bad_lines="skip")
        frames.append(df)

    if not frames:
        print("No data")
        continue

    all_df = pd.concat(frames, ignore_index=True)
    for col in ["up_bid", "up_ask", "down_bid", "down_ask", "up_midpoint",
                 "spot_price", "rtds_price", "seconds_remaining"]:
        if col in all_df.columns:
            all_df[col] = pd.to_numeric(all_df[col], errors="coerce")

    ends = all_df[all_df["row_type"].str.contains("end|resolved", case=False, na=False)].copy()
    snaps = all_df[all_df["row_type"] == "snapshot"]

    print(f"Total round_end rows: {len(ends)}")
    print(f"Outcomes: {ends['outcome'].value_counts().to_dict()}")

    # Check 1: When outcome="up", what's the up_bid at round_end?
    print("\n--- Check 1: Up_bid at round_end by outcome ---")
    for outcome in ["up", "down", "unknown"]:
        sel = ends[ends["outcome"] == outcome]
        if len(sel) > 0:
            print(f"  outcome={outcome}: n={len(sel)}, "
                  f"up_bid median={sel['up_bid'].median():.3f}, "
                  f"up_bid mean={sel['up_bid'].mean():.3f}, "
                  f"up_ask median={sel['up_ask'].median():.3f}")

    # Check 2: For rounds with outcome, look at the FIRST and LAST snapshots
    # to see if spot actually went up when outcome="up"
    print("\n--- Check 2: Did spot actually go up when outcome='up'? ---")
    known = ends[ends["outcome"].isin(["up", "down"])].copy()

    matched = 0
    mismatched = 0
    spot_data = []

    for _, end_row in known.iterrows():
        slug = end_row["slug"]
        round_snaps = snaps[snaps["slug"] == slug].sort_values("seconds_remaining", ascending=False)
        if len(round_snaps) < 5:
            continue

        # First snapshot (highest seconds_remaining) = start of round
        # Last snapshot (lowest seconds_remaining) = near end of round
        first_spot = round_snaps.iloc[0]["spot_price"]
        last_spot = round_snaps.iloc[-1]["spot_price"]
        first_rtds = round_snaps.iloc[0]["rtds_price"]
        last_rtds = round_snaps.iloc[-1]["rtds_price"]

        if pd.isna(first_spot) or pd.isna(last_spot):
            continue

        spot_went_up = last_spot > first_spot
        outcome_up = end_row["outcome"] == "up"

        if spot_went_up == outcome_up:
            matched += 1
        else:
            mismatched += 1

        spot_data.append({
            "slug": slug,
            "outcome": end_row["outcome"],
            "first_spot": first_spot,
            "last_spot": last_spot,
            "spot_went_up": spot_went_up,
            "first_rtds": first_rtds,
            "last_rtds": last_rtds,
            "rtds_went_up": last_rtds > first_rtds if not pd.isna(first_rtds) and not pd.isna(last_rtds) else None,
            "up_bid_end": end_row["up_bid"],
        })

    total = matched + mismatched
    print(f"  Spot direction matches outcome: {matched}/{total} ({100*matched/max(1,total):.1f}%)")
    print(f"  Spot direction CONTRADICTS outcome: {mismatched}/{total} ({100*mismatched/max(1,total):.1f}%)")

    # Check 3: RTDS (Binance) price direction
    spot_df = pd.DataFrame(spot_data)
    if len(spot_df) > 0 and "rtds_went_up" in spot_df.columns:
        rtds_valid = spot_df.dropna(subset=["rtds_went_up"])
        if len(rtds_valid) > 0:
            rtds_match = ((rtds_valid["rtds_went_up"]) == (rtds_valid["outcome"] == "up")).sum()
            print(f"\n  RTDS direction matches outcome: {rtds_match}/{len(rtds_valid)} "
                  f"({100*rtds_match/len(rtds_valid):.1f}%)")

    # Check 4: Show some specific examples
    print("\n--- Examples (first 10 resolved rounds) ---")
    for _, row in spot_df.head(10).iterrows():
        print(f"  {row['slug']}: outcome={row['outcome']}, "
              f"spot {row['first_spot']:.2f}→{row['last_spot']:.2f} "
              f"({'UP' if row['spot_went_up'] else 'DOWN'}), "
              f"up_bid_end={row['up_bid_end']:.3f}")

    # Check 5: The midpoint analysis from our v3 script was inverted.
    # Let's check: when up_midpoint > 0.5, does outcome tend to be "up" or "down"?
    print("\n--- Check 5: up_midpoint > 0.5 → which outcome? ---")
    mid_snaps = snaps[(snaps["up_bid"] > 0.05) & (snaps["up_ask"] < 0.95)].copy()
    mid_snaps = mid_snaps.merge(
        known[["slug", "outcome"]].rename(columns={"outcome": "round_outcome"}),
        on="slug", how="inner"
    )

    if len(mid_snaps) > 0:
        mid_snaps["mid_says_up"] = mid_snaps["up_midpoint"] > 0.5
        mid_snaps["actual_up"] = mid_snaps["round_outcome"] == "up"
        agree = (mid_snaps["mid_says_up"] == mid_snaps["actual_up"]).mean()
        disagree = (mid_snaps["mid_says_up"] != mid_snaps["actual_up"]).mean()
        print(f"  up_midpoint > 0.5 predicts outcome='up': {agree*100:.1f}%")
        print(f"  up_midpoint > 0.5 predicts outcome='down': {disagree*100:.1f}%")
        print(f"  (n={len(mid_snaps)} quoted snapshots)")

        # Now check: does up_midpoint > 0.5 predict spot going UP?
        mid_first = mid_snaps.sort_values("seconds_remaining", ascending=False).groupby("slug").first().reset_index()
        if len(mid_first) > 10:
            # Get last spot from snapshots
            last_spots = snaps.sort_values("seconds_remaining").groupby("slug").first()[["spot_price"]].rename(
                columns={"spot_price": "last_spot"})
            mid_first = mid_first.merge(last_spots, on="slug", how="inner")
            mid_first["spot_went_up"] = mid_first["last_spot"] > mid_first["spot_price"]
            mid_up_predicts_spot = (mid_first["mid_says_up"] == mid_first["spot_went_up"]).mean()
            print(f"  up_midpoint > 0.5 predicts spot going up: {mid_up_predicts_spot*100:.1f}% (n={len(mid_first)})")
