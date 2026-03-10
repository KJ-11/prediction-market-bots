"""Simulate how the T+300-540 strategy scales over time.

Uses the backtest data from Mar 8-9 to project forward at different
balance levels, with quarter-Kelly sizing and Kalshi fees.
"""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "rounds"
SERIES_LIST = ["KXBTC15M", "KXETH15M", "KXSOL15M"]
SERIES_TO_COIN = {"KXBTC15M": "BTC", "KXETH15M": "ETH", "KXSOL15M": "SOL"}

FEE_COEFF = Decimal("0.07")
ONE_CENT = Decimal("0.01")
CONFIDENCE = 0.88
KELLY_FRACTION = 0.25

WINDOW_START = 300
WINDOW_END = 540
DIST_THRESHOLD = Decimal("0.002")


def kalshi_fee(price: Decimal, contracts: int = 1) -> Decimal:
    raw = FEE_COEFF * contracts * price * (Decimal("1") - price)
    return raw.quantize(ONE_CENT, rounding="ROUND_CEILING")


def kelly_size(price: Decimal, balance: Decimal) -> int:
    fee = kalshi_fee(price)
    cost = price + fee
    net_win = Decimal("1") - price - fee
    if net_win <= 0:
        return 0
    b = float(net_win / cost)
    p = CONFIDENCE
    kelly_f = (p * b - (1 - p)) / b if b > 0 else 0.0
    kelly_f *= KELLY_FRACTION
    if kelly_f <= 0:
        return 0
    dollar_size = float(balance) * kelly_f
    contracts = int(dollar_size / float(cost))
    # Verify affordability
    while contracts > 0:
        total = price * contracts + kalshi_fee(price, contracts)
        if total <= balance:
            break
        contracts -= 1
    return max(contracts, 0)


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


def find_signal(rd: RoundData) -> dict | None:
    for row in rd.rows:
        try:
            elapsed = float(row["seconds_elapsed"])
        except (ValueError, KeyError):
            continue
        if elapsed < WINDOW_START or elapsed > WINDOW_END:
            continue
        spot_str = row.get("spot_price", "")
        strike_str = row.get("strike", "")
        yes_ask_str = row.get("yes_ask", "")
        no_ask_str = row.get("no_ask", "")
        if not spot_str or not strike_str or not yes_ask_str:
            continue
        try:
            spot = Decimal(spot_str)
            strike = Decimal(strike_str)
            yes_ask = Decimal(yes_ask_str)
        except InvalidOperation:
            continue
        if strike == 0 or yes_ask == 0:
            continue
        dist = abs(spot - strike) / strike
        if dist < DIST_THRESHOLD:
            continue
        if spot > strike:
            side = "YES"
            price = yes_ask
        else:
            side = "NO"
            if no_ask_str:
                try:
                    price = Decimal(no_ask_str)
                    if price <= 0 or price >= 1:
                        price = Decimal("1") - yes_ask
                except InvalidOperation:
                    price = Decimal("1") - yes_ask
            else:
                price = Decimal("1") - yes_ask
        if price <= 0 or price >= 1:
            continue
        won = side.lower() == rd.outcome
        return {"side": side, "price": price, "dist": dist, "won": won}
    return None


def load_all_signals(dates: list[str]) -> list[dict]:
    """Load all signals grouped by time slot for multi-coin rounds."""
    def round_slot(ticker: str) -> str:
        for s in SERIES_LIST:
            if ticker.startswith(s):
                return ticker[len(s) + 1:]
        return ticker

    slots: dict[str, list[tuple[str, RoundData]]] = defaultdict(list)
    for date in dates:
        for series in SERIES_LIST:
            filepath = DATA_DIR / f"{series}-{date}.csv"
            if not filepath.exists():
                continue
            for rd in load_rounds(filepath, series):
                if rd.outcome is None:
                    continue
                slot = round_slot(rd.ticker)
                slots[slot].append((series, rd))

    # For each time slot, find signals (up to 3, one per coin)
    all_round_signals = []
    for slot in sorted(slots.keys()):
        round_sigs = []
        for series, rd in slots[slot]:
            sig = find_signal(rd)
            if sig:
                sig["coin"] = SERIES_TO_COIN.get(series, "?")
                round_sigs.append(sig)
        if round_sigs:
            all_round_signals.append(round_sigs)

    return all_round_signals


