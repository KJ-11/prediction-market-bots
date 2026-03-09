"""Sweep distance thresholds and time windows to find profitable parameters."""

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


def kalshi_fee(price: Decimal, contracts: int) -> Decimal:
    raw = FEE_COEFF * contracts * price * (Decimal("1") - price)
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


def find_signal(rd: RoundData, dist_thresh: Decimal, window_start: int, window_end: int) -> dict | None:
    for row in rd.rows:
        try:
            elapsed = float(row["seconds_elapsed"])
        except (ValueError, KeyError):
            continue
        if elapsed < window_start or elapsed > window_end:
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
        if dist < dist_thresh:
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

        return {"side": side, "price": price, "dist": dist, "elapsed": elapsed}
    return None


def run_backtest(
    dates: list[str],
    dist_thresh: Decimal,
    window_start: int,
    window_end: int,
    max_price: Decimal | None = None,
    coins: list[str] | None = None,
) -> dict:
    series_filter = SERIES_LIST
    if coins:
        series_filter = [s for s, c in SERIES_TO_COIN.items() if c in coins]

    all_rounds: list[RoundData] = []
    for date in dates:
        for series in series_filter:
            filepath = DATA_DIR / f"{series}-{date}.csv"
            if filepath.exists():
                all_rounds.extend(load_rounds(filepath, series))

    wins = 0
    losses = 0
    total_pnl = Decimal("0")
    prices = []
    dists = []

    for rd in all_rounds:
        if rd.outcome is None:
            continue
        sig = find_signal(rd, dist_thresh, window_start, window_end)
        if sig is None:
            continue
        if max_price and sig["price"] > max_price:
            continue

        won = sig["side"].lower() == rd.outcome
        price = sig["price"]
        fee = kalshi_fee(price, 1)

        if won:
            pnl = Decimal("1") - price - fee
            wins += 1
        else:
            pnl = -price - fee
            losses += 1

        total_pnl += pnl
        prices.append(float(price))
        dists.append(float(sig["dist"]))

    total = wins + losses
    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / total * 100 if total else 0,
        "pnl_per_trade": float(total_pnl / total) if total else 0,
        "total_pnl": float(total_pnl),
        "avg_price": sum(prices) / len(prices) if prices else 0,
        "avg_dist": sum(dists) / len(dists) if dists else 0,
    }


