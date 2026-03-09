"""Deeper P&L analysis — infer win/loss from balance changes."""

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


def analyze_pnl() -> None:
    rows = load_all()

    # Group by round (use timestamp of ROUND_SUMMARY, deduplicated)
    # A "round" = set of trades between two consecutive ROUND_SUMMARY timestamps
    summaries = []
    seen_ts = set()
    for r in rows:
        if r["strategy"] == "ROUND_SUMMARY":
            ts = r["timestamp"][:19]
            if ts not in seen_ts:
                seen_ts.add(ts)
                summaries.append(r)

    # Extract balance at each round end
    round_balances = []
    for s in summaries:
        bal = float(s["balance_after"]) if s["balance_after"] else None
        reason = s.get("reason", "")
        trades = int(reason.split("trades=")[1]) if "trades=" in reason else 0
        signals = int(reason.split("signals=")[1].split(" ")[0]) if "signals=" in reason else 0
        ts = datetime.fromisoformat(s["timestamp"]).astimezone(CST)
        round_balances.append({
            "ts": ts,
            "balance": bal,
            "trades": trades,
            "signals": signals,
        })

    # Compute per-round P&L (balance delta between consecutive rounds)
    print("=== PER-ROUND P&L (traded rounds only) ===\n")
    print(f"{'Time CST':>12s} {'Trades':>6s} {'Balance':>8s} {'P&L':>8s} {'Result':>8s}")
    print("-" * 50)

    wins = 0
    losses = 0
    breakevens = 0
    win_pnl = Decimal("0")
    loss_pnl = Decimal("0")

    # By hour
    hour_wins: dict[int, int] = defaultdict(int)
    hour_losses: dict[int, int] = defaultdict(int)
    hour_pnl: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))

    prev_bal = None
    for rb in round_balances:
        if prev_bal is not None and rb["trades"] > 0:
            pnl = Decimal(str(rb["balance"])) - Decimal(str(prev_bal))
            if pnl > 0:
                result = "WIN"
                wins += 1
                win_pnl += pnl
                hour_wins[rb["ts"].hour] += 1
            elif pnl < 0:
                result = "LOSS"
                losses += 1
                loss_pnl += pnl
                hour_losses[rb["ts"].hour] += 1
            else:
                result = "EVEN"
                breakevens += 1
            hour_pnl[rb["ts"].hour] += pnl
            print(
                f"{rb['ts'].strftime('%m/%d %H:%M'):>12s} "
                f"{rb['trades']:>6d} "
                f"${rb['balance']:>7.2f} "
                f"${pnl:>+7.2f} "
                f"{result:>8s}"
            )
        prev_bal = rb["balance"]

    total = wins + losses + breakevens
    print(f"\n=== SUMMARY ===")
    print(f"Traded rounds: {total}")
    print(f"Wins: {wins} ({wins/total*100:.0f}%)  |  Losses: {losses} ({losses/total*100:.0f}%)")
    print(f"Avg win:  ${win_pnl/wins:+.2f}" if wins else "Avg win:  N/A")
    print(f"Avg loss: ${loss_pnl/losses:+.2f}" if losses else "Avg loss: N/A")
    print(f"Win P&L:  ${win_pnl:+.2f}  |  Loss P&L: ${loss_pnl:+.2f}")
    print(f"Net P&L:  ${win_pnl + loss_pnl:+.2f}")
    if wins and losses:
        print(f"Avg win/avg loss ratio: {abs(float(win_pnl/wins) / float(loss_pnl/losses)):.2f}")

    # By hour
    print(f"\n=== WIN RATE BY HOUR (CST) ===")
    print(f"{'Hour':>6s} {'Wins':>5s} {'Losses':>7s} {'WR%':>5s} {'P&L':>8s}")
    print("-" * 35)
    all_hours = sorted(set(list(hour_wins.keys()) + list(hour_losses.keys())))
    for h in all_hours:
        w = hour_wins[h]
        l = hour_losses[h]
        t = w + l
        wr = w / t * 100 if t else 0
        pnl = hour_pnl[h]
        flag = " ←" if pnl < Decimal("-1") else ""
        print(f"  {h:02d}:00 {w:>5d} {l:>7d} {wr:>4.0f}% ${pnl:>+7.2f}{flag}")

    # Consecutive loss streaks
    print(f"\n=== LOSS STREAKS ===")
    streak = 0
    max_streak = 0
    streaks = []
    for rb in round_balances:
        if prev_bal is not None and rb["trades"] > 0:
            pnl = Decimal(str(rb["balance"])) - Decimal(str(prev_bal))
            if pnl < 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                if streak > 0:
                    streaks.append(streak)
                streak = 0
        prev_bal = rb["balance"]
    if streak > 0:
        streaks.append(streak)
    print(f"Max consecutive losses: {max_streak}")
    print(f"Loss streaks: {sorted(streaks, reverse=True)[:10]}")

    # Entry price vs outcome
    print(f"\n=== BREAK-EVEN WIN RATES BY PRICE ===")
    print(f"(What win rate you need to break even at each price, ignoring fees)")
    for p in [0.85, 0.88, 0.90, 0.92, 0.94, 0.95, 0.96, 0.97]:
        be_wr = p / 1.0 * 100  # Binary: need to win p% of the time to break even
        print(f"  ${p:.2f} entry → need {be_wr:.0f}% win rate (profit ${1-p:.2f} per win, lose ${p:.2f} per loss)")


if __name__ == "__main__":
    analyze_pnl()
