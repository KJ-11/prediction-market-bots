"""Check if the strategy's directional predictions were actually correct.

For each filled trade, we know:
  - What we bet (YES or NO)
  - The entry price
  - The distance from strike

We DON'T have direct win/loss per trade in the CSV, but we can infer
from round-level balance changes (for rounds with exactly 1 trade).

For multi-trade rounds, we compute expected P&L to cross-check.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

CST = ZoneInfo("America/Chicago")


def load_all(data_dir: str = "data/trades") -> list[dict]:
    rows = []
    for f in sorted(Path(data_dir).glob("*.csv")):
        with open(f) as fh:
            for row in csv.DictReader(fh):
                rows.append(row)
    return rows


def analyze() -> None:
    rows = load_all()

    # Group rows into rounds by looking at ROUND_SUMMARY timestamps
    # Build: list of {summary_ts, trades: [...], balance_after, prev_balance}
    rounds = []
    current_trades = []
    prev_balance = None

    for row in rows:
        if row["strategy"] == "ROUND_SUMMARY":
            ts_key = row["timestamp"][:19]
            bal = float(row["balance_after"]) if row["balance_after"] else None
            reason = row.get("reason", "")
            trade_count = int(reason.split("trades=")[1]) if "trades=" in reason else 0

            # Only process first summary per round (3 per round, one per coin)
            if rounds and rounds[-1]["ts_key"] == ts_key:
                continue

            rounds.append({
                "ts_key": ts_key,
                "ts": datetime.fromisoformat(row["timestamp"]).astimezone(CST),
                "trades": list(current_trades),
                "trade_count": trade_count,
                "balance": bal,
                "prev_balance": prev_balance,
            })
            current_trades = []
            prev_balance = bal
        elif row.get("strategy") and row["strategy"] != "ROUND_SUMMARY":
            current_trades.append(row)

    # For single-trade rounds, we can definitively determine win/loss
    print("=== SINGLE-TRADE ROUNDS (definitive win/loss) ===\n")
    print(f"{'Time':>12s} {'Coin':>4s} {'Out':>3s} {'Price':>6s} {'Dist':>7s} {'ΔBal':>7s} {'Result':>6s}")
    print("-" * 55)

    single_wins = 0
    single_losses = 0
    single_details = []

    for rd in rounds:
        if rd["trade_count"] != 1 or not rd["trades"]:
            continue
        if rd["prev_balance"] is None:
            continue

        trade = None
        for t in rd["trades"]:
            if t["status"] == "filled":
                trade = t
                break
        if trade is None:
            continue

        pnl = rd["balance"] - rd["prev_balance"]
        won = pnl > 0

        ticker = trade["round_ticker"]
        coin = "BTC" if "BTC" in ticker else "ETH" if "ETH" in ticker else "SOL" if "SOL" in ticker else "?"
        outcome = trade["outcome"]
        price = float(trade["price"])
        reason = trade.get("reason", "")
        dist = ""
        dist_val = 0.0
        if "dist=" in reason:
            dist = reason.split("dist=")[1].split(" ")[0]
            dist_val = float(dist)

        result = "WIN" if won else "LOSS"
        if won:
            single_wins += 1
        else:
            single_losses += 1

        single_details.append({
            "coin": coin, "outcome": outcome, "price": price,
            "dist": dist_val, "won": won, "pnl": pnl,
        })

        print(
            f"{rd['ts'].strftime('%m/%d %H:%M'):>12s} "
            f"{coin:>4s} {outcome:>3s} "
            f"${price:>5.2f} {dist:>7s} "
            f"${pnl:>+6.2f} {result:>6s}"
        )

    total_single = single_wins + single_losses
    print(f"\nSingle-trade rounds: {total_single}")
    print(f"Wins: {single_wins} ({single_wins/total_single*100:.0f}%)  |  Losses: {single_losses} ({single_losses/total_single*100:.0f}%)")

    # Now analyze ALL filled trades regardless of round grouping
    print(f"\n\n=== ALL FILLED TRADES — CHARACTERISTICS ===\n")

    filled = [r for r in load_all() if r.get("strategy") and r["strategy"] != "ROUND_SUMMARY" and r["status"] == "filled"]

    # Strategy claims 98.5% confidence. What does Kelly give at various prices?
    print("=== KELLY SIZING AT 98.5% CONFIDENCE ===")
    print("(What the sizer actually computes at different price levels)\n")
    print(f"{'Price':>7s} {'Fee':>6s} {'Cost':>6s} {'NetWin':>7s} {'Kelly%':>7s} {'¼Kelly%':>8s} {'Contracts@$15':>14s}")
    print("-" * 65)

    for p_cents in [80, 85, 88, 90, 92, 94, 95, 96, 97]:
        p = Decimal(str(p_cents)) / Decimal("100")
        fee = (Decimal("0.07") * p * (Decimal("1") - p)).quantize(Decimal("0.01"), rounding="ROUND_CEILING")
        cost = p + fee
        net_win = Decimal("1") - p - fee
        if net_win <= 0:
            print(f"  ${p:.2f}   ${fee:.2f}  ${cost:.2f}  ${net_win:.2f}    N/A (neg EV)")
            continue
        b = float(net_win / cost)
        conf = 0.985
        kelly_f = (conf * b - (1 - conf)) / b
        qkelly_f = kelly_f * 0.25
        contracts_15 = int(15 * qkelly_f / float(cost))
        print(
            f"  ${p:.2f}   ${fee:.2f}  ${cost:.2f}  ${net_win:.2f}  "
            f"{kelly_f*100:>6.1f}%  {qkelly_f*100:>7.1f}%  {contracts_15:>13d}"
        )

    # Key insight: what's the actual confidence needed to break even?
    print(f"\n=== ACTUAL BREAK-EVEN CONFIDENCE NEEDED ===")
    print(f"(Including fees)\n")
    for p_cents in [80, 85, 88, 90, 92, 94, 95, 96, 97]:
        p = Decimal(str(p_cents)) / Decimal("100")
        fee = (Decimal("0.07") * p * (Decimal("1") - p)).quantize(Decimal("0.01"), rounding="ROUND_CEILING")
        cost = float(p + fee)
        net_win = float(Decimal("1") - p - fee)
        if net_win <= 0:
            print(f"  ${p:.2f}  → impossible (fee eats all profit)")
            continue
        # break even: p_win * net_win = (1 - p_win) * cost
        # p_win = cost / (cost + net_win)
        be = cost / (cost + net_win)
        print(f"  ${p:.2f}  → need {be*100:.1f}% accuracy (fee=${fee:.3f})")

    # What does the 98.5% confidence claim imply?
    print(f"\n=== THE CONFIDENCE=0.985 PROBLEM ===")
    print(f"Strategy hardcodes confidence=0.985 (98.5% win rate)")
    print(f"This means Kelly thinks EVERY trade has massive edge")
    print(f"Actual observed round win rate: ~34%")
    print(f"")
    print(f"At 34% actual win rate, expected P&L per $0.92 trade:")
    print(f"  Win: 0.34 × $0.08 = $0.027")
    print(f"  Loss: 0.66 × $0.92 = $0.607")
    print(f"  Net: -$0.58 per trade")
    print(f"")
    print(f"The 98.5% figure came from backtesting with PERFECT information")
    print(f"(knowing spot at T+600-800 and checking outcome at T+900).")
    print(f"Live trading has:")
    print(f"  - Price movement during execution")
    print(f"  - Market already pricing in the same signal")
    print(f"  - Multiple coins trading against thin books")

    # Distribution of actual entry prices
    print(f"\n=== WHAT WE ACTUALLY PAID (entry prices) ===")
    prices = [float(t["price"]) for t in filled]
    yes_prices = [float(t["price"]) for t in filled if t["outcome"] == "yes"]
    no_prices = [float(t["price"]) for t in filled if t["outcome"] == "no"]

    print(f"  All:  avg=${sum(prices)/len(prices):.3f}, min=${min(prices):.2f}, max=${max(prices):.2f}")
    if yes_prices:
        print(f"  YES:  avg=${sum(yes_prices)/len(yes_prices):.3f}, min=${min(yes_prices):.2f}, max=${max(yes_prices):.2f}")
    if no_prices:
        print(f"  NO:   avg=${sum(no_prices)/len(no_prices):.3f}, min=${min(no_prices):.2f}, max=${max(no_prices):.2f}")

    # The strategy uses the ASK price. Let's see how that relates to distance
    print(f"\n=== DISTANCE vs ENTRY PRICE ===")
    print(f"(Does higher distance = lower price = better entry?)\n")
    dist_price: dict[str, list] = defaultdict(list)
    for t in filled:
        reason = t.get("reason", "")
        if "dist=" in reason:
            d = float(reason.split("dist=")[1].split(" ")[0])
            p = float(t["price"])
            if d >= 0.005:
                bucket = "0.50%+"
            elif d >= 0.004:
                bucket = "0.40-0.49%"
            elif d >= 0.003:
                bucket = "0.30-0.39%"
            else:
                bucket = "0.20-0.29%"
            dist_price[bucket].append(p)

    for bucket in ["0.20-0.29%", "0.30-0.39%", "0.40-0.49%", "0.50%+"]:
        prices_in = dist_price.get(bucket, [])
        if prices_in:
            avg = sum(prices_in) / len(prices_in)
            print(f"  {bucket:>12s}: n={len(prices_in):2d}, avg price=${avg:.3f}, range ${min(prices_in):.2f}-${max(prices_in):.2f}")


if __name__ == "__main__":
    analyze()