def simulate(
    round_signals: list[list[dict]],
    starting_balance: Decimal,
    max_trades_per_round: int = 3,
) -> dict:
    balance = starting_balance
    peak = balance
    trades = 0
    wins = 0
    total_pnl = Decimal("0")
    balance_history = [float(balance)]

    for round_sigs in round_signals:
        round_trades = 0
        for sig in round_sigs[:max_trades_per_round]:
            price = sig["price"]
            contracts = kelly_size(price, balance)
            if contracts <= 0:
                continue

            fee = kalshi_fee(price, contracts)
            cost = price * contracts + fee

            if sig["won"]:
                pnl = (Decimal("1") - price) * contracts - fee
                wins += 1
            else:
                pnl = -price * contracts - fee

            balance += pnl
            total_pnl += pnl
            trades += 1
            round_trades += 1

            if balance > peak:
                peak = balance

        balance_history.append(float(balance))

    return {
        "starting": float(starting_balance),
        "ending": float(balance),
        "peak": float(peak),
        "trough": min(balance_history),
        "trades": trades,
        "wins": wins,
        "win_rate": wins / trades * 100 if trades else 0,
        "total_pnl": float(total_pnl),
        "pnl_per_trade": float(total_pnl / trades) if trades else 0,
        "history": balance_history,
    }


def load_fill_capacities(dates: list[str]) -> list[int]:
    """Load actual 60s fill capacities from round data for liquidity capping.

    For each round with a signal, measure the volume traded in the 60s
    after the signal fires. This is the empirical cap on how many contracts
    we could realistically fill per trade.
    """
    capacities: list[int] = []
    for date in dates:
        for series in SERIES_LIST:
            filepath = DATA_DIR / f"{series}-{date}.csv"
            if not filepath.exists():
                continue
            for rd in load_rounds(filepath, series):
                if rd.outcome is None:
                    continue
                # Find signal entry time
                entry_elapsed = None
                for row in rd.rows:
                    try:
                        elapsed = float(row["seconds_elapsed"])
                    except (ValueError, KeyError):
                        continue
                    if elapsed < WINDOW_START or elapsed > WINDOW_END:
                        continue
                    spot_str = row.get("spot_price", "")
                    strike_str = row.get("strike", "")
                    if not spot_str or not strike_str:
                        continue
                    try:
                        spot = Decimal(spot_str)
                        strike = Decimal(strike_str)
                    except InvalidOperation:
                        continue
                    if strike == 0:
                        continue
                    dist = abs(spot - strike) / strike
                    if dist >= DIST_THRESHOLD:
                        entry_elapsed = elapsed
                        break
                if entry_elapsed is None:
                    continue

                # Measure volume in 60s after entry
                vol_at_entry = None
                vol_after = None
                for row in rd.rows:
                    try:
                        elapsed = float(row["seconds_elapsed"])
                    except (ValueError, KeyError):
                        continue
                    vol_str = row.get("volume", "")
                    if not vol_str:
                        continue
                    try:
                        vol = int(float(vol_str))
                    except (ValueError, TypeError):
                        continue
                    if abs(elapsed - entry_elapsed) <= 3:
                        if vol_at_entry is None or abs(elapsed - entry_elapsed) < abs(vol_at_entry[1] - entry_elapsed):
                            vol_at_entry = (vol, elapsed)
                    target = entry_elapsed + 60
                    if abs(elapsed - target) <= 5:
                        if vol_after is None or abs(elapsed - target) < abs(vol_after[1] - target):
                            vol_after = (vol, elapsed)
                if vol_at_entry is not None and vol_after is not None:
                    delta = vol_after[0] - vol_at_entry[0]
                    if delta > 0:
                        capacities.append(delta)
    return capacities