def main() -> None:
    dates = ["2026-03-08", "2026-03-09"]

    # Sweep 1: Distance threshold
    print("=" * 80)
    print("SWEEP 1: DISTANCE THRESHOLD (window T+600-800, all coins, no price cap)")
    print("=" * 80)
    print(f"{'Dist%':>6s} {'Trades':>7s} {'Wins':>5s} {'WR%':>6s} {'AvgPrice':>9s} {'PnL/Trade':>10s} {'TotalPnL':>9s} {'Profitable':>11s}")
    print("-" * 75)

    for d_pct in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.80, 1.00]:
        r = run_backtest(dates, Decimal(str(d_pct / 100)), 600, 800)
        flag = "  YES" if r["pnl_per_trade"] > 0 else ""
        print(
            f"{d_pct:>5.2f}% {r['trades']:>7d} {r['wins']:>5d} "
            f"{r['win_rate']:>5.1f}% ${r['avg_price']:>7.3f}  "
            f"${r['pnl_per_trade']:>+8.4f}  ${r['total_pnl']:>+7.2f}{flag}"
        )

    # Sweep 2: Time window
    print(f"\n{'=' * 80}")
    print("SWEEP 2: TIME WINDOW (dist>=0.2%, all coins, no price cap)")
    print("=" * 80)
    print(f"{'Window':>12s} {'Trades':>7s} {'Wins':>5s} {'WR%':>6s} {'AvgPrice':>9s} {'PnL/Trade':>10s} {'TotalPnL':>9s}")
    print("-" * 65)

    windows = [
        (300, 500), (400, 600), (500, 700), (600, 800), (700, 900),
        (600, 900), (500, 800), (400, 800),
    ]
    for ws, we in windows:
        r = run_backtest(dates, Decimal("0.002"), ws, we)
        print(
            f"  T+{ws}-{we} {r['trades']:>7d} {r['wins']:>5d} "
            f"{r['win_rate']:>5.1f}% ${r['avg_price']:>7.3f}  "
            f"${r['pnl_per_trade']:>+8.4f}  ${r['total_pnl']:>+7.2f}"
        )

    # Sweep 3: Max entry price
    print(f"\n{'=' * 80}")
    print("SWEEP 3: MAX ENTRY PRICE (dist>=0.2%, T+600-800, all coins)")
    print("=" * 80)
    print(f"{'MaxPrice':>9s} {'Trades':>7s} {'Wins':>5s} {'WR%':>6s} {'AvgPrice':>9s} {'PnL/Trade':>10s} {'TotalPnL':>9s}")
    print("-" * 65)

    for mp in [0.99, 0.95, 0.93, 0.92, 0.90, 0.88, 0.85, 0.80, 0.75, 0.70]:
        r = run_backtest(dates, Decimal("0.002"), 600, 800, max_price=Decimal(str(mp)))
        print(
            f"   ${mp:.2f} {r['trades']:>7d} {r['wins']:>5d} "
            f"{r['win_rate']:>5.1f}% ${r['avg_price']:>7.3f}  "
            f"${r['pnl_per_trade']:>+8.4f}  ${r['total_pnl']:>+7.2f}"
        )

    # Sweep 4: Per coin
    print(f"\n{'=' * 80}")
    print("SWEEP 4: PER COIN (dist>=0.2%, T+600-800, no price cap)")
    print("=" * 80)

    for coin in ["BTC", "ETH", "SOL"]:
        print(f"\n  --- {coin} ---")
        print(f"  {'Dist%':>6s} {'Trades':>7s} {'WR%':>6s} {'AvgPrice':>9s} {'PnL/Trade':>10s}")
        for d_pct in [0.20, 0.30, 0.40, 0.50]:
            r = run_backtest(dates, Decimal(str(d_pct / 100)), 600, 800, coins=[coin])
            print(
                f"  {d_pct:>5.2f}% {r['trades']:>7d} "
                f"{r['win_rate']:>5.1f}% ${r['avg_price']:>7.3f}  "
                f"${r['pnl_per_trade']:>+8.4f}"
            )

    # Sweep 5: Combined — distance + price cap
    print(f"\n{'=' * 80}")
    print("SWEEP 5: DISTANCE × PRICE CAP (T+600-800, all coins)")
    print("=" * 80)
    print(f"{'Dist%':>6s} {'MaxP':>6s} {'Trades':>7s} {'WR%':>6s} {'AvgP':>6s} {'PnL/T':>8s} {'TotPnL':>8s}")
    print("-" * 55)

    for d_pct in [0.20, 0.30, 0.40, 0.50]:
        for mp in [0.92, 0.90, 0.88, 0.85]:
            r = run_backtest(
                dates, Decimal(str(d_pct / 100)), 600, 800,
                max_price=Decimal(str(mp)),
            )
            if r["trades"] == 0:
                continue
            flag = " ←" if r["pnl_per_trade"] > 0.01 else ""
            print(
                f"{d_pct:>5.2f}% ${mp:.2f} {r['trades']:>7d} "
                f"{r['win_rate']:>5.1f}% ${r['avg_price']:>.3f} "
                f"${r['pnl_per_trade']:>+7.4f} ${r['total_pnl']:>+7.2f}{flag}"
            )

    # Sweep 6: Earlier windows where price might be lower
    print(f"\n{'=' * 80}")
    print("SWEEP 6: EARLIER WINDOWS (dist>=0.3%, all coins)")
    print("  Can we catch signals before the market reprices?")
    print("=" * 80)
    print(f"{'Window':>12s} {'Trades':>7s} {'WR%':>6s} {'AvgPrice':>9s} {'PnL/Trade':>10s} {'TotalPnL':>9s}")
    print("-" * 60)

    for ws, we in [(200, 400), (300, 500), (400, 600), (500, 700), (600, 800)]:
        r = run_backtest(dates, Decimal("0.003"), ws, we)
        print(
            f"  T+{ws}-{we} {r['trades']:>7d} "
            f"{r['win_rate']:>5.1f}% ${r['avg_price']:>7.3f}  "
            f"${r['pnl_per_trade']:>+8.4f}  ${r['total_pnl']:>+7.2f}"
        )


if __name__ == "__main__":
    main()
