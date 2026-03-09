"""Track individual signals through their full lifecycle.

For each round, check: at what time does the signal first appear?
Does it persist? What's the price at each checkpoint? What's the outcome?

This answers: "If we enter at T+X, does the direction hold until close?"
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "rounds"
SERIES_LIST = ["KXBTC15M", "KXETH15M", "KXSOL15M"]
SERIES_TO_COIN = {"KXBTC15M": "BTC", "KXETH15M": "ETH", "KXSOL15M": "SOL"}

FEE_COEFF = Decimal("0.07")
ONE_CENT = Decimal("0.01")

CHECKPOINTS = [120, 180, 240, 300, 360, 420, 480, 540, 600, 660, 720, 780, 840]
DIST_THRESHOLDS = [Decimal("0.002"), Decimal("0.003"), Decimal("0.004"), Decimal("0.005")]


def kalshi_fee(price: Decimal) -> Decimal:
    raw = FEE_COEFF * price * (Decimal("1") - price)
    return raw.quantize(ONE_CENT, rounding="ROUND_CEILING")


@dataclass
class RoundData:
    ticker: str
    series: str
    rows: list[dict] = field(default_factory=list)
    outcome: str | None = None


def load_rounds(filepath: Path, series: str) -> list[RoundData]:
    rounds_by_ticker: dict[str, RoundData] = {}
    with open(filepath) as f:
        for row in csv.DictReader(f):
            ticker = row["round_ticker"]
            if ticker not in rounds_by_ticker:
                rounds_by_ticker[ticker] = RoundData(ticker=ticker, series=series)
            rounds_by_ticker[ticker].rows.append(row)
            if row.get("row_type") == "round_end" and row.get("outcome"):
                rounds_by_ticker[ticker].outcome = row["outcome"]
    return list(rounds_by_ticker.values())


def get_snapshot_at(rd: RoundData, target_elapsed: int, tolerance: int = 15) -> dict | None:
    """Get the closest snapshot to target_elapsed seconds."""
    best = None
    best_diff = float("inf")
    for row in rd.rows:
        try:
            elapsed = float(row["seconds_elapsed"])
        except (ValueError, KeyError):
            continue
        diff = abs(elapsed - target_elapsed)
        if diff < best_diff and diff <= tolerance:
            best_diff = diff
            best = row
    return best


def analyze_round(rd: RoundData) -> dict | None:
    """For one round, track the signal at every checkpoint."""
    if rd.outcome is None:
        return None

    coin = SERIES_TO_COIN.get(rd.series, "?")

    # Get strike from any row
    strike = None
    for row in rd.rows:
        s = row.get("strike", "")
        if s:
            try:
                strike = Decimal(s)
                break
            except InvalidOperation:
                pass
    if not strike:
        return None

    checkpoints = {}
    for t in CHECKPOINTS:
        snap = get_snapshot_at(rd, t)
        if snap is None:
            continue
        spot_str = snap.get("spot_price", "")
        yes_ask_str = snap.get("yes_ask", "")
        no_ask_str = snap.get("no_ask", "")
        if not spot_str or not yes_ask_str:
            continue
        try:
            spot = Decimal(spot_str)
            yes_ask = Decimal(yes_ask_str)
        except InvalidOperation:
            continue

        dist = abs(spot - strike) / strike if strike else Decimal("0")
        direction = "YES" if spot > strike else "NO"

        if direction == "YES":
            entry_price = yes_ask
        else:
            if no_ask_str:
                try:
                    entry_price = Decimal(no_ask_str)
                    if entry_price <= 0 or entry_price >= 1:
                        entry_price = Decimal("1") - yes_ask
                except InvalidOperation:
                    entry_price = Decimal("1") - yes_ask
            else:
                entry_price = Decimal("1") - yes_ask

        won = direction.lower() == rd.outcome
        fee = kalshi_fee(entry_price)
        if won:
            pnl = Decimal("1") - entry_price - fee
        else:
            pnl = -entry_price - fee

        checkpoints[t] = {
            "spot": spot,
            "dist": dist,
            "direction": direction,
            "entry_price": entry_price,
            "won": won,
            "pnl": float(pnl),
        }

    if not checkpoints:
        return None

    return {
        "ticker": rd.ticker,
        "coin": coin,
        "outcome": rd.outcome,
        "strike": strike,
        "checkpoints": checkpoints,
    }


def main() -> None:
    dates = ["2026-03-08", "2026-03-09"]

    all_round_data = []
    for date in dates:
        for series in SERIES_LIST:
            filepath = DATA_DIR / f"{series}-{date}.csv"
            if not filepath.exists():
                continue
            for rd in load_rounds(filepath, series):
                result = analyze_round(rd)
                if result:
                    all_round_data.append(result)

    print(f"Analyzed {len(all_round_data)} rounds across {len(dates)} days\n")

    # Analysis 1: At each checkpoint, IF distance > threshold, what's accuracy and avg price?
    for dist_thresh in DIST_THRESHOLDS:
        print(f"{'=' * 85}")
        print(f"DISTANCE THRESHOLD: {float(dist_thresh)*100:.1f}%")
        print(f"{'=' * 85}")
        print(f"{'Time':>6s} {'Qualifying':>11s} {'Accuracy':>9s} {'AvgPrice':>9s} {'AvgPnL':>8s} {'TotalPnL':>9s} {'AvgDist':>8s}")
        print("-" * 70)

        for t in CHECKPOINTS:
            qualifying = []
            for rd in all_round_data:
                cp = rd["checkpoints"].get(t)
                if cp and cp["dist"] >= dist_thresh:
                    qualifying.append(cp)

            if not qualifying:
                continue

            wins = sum(1 for q in qualifying if q["won"])
            accuracy = wins / len(qualifying) * 100
            avg_price = sum(float(q["entry_price"]) for q in qualifying) / len(qualifying)
            avg_pnl = sum(q["pnl"] for q in qualifying) / len(qualifying)
            total_pnl = sum(q["pnl"] for q in qualifying)
            avg_dist = sum(float(q["dist"]) for q in qualifying) / len(qualifying)
            flag = " ✓" if avg_pnl > 0 else ""

            print(
                f"  T+{t:>3d} {len(qualifying):>11d} "
                f"{accuracy:>8.1f}% ${avg_price:>7.3f}  "
                f"${avg_pnl:>+6.4f}  ${total_pnl:>+7.2f} "
                f"{avg_dist*100:>6.2f}%{flag}"
            )

    # Analysis 2: Signal persistence — if signal appears at T+X, does it still exist at T+600?
    print(f"\n{'=' * 85}")
    print("SIGNAL PERSISTENCE: If dist > 0.2% at T+X, what % still have dist > 0.2% at T+600?")
    print(f"{'=' * 85}")
    print(f"{'Entry':>6s} {'Has Signal':>11s} {'Still@600':>10s} {'Persist%':>9s} {'Same Dir':>9s} {'DirPersist%':>12s}")
    print("-" * 65)

    for entry_t in [180, 240, 300, 360, 420, 480, 540]:
        has_signal = 0
        still_at_600 = 0
        same_direction = 0

        for rd in all_round_data:
            cp_entry = rd["checkpoints"].get(entry_t)
            cp_600 = rd["checkpoints"].get(600)
            if not cp_entry or cp_entry["dist"] < Decimal("0.002"):
                continue
            has_signal += 1
            if cp_600 and cp_600["dist"] >= Decimal("0.002"):
                still_at_600 += 1
                if cp_600["direction"] == cp_entry["direction"]:
                    same_direction += 1

        if has_signal == 0:
            continue
        persist = still_at_600 / has_signal * 100
        dir_persist = same_direction / has_signal * 100 if has_signal else 0

        print(
            f"  T+{entry_t:>3d} {has_signal:>11d} "
            f"{still_at_600:>10d} {persist:>8.1f}% "
            f"{same_direction:>9d} {dir_persist:>10.1f}%"
        )

    # Analysis 3: Direction flips — how often does the direction at entry match outcome?
    print(f"\n{'=' * 85}")
    print("DIRECTION ACCURACY BY ENTRY TIME (dist > 0.2% at entry time)")
    print("Does the direction at time T predict the final outcome?")
    print(f"{'=' * 85}")
    print(f"{'Entry':>6s} {'Signals':>8s} {'Correct':>8s} {'Accuracy':>9s} {'Flipped':>8s}")
    print("-" * 45)

    for t in CHECKPOINTS:
        signals = 0
        correct = 0
        for rd in all_round_data:
            cp = rd["checkpoints"].get(t)
            if not cp or cp["dist"] < Decimal("0.002"):
                continue
            signals += 1
            if cp["won"]:
                correct += 1

        if signals == 0:
            continue
        flipped = signals - correct
        print(
            f"  T+{t:>3d} {signals:>8d} {correct:>8d} "
            f"{correct/signals*100:>8.1f}% {flipped:>8d}"
        )

    # Analysis 4: The core question — if we enter at T+300, what price do we get vs T+600?
    print(f"\n{'=' * 85}")
    print("SAME-ROUND COMPARISON: Price at T+300 vs T+600 for rounds with signals at both")
    print(f"{'=' * 85}")

    both_count = 0
    price_300_total = Decimal("0")
    price_600_total = Decimal("0")
    early_better = 0
    same_direction_count = 0
    early_won = 0
    late_won = 0

    for rd in all_round_data:
        cp300 = rd["checkpoints"].get(300)
        cp600 = rd["checkpoints"].get(600)
        if not cp300 or not cp600:
            continue
        if cp300["dist"] < Decimal("0.002") or cp600["dist"] < Decimal("0.002"):
            continue

        both_count += 1
        price_300_total += cp300["entry_price"]
        price_600_total += cp600["entry_price"]

        if cp300["entry_price"] < cp600["entry_price"]:
            early_better += 1
        if cp300["direction"] == cp600["direction"]:
            same_direction_count += 1
        if cp300["won"]:
            early_won += 1
        if cp600["won"]:
            late_won += 1

    if both_count > 0:
        print(f"  Rounds with signal at both T+300 and T+600: {both_count}")
        print(f"  Avg price at T+300: ${price_300_total/both_count:.3f}")
        print(f"  Avg price at T+600: ${price_600_total/both_count:.3f}")
        print(f"  Price cheaper at T+300: {early_better}/{both_count} ({early_better/both_count*100:.0f}%)")
        print(f"  Same direction at both: {same_direction_count}/{both_count} ({same_direction_count/both_count*100:.0f}%)")
        print(f"  Accuracy at T+300: {early_won}/{both_count} ({early_won/both_count*100:.0f}%)")
        print(f"  Accuracy at T+600: {late_won}/{both_count} ({late_won/both_count*100:.0f}%)")
        print(f"  Price savings: ${(price_600_total - price_300_total)/both_count:.3f}/contract avg")
    else:
        print("  No rounds with signals at both checkpoints.")


if __name__ == "__main__":
    main()