def monte_carlo(
    round_signals: list[list[dict]],
    starting_balance: Decimal,
    num_days: int = 30,
    num_sims: int = 1000,
    max_contracts: int | None = None,
    fill_capacities: list[int] | None = None,
    label: str = "",
) -> None:
    """Bootstrap simulation: resample days worth of rounds to project forward.

    Liquidity modes:
    - max_contracts: hard cap on contracts per trade
    - fill_capacities: sample from observed fill volumes to cap each trade
    """
    # We have ~2 days of data. Group rounds into "days" of ~96 rounds each
    rounds_per_day = len(round_signals) // 2 or len(round_signals)

    final_balances = []
    bust_count = 0

    for _ in range(num_sims):
        balance = starting_balance
        for day in range(num_days):
            # Sample a random day's worth of rounds
            start = random.randint(0, max(0, len(round_signals) - rounds_per_day))
            day_rounds = round_signals[start:start + rounds_per_day]

            for round_sigs in day_rounds:
                for sig in round_sigs[:3]:
                    price = sig["price"]
                    contracts = kelly_size(price, balance)
                    if contracts <= 0:
                        continue

                    # Apply liquidity cap
                    if fill_capacities:
                        # Sample a real fill capacity and use fraction of it
                        # (we can't take all the volume — assume we get 10-25%)
                        observed_vol = random.choice(fill_capacities)
                        liq_cap = max(1, int(observed_vol * 0.15))
                        contracts = min(contracts, liq_cap)
                    elif max_contracts is not None:
                        contracts = min(contracts, max_contracts)

                    fee = kalshi_fee(price, contracts)
                    if sig["won"]:
                        pnl = (Decimal("1") - price) * contracts - fee
                    else:
                        pnl = -price * contracts - fee
                    balance += pnl

                if balance < Decimal("1"):
                    bust_count += 1
                    break
            if balance < Decimal("1"):
                break

        final_balances.append(float(balance))

    final_balances.sort()
    mode_label = label or "UNCAPPED"
    print(f"\n{'=' * 70}")
    print(f"MONTE CARLO ({mode_label}): {num_sims} sims x {num_days} days, ${starting_balance}")
    print(f"(Resampling from {len(round_signals)} observed rounds)")
    print(f"{'=' * 70}")
    print(f"  Bust rate (<$1): {bust_count/num_sims*100:.1f}%")
    print(f"  Median final:    ${final_balances[len(final_balances)//2]:.2f}")
    print(f"  Mean final:      ${sum(final_balances)/len(final_balances):.2f}")
    print(f"  5th percentile:  ${final_balances[int(num_sims*0.05)]:.2f}")
    print(f"  25th percentile: ${final_balances[int(num_sims*0.25)]:.2f}")
    print(f"  75th percentile: ${final_balances[int(num_sims*0.75)]:.2f}")
    print(f"  95th percentile: ${final_balances[int(num_sims*0.95)]:.2f}")
    print(f"  Best case:       ${max(final_balances):.2f}")
    print(f"  Worst case:      ${min(final_balances):.2f}")


