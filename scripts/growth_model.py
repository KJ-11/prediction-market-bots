"""12-month growth model for whale-following strategy.

Monte Carlo simulation using validated parameters:
- 95% win rate (35-day backtest)
- Risk-phased sizing from sizing.py
- Kalshi fee model
- Dynamic circuit breaker
- 2 concurrent positions max

Usage:
    python scripts/growth_model.py [--start-balance 77.62] [--sims 10000]
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from decimal import Decimal

# --- Parameters from backtest + today's live data ---

WIN_RATE = 0.95
AVG_ENTRY_PRICE = 0.91  # From today's 16 trades
TRADES_PER_DAY = 12     # Conservative (today was 16 in 10h, but not every day is busy)
FEE_COEFFICIENT = 0.07  # Kalshi taker fee coefficient
MAX_CONCURRENT = 2
DAYS = 365

# Sizing phases from sizing.py
PHASES = [
    (50_000, 0.10),
    (5_000,  0.20),
    (1_000,  0.30),
    (500,    0.50),
    (0,      1.00),
]

# Dynamic drawdown tiers (from updated risk.py)
DRAWDOWN_TIERS = [
    (5_000, 0.40),
    (1_000, 0.50),
    (500,   0.60),
    (0,     0.70),
]

# Liquidity ceiling — at some point orders can't fill at these sizes
# Assume max ~$5K notional per trade in sports markets
MAX_TRADE_NOTIONAL = 5_000


def kalshi_fee(price: float, contracts: int) -> float:
    return FEE_COEFFICIENT * contracts * price * (1 - price)


def get_alloc_pct(balance: float) -> float:
    for threshold, pct in PHASES:
        if balance >= threshold:
            return pct
    return 1.0


def get_drawdown_limit(balance: float) -> float:
    for threshold, pct in DRAWDOWN_TIERS:
        if balance >= threshold:
            return pct
    return 0.70


@dataclass
class SimResult:
    final_balance: float
    total_trades: int
    total_wins: int
    total_losses: int
    max_drawdown_pct: float
    circuit_breaker_pauses: int
    peak_balance: float
    hit_ceiling: bool  # True if liquidity constrained


def simulate_one(start_balance: float, seed: int | None = None) -> SimResult:
    rng = random.Random(seed)
    balance = start_balance
    ath = start_balance
    total_trades = 0
    total_wins = 0
    total_losses = 0
    max_dd = 0.0
    cb_pauses = 0
    peak = start_balance
    hit_ceiling = False
    consecutive_losses = 0
    paused_days = 0

    for day in range(DAYS):
        if paused_days > 0:
            paused_days -= 1
            continue

        day_start = balance

        for trade_idx in range(TRADES_PER_DAY):
            if balance <= 1.0:
                break

            # Sizing: split balance across available slots
            # Simplified: assume avg 1 slot open, so use balance / 1.5
            alloc_pct = get_alloc_pct(balance)
            slot_balance = balance * alloc_pct / 1.5  # Avg between 1 and 2 open

            # Contracts at avg entry price + fee
            price = AVG_ENTRY_PRICE
            fee_per = kalshi_fee(price, 1)
            cost_per = price + fee_per
            contracts = int(slot_balance / cost_per)
            if contracts <= 0:
                break

            # Liquidity ceiling
            notional = contracts * price
            if notional > MAX_TRADE_NOTIONAL:
                contracts = int(MAX_TRADE_NOTIONAL / price)
                hit_ceiling = True

            total_cost = contracts * price + kalshi_fee(price, contracts)
            if total_cost > balance:
                contracts -= 1
                if contracts <= 0:
                    break
                total_cost = contracts * price + kalshi_fee(price, contracts)

            # Execute trade
            won = rng.random() < WIN_RATE
            total_trades += 1

            if won:
                # Payout: $1 per contract, minus entry cost, minus exit fee
                # Kalshi charges fee on both entry and exit (settlement)
                exit_fee = kalshi_fee(1.0 - price, contracts)  # Fee on profit side
                pnl = contracts * (1 - price) - kalshi_fee(price, contracts) - exit_fee
                balance += pnl
                total_wins += 1
                consecutive_losses = 0
            else:
                # Lose entire cost
                balance -= total_cost
                total_losses += 1
                consecutive_losses += 1

            # Track peak/drawdown
            if balance > peak:
                peak = balance
            if balance > ath:
                ath = balance
            if ath > 0:
                dd = (ath - balance) / ath * 100
                if dd > max_dd:
                    max_dd = dd

                # Circuit breaker check
                limit = get_drawdown_limit(balance)
                if dd >= limit:
                    cb_pauses += 1
                    paused_days = 1  # 24h pause
                    ath = balance  # Reset ATH after pause
                    break

            # Consecutive loss skip
            if consecutive_losses >= 3:
                consecutive_losses = 0
                break  # Skip rest of day

    return SimResult(
        final_balance=balance,
        total_trades=total_trades,
        total_wins=total_wins,
        total_losses=total_losses,
        max_drawdown_pct=max_dd,
        circuit_breaker_pauses=cb_pauses,
        peak_balance=peak,
        hit_ceiling=hit_ceiling,
    )


def main():
    parser = argparse.ArgumentParser(description="Whale bot 12-month growth model")
    parser.add_argument("--start-balance", type=float, default=77.62)
    parser.add_argument("--sims", type=int, default=10_000)
    args = parser.parse_args()

    print(f"=== 12-Month Growth Model ===")
    print(f"Start balance: ${args.start_balance:.2f}")
    print(f"Win rate: {WIN_RATE:.0%} | Avg entry: ${AVG_ENTRY_PRICE}")
    print(f"Trades/day: {TRADES_PER_DAY} | Max concurrent: {MAX_CONCURRENT}")
    print(f"Liquidity ceiling: ${MAX_TRADE_NOTIONAL}/trade")
    print(f"Simulations: {args.sims:,}")
    print()

    results = [simulate_one(args.start_balance, seed=i) for i in range(args.sims)]

    finals = sorted(r.final_balance for r in results)
    peaks = sorted(r.peak_balance for r in results)
    trades = [r.total_trades for r in results]
    wins = [r.total_wins for r in results]
    losses = [r.total_losses for r in results]
    drawdowns = sorted(r.max_drawdown_pct for r in results)
    pauses = [r.circuit_breaker_pauses for r in results]
    busted = sum(1 for r in results if r.final_balance < 10)
    ceiling_hit = sum(1 for r in results if r.hit_ceiling)

    def pct(arr, p):
        idx = int(len(arr) * p / 100)
        return arr[min(idx, len(arr) - 1)]

    print("=== Final Balance Distribution ===")
    print(f"  1st percentile (worst):   ${pct(finals, 1):>12,.2f}")
    print(f"  5th percentile:           ${pct(finals, 5):>12,.2f}")
    print(f"  10th percentile:          ${pct(finals, 10):>12,.2f}")
    print(f"  25th percentile:          ${pct(finals, 25):>12,.2f}")
    print(f"  MEDIAN (50th):            ${pct(finals, 50):>12,.2f}")
    print(f"  75th percentile:          ${pct(finals, 75):>12,.2f}")
    print(f"  90th percentile:          ${pct(finals, 90):>12,.2f}")
    print(f"  95th percentile:          ${pct(finals, 95):>12,.2f}")
    print(f"  99th percentile (best):   ${pct(finals, 99):>12,.2f}")
    print()

    median_final = pct(finals, 50)
    print(f"=== Key Metrics ===")
    print(f"  Median return:            {(median_final / args.start_balance - 1) * 100:>10,.0f}%")
    print(f"  Median final balance:     ${median_final:>12,.2f}")
    print(f"  Bust rate (<$10):         {busted / args.sims * 100:>10.1f}%")
    print(f"  Liquidity-constrained:    {ceiling_hit / args.sims * 100:>10.1f}%")
    print(f"  Avg trades/sim:           {sum(trades) / args.sims:>10,.0f}")
    print(f"  Avg wins:                 {sum(wins) / args.sims:>10,.0f}")
    print(f"  Avg losses:               {sum(losses) / args.sims:>10,.0f}")
    print(f"  Actual WR:                {sum(wins) / max(sum(trades), 1) * 100:>10.1f}%")
    print(f"  Avg CB pauses:            {sum(pauses) / args.sims:>10.1f}")
    print(f"  Median max drawdown:      {pct(drawdowns, 50):>10.1f}%")
    print(f"  95th pct drawdown:        {pct(drawdowns, 95):>10.1f}%")
    print()

    # ---- Week-by-week percentile chart across ALL sims ----
    # Re-run all sims recording weekly snapshots
    weeks = 52
    # Store weekly balances: [sim_idx][week] = balance
    weekly_data: list[list[float]] = []
    for seed in range(args.sims):
        rng = random.Random(seed)
        balance = args.start_balance
        ath = balance
        paused_d = 0
        consecutive_losses = 0
        snapshots = [balance]  # week 0

        day_counter = 0
        for week in range(1, weeks + 1):
            for _ in range(7):
                day_counter += 1
                if paused_d > 0:
                    paused_d -= 1
                    continue
                for _ in range(TRADES_PER_DAY):
                    if balance <= 1:
                        break
                    alloc_pct = get_alloc_pct(balance)
                    slot_bal = balance * alloc_pct / 1.5
                    price = AVG_ENTRY_PRICE
                    cost_per = price + kalshi_fee(price, 1)
                    contracts = int(slot_bal / cost_per)
                    if contracts <= 0:
                        break
                    notional = contracts * price
                    if notional > MAX_TRADE_NOTIONAL:
                        contracts = int(MAX_TRADE_NOTIONAL / price)
                    total_cost = contracts * price + kalshi_fee(price, contracts)
                    if total_cost > balance:
                        contracts -= 1
                        if contracts <= 0:
                            break
                        total_cost = contracts * price + kalshi_fee(price, contracts)

                    if rng.random() < WIN_RATE:
                        exit_fee = kalshi_fee(1 - price, contracts)
                        pnl = contracts * (1 - price) - kalshi_fee(price, contracts) - exit_fee
                        balance += pnl
                        consecutive_losses = 0
                    else:
                        balance -= total_cost
                        consecutive_losses += 1

                    if balance > ath:
                        ath = balance
                    if ath > 0:
                        dd = (ath - balance) / ath * 100
                        limit = get_drawdown_limit(balance)
                        if dd >= limit:
                            paused_d = 1
                            ath = balance
                            break
                    if consecutive_losses >= 3:
                        consecutive_losses = 0
                        break

            snapshots.append(balance)
        weekly_data.append(snapshots)

    def week_pct(week: int, p: int) -> float:
        vals = sorted(wd[week] for wd in weekly_data)
        idx = int(len(vals) * p / 100)
        return vals[min(idx, len(vals) - 1)]

    # Print week-by-week chart
    print("=== Week-by-Week Growth (percentile bands) ===")
    print()
    print(f"{'Week':>4} {'p5':>10} {'p25':>10} {'MEDIAN':>10} {'p75':>10} {'p95':>10}  {'Alive':>6}  Chart (log scale)")
    print(f"{'─' * 4} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 10}  {'─' * 6}  {'─' * 40}")

    for w in range(0, weeks + 1):
        p5 = week_pct(w, 5)
        p25 = week_pct(w, 25)
        p50 = week_pct(w, 50)
        p75 = week_pct(w, 75)
        p95 = week_pct(w, 95)
        alive = sum(1 for wd in weekly_data if wd[w] >= 10) / args.sims * 100

        # Log-scale bar chart for median
        import math
        if p50 > 1:
            bar_len = max(0, int(math.log10(p50) * 10))
        else:
            bar_len = 0
        # Show p25-p75 range as a band
        if p25 > 1:
            low_len = max(0, int(math.log10(p25) * 10))
        else:
            low_len = 0
        if p75 > 1:
            hi_len = max(0, int(math.log10(p75) * 10))
        else:
            hi_len = 0

        bar = "░" * low_len + "█" * max(0, bar_len - low_len) + "░" * max(0, hi_len - bar_len)

        # Only print every week for first month, then every 2 weeks
        if w <= 4 or w % 2 == 0 or w == weeks:
            print(
                f"{w:>4} "
                f"${p5:>9,.0f} "
                f"${p25:>9,.0f} "
                f"${p50:>9,.0f} "
                f"${p75:>9,.0f} "
                f"${p95:>9,.0f}  "
                f"{alive:>5.0f}%  "
                f"{bar}"
            )

    print()
    print(f"  Start: ${args.start_balance:.2f}")
    print(f"  Sims that survive Phase 1 (>$500): {sum(1 for wd in weekly_data if wd[-1] >= 500) / args.sims * 100:.1f}%")
    print(f"  Sims that bust (<$10):              {sum(1 for wd in weekly_data if wd[-1] < 10) / args.sims * 100:.1f}%")
    print(f"  Median @ 1 month: ${week_pct(4, 50):,.0f}")
    print(f"  Median @ 3 months: ${week_pct(13, 50):,.0f}")
    print(f"  Median @ 6 months: ${week_pct(26, 50):,.0f}")
    print(f"  Median @ 12 months: ${week_pct(52, 50):,.0f}")

    print()
    print("⚠️  CAVEATS:")
    print("  - Win rate assumed constant at 95% (may degrade with scale)")
    print("  - Liquidity capped at $5K/trade (real limits unknown)")
    print("  - No account for market regime changes or reduced sports schedules")
    print("  - Fees modeled as 7% coefficient (Kalshi's current rate)")
    print("  - Circuit breaker pauses cost opportunity, not capital")


if __name__ == "__main__":
    main()
