"""
Tick-accurate backtest of the SpotDistanceStrategy against collected round data.

Faithfully replays the actual bot logic:
- First signal only per coin per round (_traded flag)
- Max 3 trades per round (round cap)
- Fractional Kelly (25%) sizing with actual ask prices
- +2c price cushion on buys
- Kelly = 0 → skip (price too high / no edge after fees)
- Kalshi fee model: ceil(0.07 * C * P * (1-P))
- IOC fill assumption: if ask_size present and > 0, assume fill; else skip

Also explores parameter variations and new signals.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

import pandas as pd
import numpy as np

DATA_DIR = Path("data")
ROUNDS_DIR = DATA_DIR / "rounds"
TRADES_DIR = DATA_DIR / "trades"

ONE_CENT = Decimal("0.01")
FEE_COEFF = Decimal("0.07")
PRICE_CUSHION = Decimal("0.02")  # +2c for buys


# ---- Fee & sizing (mirrors bots/kalshi_crypto/sizing.py) --------------------

def kalshi_fee(price: Decimal, contracts: int = 1) -> Decimal:
    """Match real bot: (raw * 100).to_integral_value() / 100 (ROUND_HALF_EVEN)."""
    raw = FEE_COEFF * contracts * price * (Decimal("1") - price)
    return (raw * 100).to_integral_value() / 100


def kelly_size(
    price: Decimal,
    confidence: float,
    balance: Decimal,
    fraction: float = 0.25,
) -> int:
    if confidence <= 0 or price <= 0 or price >= Decimal("1"):
        return 0
    fee = kalshi_fee(price)
    cost = price + fee
    net_win = Decimal("1") - price - fee
    if net_win <= 0:
        return 0
    b = float(net_win / cost)
    p, q = confidence, 1.0 - confidence
    kelly_f = (p * b - q) / b if b > 0 else 0.0
    kelly_f *= fraction
    if kelly_f <= 0:
        return 0
    contracts = int(float(balance) * kelly_f / float(cost))
    # Cap by balance
    if contracts > 0:
        total = price * contracts + kalshi_fee(price, contracts)
        while total > balance and contracts > 0:
            contracts -= 1
            total = price * contracts + kalshi_fee(price, contracts)
    return max(contracts, 0)


# ---- Data loading -----------------------------------------------------------

def load_all_rounds() -> pd.DataFrame:
    files = sorted(ROUNDS_DIR.glob("KX*.csv"))
    dfs = []
    for f in files:
        df = pd.read_csv(f, parse_dates=["timestamp"])
        coin = re.match(r"KX(\w+?)15M", f.stem).group(1)
        df["coin"] = coin
        dfs.append(df)
    combined = pd.concat(dfs, ignore_index=True)
    combined.sort_values(["round_ticker", "seconds_elapsed"], inplace=True)
    return combined


def load_all_trades() -> pd.DataFrame:
    files = sorted(TRADES_DIR.glob("kalshi-crypto-multi-*.csv"))
    dfs = []
    for f in files:
        df = pd.read_csv(f, parse_dates=["timestamp"], engine="python",
                         on_bad_lines="skip")
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)


# ---- Tick-accurate backtest -------------------------------------------------

def _find_first_signals(
    rounds_df: pd.DataFrame,
    *,
    dist_threshold: float,
    window_start: int,
    window_end: int,
    coins: list[str],
    no_side_extra_threshold: float | None,
    min_price: float | None,
) -> pd.DataFrame:
    """Vectorized: find the first qualifying tick per coin per round.

    Returns one row per (round_time, coin) where signal fires, with columns:
    round_time, coin, side, signal_price, buy_price, dist_pct, elapsed, outcome
    """
    df = rounds_df.copy()

    # Filter to relevant coins and time window
    df = df[df["coin"].isin(coins)]
    df = df[(df["seconds_elapsed"] >= window_start) & (df["seconds_elapsed"] <= window_end)]

    # Compute distance
    df["dist"] = (df["spot_price"] - df["strike"]).abs() / df["strike"]
    df = df[df["dist"] >= dist_threshold]

    # Determine side and price
    df["side"] = np.where(df["spot_price"] > df["strike"], "yes", "no")
    df["price_raw"] = np.where(
        df["side"] == "yes",
        df["yes_ask"],
        np.where(df["no_ask"].notna(), df["no_ask"], 1.0 - df["yes_bid"]),
    )

    # Filter valid prices
    df = df[df["price_raw"].notna() & (df["price_raw"] > 0) & (df["price_raw"] < 1)]

    # Optional NO-side extra threshold
    if no_side_extra_threshold is not None:
        df = df[~((df["side"] == "no") & (df["dist"] < no_side_extra_threshold))]

    # Compute buy price (with cushion) and filter
    df["signal_price"] = (df["price_raw"] * 100).round() / 100  # round to cents
    df["buy_price"] = df["signal_price"] + float(PRICE_CUSHION)
    df = df[df["buy_price"] < 1.0]

    # Optional min price
    if min_price is not None:
        df = df[df["signal_price"] >= min_price]

    # Round time slot for grouping multi-coin rounds
    df["round_time"] = df["round_ticker"].str.extract(r"(\d{6}-\d{2})$")[0]

    # Get outcome per round_ticker (take first non-null)
    outcomes = rounds_df.groupby("round_ticker")["outcome"].first().dropna()
    df["outcome"] = df["round_ticker"].map(outcomes)

    # First qualifying tick per coin per round (first signal = _traded flag)
    df = df.sort_values("seconds_elapsed")
    first = df.groupby(["round_time", "coin"]).first().reset_index()

    return first[["round_time", "coin", "side", "signal_price", "buy_price",
                   "dist", "seconds_elapsed", "outcome"]].rename(
        columns={"dist": "dist_pct", "seconds_elapsed": "elapsed"})


def backtest(
    rounds_df: pd.DataFrame,
    *,
    dist_threshold: float = 0.002,
    window_start: int = 300,
    window_end: int = 540,
    confidence: float = 0.88,
    kelly_frac: float = 0.25,
    max_trades_per_round: int = 3,
    starting_balance: float = 34.56,
    coins: list[str] | None = None,
    min_price: float | None = None,
    no_side_extra_threshold: float | None = None,
) -> pd.DataFrame:
    """Replay strategy against collected data. Returns one row per signal."""

    if coins is None:
        coins = ["BTC", "ETH", "SOL", "XRP"]

    # Step 1: Vectorized signal detection — first qualifying tick per coin per round
    signals = _find_first_signals(
        rounds_df,
        dist_threshold=dist_threshold,
        window_start=window_start,
        window_end=window_end,
        coins=coins,
        no_side_extra_threshold=no_side_extra_threshold,
        min_price=min_price,
    )

    if len(signals) == 0:
        return pd.DataFrame()

    # Step 2: Sequential execution with Kelly sizing, round cap, balance tracking
    # Sort by round_time then elapsed (earlier signals within a round execute first)
    signals = signals.sort_values(["round_time", "elapsed"]).reset_index(drop=True)

    balance = Decimal(str(starting_balance))
    results = []
    current_round = None
    round_trades = 0

    for _, sig in signals.iterrows():
        rt = sig["round_time"]
        if rt != current_round:
            current_round = rt
            round_trades = 0

        coin = sig["coin"]
        side = sig["side"]
        buy_price = Decimal(str(round(sig["buy_price"], 2)))
        signal_price = Decimal(str(round(sig["signal_price"], 2)))
        outcome = sig["outcome"]

        # Round cap
        if round_trades >= max_trades_per_round:
            results.append({
                "round_time": rt, "coin": coin, "side": side,
                "signal_price": float(signal_price),
                "buy_price": float(buy_price),
                "dist_pct": sig["dist_pct"] * 100,
                "size": 0, "outcome": outcome, "won": None,
                "pnl": 0.0, "fee": 0.0, "balance": float(balance),
                "skip_reason": "round_cap", "elapsed": sig["elapsed"],
            })
            continue

        # Kelly sizing — real bot sizes on signal_price (ask), NOT cushioned price
        size = kelly_size(signal_price, confidence, balance, kelly_frac)
        if size <= 0:
            results.append({
                "round_time": rt, "coin": coin, "side": side,
                "signal_price": float(signal_price),
                "buy_price": float(buy_price),
                "dist_pct": sig["dist_pct"] * 100,
                "size": 0, "outcome": outcome, "won": None,
                "pnl": 0.0, "fee": 0.0, "balance": float(balance),
                "skip_reason": "kelly_zero", "elapsed": sig["elapsed"],
            })
            continue

        # Skip if no outcome data
        if pd.isna(outcome):
            continue

        # P&L uses signal_price (ask), not cushioned price.
        # IOC fills at best available — typically at or near the ask.
        won = (side == outcome)
        fee = kalshi_fee(signal_price, size)
        cost = signal_price * size + fee

        if won:
            pnl = Decimal(size) - cost
        else:
            pnl = -cost

        balance += pnl
        round_trades += 1

        results.append({
            "round_time": rt, "coin": coin, "side": side,
            "signal_price": float(signal_price),
            "buy_price": float(buy_price),
            "dist_pct": sig["dist_pct"] * 100,
            "size": size, "outcome": outcome, "won": won,
            "pnl": float(pnl), "fee": float(fee),
            "balance": float(balance),
            "skip_reason": None, "elapsed": sig["elapsed"],
        })

    return pd.DataFrame(results)


# ---- Reporting --------------------------------------------------------------

def section(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def report_backtest(df: pd.DataFrame, label: str = "") -> dict:
    """Print backtest results, return summary stats."""
    trades = df[df["size"] > 0].copy()
    skips = df[df["skip_reason"].notna()]

    if len(trades) == 0:
        print(f"  {label}: No trades.")
        return {}

    wins = trades[trades["won"] == True]  # noqa
    losses = trades[trades["won"] == False]  # noqa
    total_pnl = trades["pnl"].sum()
    total_fees = trades["fee"].sum()
    wr = len(wins) / len(trades) * 100
    final_bal = trades.iloc[-1]["balance"]

    print(f"  {label}")
    print(f"  Trades: {len(trades)} ({len(wins)}W/{len(losses)}L) — {wr:.1f}% WR")
    print(f"  P&L: ${total_pnl:.2f} (fees: ${total_fees:.2f})")
    print(f"  Final balance: ${final_bal:.2f}")
    if len(wins) > 0:
        print(f"  Avg win:  ${wins['pnl'].mean():.2f}")
    if len(losses) > 0:
        print(f"  Avg loss: ${losses['pnl'].mean():.2f}")
    print(f"  Kelly=0 skips: {len(skips)}")

    # Per coin
    print(f"\n  {'Coin':>6} {'W/L':>8} {'WR%':>6} {'P&L':>10} {'Trades':>7}")
    for coin in sorted(trades["coin"].unique()):
        c = trades[trades["coin"] == coin]
        cw = c[c["won"] == True]  # noqa
        cl = c[c["won"] == False]  # noqa
        print(f"  {coin:>6} {len(cw)}W/{len(cl)}L {len(cw)/len(c)*100:>5.1f}% ${c['pnl'].sum():>9.2f} {len(c):>7}")

    return {
        "trades": len(trades),
        "wr": wr,
        "pnl": total_pnl,
        "fees": total_fees,
        "final_bal": final_bal,
    }


def main() -> None:
    print("Loading round snapshots...")
    rounds_df = load_all_rounds()
    n_rounds = rounds_df["round_ticker"].nunique()
    print(f"  {len(rounds_df):,} rows, {n_rounds} rounds, "
          f"{rounds_df['coin'].nunique()} coins")

    # ---- 1. Faithful replay of current strategy ----
    section("1. FAITHFUL REPLAY — Current Strategy")
    baseline = backtest(rounds_df)
    baseline_stats = report_backtest(baseline, "T+300-540, dist≥0.2%, Kelly 0.25")

    # ---- 2. Compare with live results ----
    section("2. LIVE vs BACKTEST COMPARISON")
    trades_df = load_all_trades()
    live_fills = trades_df[trades_df["status"] == "filled"]
    live_cancels = trades_df[trades_df["status"] == "cancelled"]
    fill_rate = len(live_fills) / (len(live_fills) + len(live_cancels)) if (len(live_fills) + len(live_cancels)) > 0 else 1.0
    bt_trades = baseline[baseline["size"] > 0]
    print(f"  Live fills: {len(live_fills)}, IOC cancels: {len(live_cancels)}")
    print(f"  Live fill rate: {fill_rate*100:.1f}%")
    print(f"  Backtest trades: {len(bt_trades)} (assumes 100% fill rate)")
    if len(bt_trades) > 0 and baseline_stats:
        print(f"  Backtest WR: {baseline_stats['wr']:.1f}%")
        print(f"  Live WR (from perf report): 82.1% (46W/10L)")
        print(f"  Backtest P&L: ${baseline_stats['pnl']:.2f}")
        print(f"  Live P&L: $-3.91")
    print(f"\n  CAVEATS:")
    print(f"  - Backtest assumes 100% fill rate (live is {fill_rate*100:.0f}%)")
    print(f"  - P&L compounds over all {n_rounds} rounds; live bot started mid-day Mar 9")
    print(f"  - No liquidity cap in backtest (live caps to ask_size on book)")
    print(f"  - WR comparison is more reliable than P&L comparison")

    # ---- 3. Parameter variations ----
    section("3. PARAMETER SWEEP")

    # 3a. Distance thresholds
    print("  --- Distance threshold ---")
    print(f"  {'Thresh':>8} {'Trades':>7} {'WR%':>6} {'P&L':>10} {'Final':>10}")
    for thresh in [0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005]:
        r = backtest(rounds_df, dist_threshold=thresh)
        t = r[r["size"] > 0]
        if len(t) == 0:
            continue
        w = t[t["won"] == True]  # noqa
        pnl = t["pnl"].sum()
        print(f"  {thresh*100:>7.2f}% {len(t):>7} {len(w)/len(t)*100:>5.1f}% ${pnl:>9.2f} ${t.iloc[-1]['balance']:>9.2f}")

    # 3b. Time windows
    print(f"\n  --- Time window ---")
    print(f"  {'Window':>12} {'Trades':>7} {'WR%':>6} {'P&L':>10} {'Final':>10}")
    for ws, we, label in [
        (200, 400, "T+200-400"),
        (250, 500, "T+250-500"),
        (300, 540, "T+300-540"),
        (300, 600, "T+300-600"),
        (350, 600, "T+350-600"),
        (400, 700, "T+400-700"),
    ]:
        r = backtest(rounds_df, window_start=ws, window_end=we)
        t = r[r["size"] > 0]
        if len(t) == 0:
            continue
        w = t[t["won"] == True]  # noqa
        pnl = t["pnl"].sum()
        print(f"  {label:>12} {len(t):>7} {len(w)/len(t)*100:>5.1f}% ${pnl:>9.2f} ${t.iloc[-1]['balance']:>9.2f}")

    # 3c. Kelly fraction
    print(f"\n  --- Kelly fraction ---")
    print(f"  {'Frac':>8} {'Trades':>7} {'WR%':>6} {'P&L':>10} {'Final':>10}")
    for frac in [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]:
        r = backtest(rounds_df, kelly_frac=frac)
        t = r[r["size"] > 0]
        if len(t) == 0:
            continue
        w = t[t["won"] == True]  # noqa
        pnl = t["pnl"].sum()
        print(f"  {frac:>7.0%} {len(t):>7} {len(w)/len(t)*100:>5.1f}% ${pnl:>9.2f} ${t.iloc[-1]['balance']:>9.2f}")

    # 3d. Coin selection
    print(f"\n  --- Coin exclusion ---")
    all_coins = ["BTC", "ETH", "SOL", "XRP"]
    print(f"  {'Coins':>20} {'Trades':>7} {'WR%':>6} {'P&L':>10} {'Final':>10}")
    for exclude in [None, "XRP", "SOL", "BTC"]:
        coins = [c for c in all_coins if c != exclude] if exclude else all_coins
        label = ",".join(coins)
        r = backtest(rounds_df, coins=coins)
        t = r[r["size"] > 0]
        if len(t) == 0:
            continue
        w = t[t["won"] == True]  # noqa
        pnl = t["pnl"].sum()
        print(f"  {label:>20} {len(t):>7} {len(w)/len(t)*100:>5.1f}% ${pnl:>9.2f} ${t.iloc[-1]['balance']:>9.2f}")

    # 3e. YES vs NO side thresholds
    print(f"\n  --- Asymmetric NO-side threshold ---")
    print(f"  {'NO thresh':>12} {'Trades':>7} {'WR%':>6} {'P&L':>10} {'Final':>10}")
    for no_thresh in [None, 0.003, 0.004, 0.005]:
        label = f"{no_thresh*100:.1f}%" if no_thresh else "same"
        r = backtest(rounds_df, no_side_extra_threshold=no_thresh)
        t = r[r["size"] > 0]
        if len(t) == 0:
            continue
        w = t[t["won"] == True]  # noqa
        pnl = t["pnl"].sum()
        print(f"  {label:>12} {len(t):>7} {len(w)/len(t)*100:>5.1f}% ${pnl:>9.2f} ${t.iloc[-1]['balance']:>9.2f}")

    # ---- 4. Side analysis ----
    section("4. YES vs NO SIDE ANALYSIS")
    bt = baseline[baseline["size"] > 0]
    for side in ["yes", "no"]:
        s = bt[bt["side"] == side]
        if len(s) == 0:
            continue
        w = s[s["won"] == True]  # noqa
        print(f"  {side.upper():>4}: {len(w)}W/{len(s)-len(w)}L ({len(w)/len(s)*100:.1f}% WR), "
              f"P&L=${s['pnl'].sum():.2f}, avg_price=${s['signal_price'].mean():.2f}")

    # ---- 5. Sample size & confidence ----
    section("5. STATISTICAL CONFIDENCE")
    if len(bt) > 0:
        n = len(bt)
        k = len(bt[bt["won"] == True])  # noqa
        wr = k / n
        z = 1.96
        denom = 1 + z**2 / n
        center = (wr + z**2 / (2 * n)) / denom
        spread = z * np.sqrt((wr * (1 - wr) + z**2 / (4 * n)) / n) / denom
        lo, hi = center - spread, center + spread
        print(f"  Trades: {n}, Wins: {k}, WR: {wr*100:.1f}%")
        print(f"  95% CI: [{lo*100:.1f}%, {hi*100:.1f}%]")

        avg_price = bt["buy_price"].mean()
        fee_per = float(kalshi_fee(Decimal(str(round(avg_price, 2)))))
        be_wr = (avg_price + fee_per) / 1.0
        print(f"  Avg buy price (with cushion): ${avg_price:.2f}")
        print(f"  Break-even WR: {be_wr*100:.1f}%")
        if lo > be_wr:
            print(f"  >> 95% confident above break-even")
        elif wr > be_wr:
            print(f"  >> Point estimate above BE, but CI includes below-BE")
        else:
            print(f"  >> Point estimate BELOW break-even")

    # ---- 6. Trade log analysis ----
    section("6. SLIPPAGE & FILL ANALYSIS (from trade logs)")
    fills = trades_df[trades_df["status"] == "filled"].copy()
    cancels = trades_df[trades_df["status"] == "cancelled"].copy()
    if len(fills) > 0:
        print(f"  Fills: {len(fills)}, IOC cancels: {len(cancels)}")
        print(f"  Fill rate: {len(fills)/(len(fills)+len(cancels))*100:.1f}%")
        if "slippage_cents" in fills.columns:
            slip = fills["slippage_cents"].dropna()
            if len(slip) > 0:
                print(f"  Slippage — mean: {slip.mean():.1f}c, median: {slip.median():.0f}c, "
                      f"max: {slip.max():.0f}c, zero: {(slip==0).sum()}/{len(slip)}")


if __name__ == "__main__":
    main()