def main() -> None:
    dates = ["2026-03-08", "2026-03-09"]
    round_signals = load_all_signals(dates)

    total_sigs = sum(len(rs) for rs in round_signals)
    print(f"Loaded {len(round_signals)} rounds with {total_sigs} signals total")
    print(f"Window: T+{WINDOW_START}-{WINDOW_END}, dist >= {DIST_THRESHOLD}")
    print(f"Confidence: {CONFIDENCE}, Kelly fraction: {KELLY_FRACTION}")

    # Simulate at different starting balances
    print(f"\n{'=' * 70}")
    print("REPLAY: Run exact historical signals with Kelly sizing")
    print(f"{'=' * 70}")
    print(f"{'Start':>8s} {'End':>8s} {'P&L':>8s} {'Trades':>7s} {'WR%':>5s} {'Peak':>8s} {'Trough':>8s}")
    print("-" * 58)

    for start_bal in [32, 40, 50, 75, 100, 200, 500]:
        r = simulate(round_signals, Decimal(str(start_bal)))
        print(
            f"${start_bal:>6d} ${r['ending']:>7.2f} ${r['total_pnl']:>+7.2f} "
            f"{r['trades']:>7d} {r['win_rate']:>4.0f}% "
            f"${r['peak']:>7.2f} ${r['trough']:>7.2f}"
        )

    # Trades per day estimate
    rounds_with_trades = sum(1 for rs in round_signals for s in rs if kelly_size(s["price"], Decimal("40")) > 0)
    print(f"\n  Estimated trades/day at $40 balance: ~{rounds_with_trades / 2:.0f}")

    # Load actual fill capacities from round data
    fill_caps = load_fill_capacities(dates)
    fill_caps.sort()
    n_caps = len(fill_caps)
    if n_caps:
        print(f"\n  Fill capacity data: {n_caps} observations")
        print(f"  P10={fill_caps[int(n_caps*0.1)]}  P25={fill_caps[int(n_caps*0.25)]}"
              f"  P50={fill_caps[n_caps//2]}  P75={fill_caps[int(n_caps*0.75)]}"
              f"  P90={fill_caps[int(n_caps*0.9)]} contracts/60s")
        print(f"  Using 15% of observed volume as fillable (conservative)")

    # Monte Carlo: uncapped (original) for reference
    monte_carlo(round_signals, Decimal("32"), num_days=7, label="UNCAPPED")

    # Monte Carlo: liquidity-capped with real data
    for start_bal in [32, 50, 100, 200, 500, 1000]:
        for days in [7, 30]:
            monte_carlo(
                round_signals, Decimal(str(start_bal)),
                num_days=days,
                fill_capacities=fill_caps,
                label=f"LIQ-CAPPED 15% of observed vol",
            )

    # Hard-cap summary table
    print(f"\n{'=' * 70}")
    print("HARD CAP SCENARIOS: 30-day median final balance by max contracts")
    print(f"{'=' * 70}")
    print(f"{'MaxContracts':>13s} {'$32 start':>12s} {'$100 start':>12s} {'$500 start':>12s}")
    print("-" * 55)
    for cap in [1, 3, 5, 10, 25, 50]:
        row = []
        for start_bal in [32, 100, 500]:
            # Quick sim with fewer iterations for the table
            rounds_per_day = len(round_signals) // 2 or len(round_signals)
            finals = []
            for _ in range(500):
                balance = Decimal(str(start_bal))
                for day in range(30):
                    start_idx = random.randint(0, max(0, len(round_signals) - rounds_per_day))
                    day_rounds = round_signals[start_idx:start_idx + rounds_per_day]
                    for round_sigs in day_rounds:
                        for sig in round_sigs[:3]:
                            price = sig["price"]
                            contracts = min(kelly_size(price, balance), cap)
                            if contracts <= 0:
                                continue
                            fee = kalshi_fee(price, contracts)
                            if sig["won"]:
                                pnl = (Decimal("1") - price) * contracts - fee
                            else:
                                pnl = -price * contracts - fee
                            balance += pnl
                        if balance < Decimal("1"):
                            break
                    if balance < Decimal("1"):
                        break
                finals.append(float(balance))
            finals.sort()
            row.append(finals[len(finals) // 2])
        print(f"{cap:>13d} ${row[0]:>10.2f} ${row[1]:>10.2f} ${row[2]:>10.2f}")


if __name__ == "__main__":
    random.seed(42)
    main()
