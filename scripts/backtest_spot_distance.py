"""Backtest SpotDistanceStrategy against recorded round data.

Simulates realistic trading: all coins trade simultaneously from the same
balance within each round, using Kalshi's official fee formula with proper
rounding.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

WINDOW_START = 600
WINDOW_END = 800
DIST_THRESHOLD = Decimal("0.002")

# Kalshi taker fee: round_up(0.07 * C * P * (1 - P))
FEE_COEFF = Decimal("0.07")
ONE_CENT = Decimal("0.01")

INITIAL_BALANCE = Decimal("50")
BANKROLL_RISK_PCT = 0.12  # Phase 1 sizing: 12% of bankroll per trade

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "rounds"
SERIES_LIST = ["KXBTC15M", "KXETH15M", "KXSOL15M"]
SERIES_TO_COIN = {"KXBTC15M": "BTC", "KXETH15M": "ETH", "KXSOL15M": "SOL"}


def kalshi_fee(price: Decimal, contracts: int) -> Decimal:
    """Kalshi taker fee: round_up(0.07 * C * P * (1 - P))."""
    raw = FEE_COEFF * contracts * price * (Decimal("1") - price)
    return raw.quantize(ONE_CENT, rounding="ROUND_CEILING")


def size_contracts(price: Decimal, balance: Decimal) -> int:
    """Phase 1 sizing: 12% of bankroll / price."""
    if price <= 0:
        return 0
    risk = float(balance) * BANKROLL_RISK_PCT
    contracts = max(1, int(risk / float(price)))
    # Verify we can afford it including fees
    fee = kalshi_fee(price, contracts)
    total_cost = price * contracts + fee
    while total_cost > balance and contracts > 0:
        contracts -= 1
        fee = kalshi_fee(price, contracts)
        total_cost = price * contracts + fee
    return contracts


@dataclass
class Trade:
    ticker: str
    coin: str
    side: str  # YES or NO
    entry_price: Decimal
    contracts: int
    outcome: str  # yes or no
    dist: Decimal
    elapsed: float

    @property
    def won(self) -> bool:
        return self.side.lower() == self.outcome

    @property
    def fee(self) -> Decimal:
        return kalshi_fee(self.entry_price, self.contracts)

    @property
    def pnl(self) -> Decimal:
        if self.won:
            return (Decimal("1") - self.entry_price) * self.contracts - self.fee
        else:
            return -self.entry_price * self.contracts - self.fee

    @property
    def cost(self) -> Decimal:
        return self.entry_price * self.contracts + self.fee


@dataclass
class RoundData:
    """All snapshot rows for one round of one coin."""
    ticker: str
    series: str
    rows: list[dict] = field(default_factory=list)
    outcome: str | None = None


def load_rounds(filepath: Path, series: str) -> list[RoundData]:
    """Load all rounds from a CSV file."""
    rounds_by_ticker: dict[str, RoundData] = {}
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row["round_ticker"]
            if ticker not in rounds_by_ticker:
                rounds_by_ticker[ticker] = RoundData(
                    ticker=ticker, series=series,
                )
            rounds_by_ticker[ticker].rows.append(row)
            if row.get("row_type") == "round_end" and row.get("outcome"):
                rounds_by_ticker[ticker].outcome = row["outcome"]
    return list(rounds_by_ticker.values())


def find_signal(rd: RoundData) -> dict | None:
    """Find the first tradeable signal in a round's data.

    Returns dict with: side, price, dist, elapsed. Or None.
    """
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

        return {
            "side": side, "price": price,
            "dist": dist, "elapsed": elapsed,
        }
    return None


def main() -> None:
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-03-06"

    # Load all rounds for all coins
    all_rounds: dict[str, list[RoundData]] = {}
    for series in SERIES_LIST:
        filepath = DATA_DIR / f"{series}-{date}.csv"
        if not filepath.exists():
            print(f"  {series}: no data file")
            continue
        all_rounds[series] = load_rounds(filepath, series)

    if not all_rounds:
        print("No data found.")
        return

    # Group rounds by their time slot (extract the timestamp portion)
    # e.g. KXBTC15M-26MAR060015-15 -> "26MAR060015-15"
    def round_slot(ticker: str) -> str:
        # Strip the series prefix to get the time slot
        for s in SERIES_LIST:
            if ticker.startswith(s):
                return ticker[len(s) + 1:]  # skip the "-"
        return ticker

    # Build time_slot -> {series: RoundData}
    slots: dict[str, dict[str, RoundData]] = defaultdict(dict)
    for series, rounds in all_rounds.items():
        for rd in rounds:
            slot = round_slot(rd.ticker)
            slots[slot][series] = rd

    # Simulate chronologically
    balance = INITIAL_BALANCE
    all_trades: list[Trade] = []
    coin_trades: dict[str, list[Trade]] = defaultdict(list)

    sorted_slots = sorted(slots.keys())

    for slot in sorted_slots:
        coin_rounds = slots[slot]

        # Find signals for all coins in this round
        signals: list[tuple[str, RoundData, dict]] = []
        for series, rd in coin_rounds.items():
            if rd.outcome is None:
                continue
            sig = find_signal(rd)
            if sig:
                signals.append((series, rd, sig))

        # Sort by elapsed time (earliest signal first)
        signals.sort(key=lambda x: x[2]["elapsed"])

        # Execute up to 3 signals from shared balance
        for series, rd, sig in signals[:3]:
            coin = SERIES_TO_COIN[series]
            contracts = size_contracts(sig["price"], balance)
            if contracts <= 0:
                continue

            trade = Trade(
                ticker=rd.ticker,
                coin=coin,
                side=sig["side"],
                entry_price=sig["price"],
                contracts=contracts,
                outcome=rd.outcome,
                dist=sig["dist"],
                elapsed=sig["elapsed"],
            )

            # Deduct cost immediately (balance drops while in position)
            balance -= trade.cost
            all_trades.append(trade)
            coin_trades[series].append(trade)

        # Settle all trades at round end (winners get $1/contract back)
        for series, rd, sig in signals[:3]:
            matching = [
                t for t in all_trades
                if t.ticker == rd.ticker and t.coin == SERIES_TO_COIN[series]
            ]
            for trade in matching:
                if trade.won:
                    balance += Decimal("1") * trade.contracts

    print(f"Backtest: SpotDistanceStrategy | {date}")
    print(f"Starting balance: ${INITIAL_BALANCE}")
    print(f"Sizing: Phase 1 ({BANKROLL_RISK_PCT*100:.0f}% of bankroll)")
    print("Fees: Kalshi taker — round_up(0.07 * C * P * (1-P))")
    print("Simultaneous: all coins trade from shared balance per round")
    print()

    for series in SERIES_LIST:
        trades = coin_trades.get(series, [])
        if not trades:
            print(f"  {series}: no trades")
            continue
        coin = SERIES_TO_COIN[series]
        wins = sum(1 for t in trades if t.won)
        total_pnl = sum(t.pnl for t in trades)
        total_risked = sum(t.cost for t in trades)
        total_fees = sum(t.fee for t in trades)
        win_rate = wins / len(trades) * 100
        avg_contracts = sum(t.contracts for t in trades) / len(trades)
        print(
            f"  {series}: {len(trades)} trades, "
            f"{wins} wins ({win_rate:.1f}%), "
            f"P&L: ${total_pnl:+.2f}, "
            f"Fees: ${total_fees:.2f}, "
            f"Risked: ${total_risked:.2f}, "
            f"Avg size: {avg_contracts:.1f} contracts"
        )

    print()
    if all_trades:
        total_wins = sum(1 for t in all_trades if t.won)
        total_pnl = sum(t.pnl for t in all_trades)
        total_risked = sum(t.cost for t in all_trades)
        total_fees = sum(t.fee for t in all_trades)
        total_wr = total_wins / len(all_trades) * 100
        avg_pnl = total_pnl / len(all_trades)
        print(
            f"  TOTAL: {len(all_trades)} trades, "
            f"{total_wins} wins ({total_wr:.1f}%)"
        )
        print(
            f"  P&L: ${total_pnl:+.2f} | "
            f"Fees: ${total_fees:.2f} | "
            f"Risked: ${total_risked:.2f} | "
            f"Avg: ${avg_pnl:+.4f}/trade"
        )
        print(f"  Final balance: ${balance:.2f} (started ${INITIAL_BALANCE})")

        losers = [t for t in all_trades if not t.won]
        if losers:
            print(f"\n  Losing trades ({len(losers)}):")
            for t in losers:
                print(
                    f"    {t.ticker} — {t.side} @ ${t.entry_price} "
                    f"x{t.contracts} (dist={float(t.dist):.4f}, T+{t.elapsed:.0f}s) "
                    f"outcome={t.outcome} pnl=${t.pnl:+.2f}"
                )
    else:
        print("  No trades found.")


if __name__ == "__main__":
    main()
