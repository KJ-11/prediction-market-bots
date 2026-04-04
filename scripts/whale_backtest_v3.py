"""E2E backtest v3 — proper concurrent position modeling.

Key fix: positions settle when last trade occurs on the market (proxy for
match end), not next day. This allows rapid slot turnover.

Uses last_trade timestamp from trades table as settlement proxy (+5min buffer).
"""

from __future__ import annotations

import asyncio
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from dateutil.parser import isoparse
from dotenv import dotenv_values

# --- Config ---
WHALE_THRESHOLD = 1000
MIN_WHALE_COUNT = 3
CONSENSUS_PCT = 90.0
WINDOW_MINUTES = 30
PRICE_MIN = 0.85
PRICE_MAX = 0.95
STOP_LOSS_PCT = 0.15
SLIPPAGE_CENTS = 0.01
STARTING_BANKROLL = 100.0
MAX_CONCURRENT = 2
SETTLE_BUFFER_MIN = 5  # Minutes after last trade before we consider it settled

CATEGORIES = {"sports", "economics"}
KNOWN_SERIES = {
    "KXMLBGAME", "KXNBAGAME", "KXNHLGAME", "KXATPMATCH", "KXWTAMATCH",
    "KXIPLGAME", "KXUFCFIGHT", "KXMLBHR", "KXNBA1HTOTAL", "KXMLB1HTOTAL",
    "KXNHL1HTOTAL", "KXATPCHALLENGERMATCH", "KXMLBHIT", "KXMLBSTRIKEOUT",
    "KXNBAPLAYER", "KXNBAPLAYERPTS", "KXNBAAST", "KXNBA2D", "KXNBAREB",
    "KXNHLGOAL", "KXNHLPTS", "KXMLBHRR", "KXWTI", "KXINXU", "KXINXD",
    "KXGOLD", "KXSILVER", "KXNATGAS", "KXT20MATCH",
}

PHASES = [(0, 500, 1.0), (500, 1_000, 0.50), (1_000, 5_000, 0.30),
          (5_000, 50_000, 0.20), (50_000, float("inf"), 0.10)]
MAX_CONSECUTIVE_LOSSES = 3
DAILY_LOSS_LIMIT_PCT = 20.0
KILL_SWITCH_PCT = 40.0
FEE_COEFF = 0.07


def kalshi_fee(price: float, contracts: int) -> float:
    return math.ceil(FEE_COEFF * contracts * price * (1 - price) * 100) / 100


def parse_event_date(ticker: str):
    m = re.search(r'(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})', ticker, re.IGNORECASE)
    if not m:
        return None
    y = 2000 + int(m.group(1))
    mo = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}[m.group(2).upper()]
    return f"{y}-{mo:02d}-{int(m.group(3)):02d}"


def get_risk_cap(bankroll: float) -> float:
    for floor, ceiling, pct in PHASES:
        if floor <= bankroll < ceiling:
            return bankroll * pct
    return bankroll * PHASES[-1][2]


@dataclass
class Signal:
    market_id: str
    ticker: str
    event_date: str
    category: str
    consensus_side: str
    consensus_pct: float
    whale_count: int
    total_notional: float
    avg_entry_price: float
    signal_time: datetime  # when signal fires
    settle_time: datetime  # last trade + buffer (when capital frees)
    resolution: str
    won: bool
    lockup_minutes: float


def find_signals(by_market, markets, events, last_trade_times) -> list[Signal]:
    signals = []

    for mid, trades in by_market.items():
        m = markets.get(mid)
        if not m or not m.get("resolved") or not m.get("resolution"):
            continue
        e = events.get(m.get("event_id", ""), {})
        cat = (e.get("category") or "").lower()
        if cat not in CATEGORIES:
            continue
        ticker = m.get("external_id", "")
        event_date = parse_event_date(ticker)
        if not event_date:
            continue
        if not any(ticker.startswith(s) for s in KNOWN_SERIES):
            continue

        resolution = m["resolution"].lower()
        settle_time = last_trade_times.get(mid)
        if not settle_time:
            continue
        settle_time = settle_time + timedelta(minutes=SETTLE_BUFFER_MIN)

        sorted_trades = sorted(trades, key=lambda t: t["traded_at"])

        for i, t_start in enumerate(sorted_trades):
            start_time = isoparse(str(t_start["traded_at"]))
            window_end = start_time + timedelta(minutes=WINDOW_MINUTES)

            window = [t_start]
            for j in range(i + 1, len(sorted_trades)):
                if isoparse(str(sorted_trades[j]["traded_at"])) <= window_end:
                    window.append(sorted_trades[j])
                else:
                    break

            if len(window) < MIN_WHALE_COUNT:
                continue

            yes_v = sum(float(t["notional"]) for t in window if t["outcome"].lower() == "yes")
            no_v = sum(float(t["notional"]) for t in window if t["outcome"].lower() == "no")
            total = yes_v + no_v
            if total == 0:
                continue
            cons_side = "yes" if yes_v >= no_v else "no"
            cons_pct = max(yes_v, no_v) / total * 100
            if cons_pct < CONSENSUS_PCT:
                continue

            consensus_trades = [
                t for t in window
                if t["outcome"].lower() == cons_side
                and PRICE_MIN <= float(t["price"]) <= PRICE_MAX
            ]
            if not consensus_trades:
                continue

            avg_price = sum(float(t["price"]) for t in consensus_trades) / len(consensus_trades)

            trade_date = start_time.strftime("%Y-%m-%d")
            evt_dt = datetime.strptime(event_date, "%Y-%m-%d")
            trd_dt = datetime.strptime(trade_date, "%Y-%m-%d")
            if not (trd_dt == evt_dt or trd_dt == evt_dt - timedelta(days=1)):
                continue

            won = cons_side == resolution
            lockup = (settle_time - start_time).total_seconds() / 60

            signals.append(Signal(
                market_id=mid, ticker=ticker, event_date=event_date,
                category=cat, consensus_side=cons_side, consensus_pct=cons_pct,
                whale_count=len(window), total_notional=total,
                avg_entry_price=avg_price, signal_time=start_time,
                settle_time=settle_time, resolution=resolution,
                won=won, lockup_minutes=max(lockup, 1),
            ))
            break  # first signal per market

    return signals


@dataclass
class OpenPosition:
    entry_cost: float
    entry_price: float
    contracts: int
    signal: Signal
    settle_time: datetime  # when capital frees


def simulate(signals: list[Signal], shuffle_seed=None) -> dict:
    """Simulate with proper concurrent slot modeling."""
    sigs = sorted(signals, key=lambda s: s.signal_time)

    if shuffle_seed is not None:
        # Shuffle within each day for Monte Carlo
        rng = np.random.default_rng(shuffle_seed)
        by_day = defaultdict(list)
        for s in sigs:
            by_day[s.signal_time.strftime("%Y-%m-%d")].append(s)
        sigs = []
        for day in sorted(by_day.keys()):
            day_sigs = list(by_day[day])
            rng.shuffle(day_sigs)
            sigs.extend(day_sigs)

    bankroll = STARTING_BANKROLL
    peak = bankroll
    day_start_bankroll = bankroll
    current_day = None
    consecutive_losses = 0
    skip_next = False
    stopped_for_day = False
    killed = False

    open_positions: list[OpenPosition] = []
    traded_markets = set()
    trades_executed = []
    daily_bankrolls = {}

    for sig in sigs:
        day = sig.signal_time.strftime("%Y-%m-%d")

        if day != current_day:
            if current_day:
                daily_bankrolls[current_day] = bankroll
            current_day = day
            day_start_bankroll = bankroll
            stopped_for_day = False

        if killed:
            continue

        # Settle positions whose settle_time has passed
        still_open = []
        for pos in open_positions:
            if sig.signal_time >= pos.settle_time:
                # Settle
                if pos.signal.won:
                    pnl = pos.contracts * 1.0 - pos.entry_cost
                else:
                    sp = pos.entry_price * (1 - STOP_LOSS_PCT)
                    ef = kalshi_fee(sp, pos.contracts)
                    pnl = (sp * pos.contracts - ef) - pos.entry_cost

                bankroll += pnl
                bankroll = max(bankroll, 0)
                trades_executed.append({
                    "won": pos.signal.won, "pnl": pnl,
                    "entry_price": pos.entry_price,
                    "contracts": pos.contracts, "entry_cost": pos.entry_cost,
                    "lockup_min": pos.signal.lockup_minutes,
                })

                if pos.signal.won:
                    consecutive_losses = 0
                else:
                    consecutive_losses += 1
                    if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                        skip_next = True

                peak = max(peak, bankroll)
                if peak > 0 and (peak - bankroll) / peak * 100 >= KILL_SWITCH_PCT:
                    killed = True
            else:
                still_open.append(pos)
        open_positions = still_open

        if killed or stopped_for_day or bankroll <= 0:
            continue
        if skip_next:
            skip_next = False
            continue
        if len(open_positions) >= MAX_CONCURRENT:
            continue
        if sig.market_id in traded_markets:
            continue

        locked = sum(p.entry_cost for p in open_positions)
        available = bankroll - locked
        if available <= 0:
            continue

        entry_price = min(sig.avg_entry_price + SLIPPAGE_CENTS, 0.99)
        if entry_price < PRICE_MIN or entry_price > PRICE_MAX + SLIPPAGE_CENTS:
            continue

        risk_cap = get_risk_cap(bankroll)
        dollar_size = min(risk_cap * 0.5, available)
        fee_per = kalshi_fee(entry_price, 1)
        cost_per = entry_price + fee_per
        if cost_per <= 0:
            continue
        contracts = int(dollar_size / cost_per)
        if contracts <= 0:
            continue
        entry_fee = kalshi_fee(entry_price, contracts)
        entry_cost = entry_price * contracts + entry_fee
        if entry_cost > available:
            contracts = max(int(available / cost_per) - 1, 0)
            if contracts <= 0:
                continue
            entry_fee = kalshi_fee(entry_price, contracts)
            entry_cost = entry_price * contracts + entry_fee

        open_positions.append(OpenPosition(
            entry_cost=entry_cost, entry_price=entry_price,
            contracts=contracts, signal=sig, settle_time=sig.settle_time,
        ))
        traded_markets.add(sig.market_id)

        if day_start_bankroll > 0:
            locked_now = sum(p.entry_cost for p in open_positions)
            if (day_start_bankroll - (bankroll - locked_now)) / day_start_bankroll * 100 >= DAILY_LOSS_LIMIT_PCT:
                stopped_for_day = True

    # Settle remaining
    for pos in open_positions:
        if pos.signal.won:
            pnl = pos.contracts * 1.0 - pos.entry_cost
        else:
            sp = pos.entry_price * (1 - STOP_LOSS_PCT)
            ef = kalshi_fee(sp, pos.contracts)
            pnl = (sp * pos.contracts - ef) - pos.entry_cost
        bankroll += pnl
        bankroll = max(bankroll, 0)
        trades_executed.append({
            "won": pos.signal.won, "pnl": pnl,
            "entry_price": pos.entry_price,
            "contracts": pos.contracts, "entry_cost": pos.entry_cost,
            "lockup_min": pos.signal.lockup_minutes,
        })

    if current_day:
        daily_bankrolls[current_day] = bankroll

    return {
        "trades": trades_executed,
        "final_bankroll": bankroll,
        "daily_bankrolls": daily_bankrolls,
        "killed": killed,
    }


async def main():
    env_path = Path("/Users/kj/Code/Profitlabs/profitlabs-ml-pipeline/.env")
    if len(sys.argv) > 1:
        env_path = Path(sys.argv[1])

    env = dotenv_values(env_path)
    from supabase import create_async_client
    client = await create_async_client(env["SUPABASE_URL"], env["SUPABASE_SECRET_KEY"])

    pids = await client.table("platforms").select("id,slug").execute()
    k_id = [p["id"] for p in pids.data if p["slug"] == "kalshi"][0]

    # Fetch whale trades
    print("Fetching whale trades...")
    all_trades = []
    offset = 0
    while True:
        resp = await client.table("trades").select(
            "market_id, price, notional, outcome, traded_at"
        ).eq("platform_id", k_id).gte("notional", WHALE_THRESHOLD).gte(
            "price", PRICE_MIN
        ).lte("price", PRICE_MAX).order("traded_at", desc=True).range(
            offset, offset + 999
        ).execute()
        if not resp.data:
            break
        all_trades.extend(resp.data)
        offset += 1000
        if len(all_trades) >= 500000:
            break
        if len(all_trades) % 10000 < 1000:
            print(f"  {len(all_trades)} trades...", flush=True)
    print(f"Fetched {len(all_trades)} trades")

    # Fetch markets
    mids = list(set(t["market_id"] for t in all_trades))
    print(f"Fetching {len(mids)} markets...")
    markets = {}
    for i in range(0, len(mids), 50):
        resp = await client.table("markets").select(
            "id, external_id, resolution, resolved, event_id"
        ).in_("id", mids[i:i + 50]).execute()
        for m in resp.data:
            markets[m["id"]] = m

    # Fetch events
    eids = list(set(m.get("event_id") for m in markets.values() if m.get("event_id")))
    print(f"Fetching {len(eids)} events...")
    events = {}
    for i in range(0, len(eids), 50):
        resp = await client.table("events").select("id, category").in_("id", eids[i:i + 50]).execute()
        for e in resp.data:
            events[e["id"]] = e

    # Get last trade time per market (settlement proxy)
    # Use the whale trades we already have — last whale trade at any price
    # is a good proxy (whales are active until the match ends)
    print("Computing last trade times from whale trade data...")
    by_market = defaultdict(list)
    for t in all_trades:
        by_market[t["market_id"]].append(t)

    # Also fetch whale trades at ALL prices (not just 85-95c) for settlement timing
    # A whale buying at 99c right before settlement is our best timestamp
    print("Fetching all whale trades for settlement timing...")
    all_whale = []
    offset = 0
    while True:
        resp = await client.table("trades").select(
            "market_id, traded_at"
        ).eq("platform_id", k_id).gte("notional", WHALE_THRESHOLD).order(
            "traded_at", desc=True
        ).range(offset, offset + 999).execute()
        if not resp.data:
            break
        all_whale.extend(resp.data)
        offset += 1000
        if len(all_whale) >= 700000:
            break
        if len(all_whale) % 50000 < 1000:
            print(f"  {len(all_whale)} whale trades...", flush=True)
    print(f"Fetched {len(all_whale)} whale trades (all prices)")

    last_trade_times = {}
    for t in all_whale:
        mid = t["market_id"]
        ts = isoparse(str(t["traded_at"]))
        if mid not in last_trade_times or ts > last_trade_times[mid]:
            last_trade_times[mid] = ts

    print(f"Got last trade times for {len(last_trade_times)} markets")

    # Find signals
    print("Finding signals...")
    signals = find_signals(by_market, markets, events, last_trade_times)
    signals.sort(key=lambda s: s.signal_time)
    print(f"Found {len(signals)} signals")

    if not signals:
        print("No signals found.")
        return

    date_range = f"{signals[0].signal_time.strftime('%Y-%m-%d')} to {signals[-1].signal_time.strftime('%Y-%m-%d')}"

    # --- Lockup analysis ---
    lockups = [s.lockup_minutes for s in signals]
    lockups_sorted = sorted(lockups)

    print()
    print("=" * 70)
    print("  WHALE-FOLLOWING E2E BACKTEST v3")
    print("=" * 70)
    print(f"  Date range: {date_range}")
    print(f"  Signals: {len(signals)} ({len(signals)/35:.1f}/day)")
    print(f"  Win rate: {sum(1 for s in signals if s.won)/len(signals)*100:.1f}%")
    print()

    print("  LOCKUP TIME (signal → settlement):")
    for label, lo, hi in [("<15min", 0, 15), ("15-30min", 15, 30), ("30-60min", 30, 60),
                           ("1-2hr", 60, 120), ("2-4hr", 120, 240), (">4hr", 240, float("inf"))]:
        sub = [l for l in lockups if lo <= l < hi]
        if sub:
            pct = len(sub) / len(lockups) * 100
            print(f"    {label:>10}: {len(sub):>5} ({pct:>5.1f}%)")
    print(f"    Median: {lockups_sorted[len(lockups_sorted)//2]:.0f} min")
    print(f"    p25: {lockups_sorted[len(lockups_sorted)//4]:.0f} min, p75: {lockups_sorted[3*len(lockups_sorted)//4]:.0f} min")

    # --- Deterministic simulation ---
    print()
    print("  DETERMINISTIC P&L (chronological):")
    result = simulate(signals)
    trades = result["trades"]
    final = result["final_bankroll"]

    if trades:
        wins = sum(1 for t in trades if t["won"])
        total_pnl = sum(t["pnl"] for t in trades)
        print(f"    Trades executed: {len(trades)} ({len(trades)/35:.1f}/day)")
        print(f"    Win/Loss: {wins}/{len(trades)-wins} ({wins/len(trades)*100:.1f}% WR)")
        print(f"    Starting: ${STARTING_BANKROLL:.2f}")
        print(f"    Final: ${final:,.2f}")
        print(f"    Return: {(final/STARTING_BANKROLL - 1)*100:.1f}%")
        print(f"    Total P&L: ${total_pnl:,.2f}")
        print(f"    Avg P&L/trade: ${total_pnl/len(trades):.2f}")
        print(f"    Killed: {result['killed']}")

    # --- Monte Carlo ---
    print()
    N_SIMS = 1000
    print(f"  MONTE CARLO ({N_SIMS} sims, shuffled within each day):")
    mc_finals = []
    mc_daily = []

    for i in range(N_SIMS):
        r = simulate(signals, shuffle_seed=i)
        mc_finals.append(r["final_bankroll"])
        mc_daily.append(list(r["daily_bankrolls"].values()))

    finals = np.array(mc_finals)
    print(f"    Median final: ${np.median(finals):,.2f}")
    print(f"    Mean final: ${np.mean(finals):,.2f}")
    print(f"    p5: ${np.percentile(finals, 5):,.2f}")
    print(f"    p95: ${np.percentile(finals, 95):,.2f}")
    print(f"    Ruin: {(finals <= 0).mean()*100:.1f}%")

    # Drawdown from daily snapshots
    max_dds = []
    for path in mc_daily:
        if not path:
            continue
        arr = np.array(path)
        peak = np.maximum.accumulate(arr)
        dd = np.where(peak > 0, (peak - arr) / peak, 0)
        max_dds.append(dd.max() * 100)
    if max_dds:
        max_dds = np.array(max_dds)
        print(f"    Max drawdown (median): {np.median(max_dds):.1f}%")
        print(f"    Max drawdown (p95): {np.percentile(max_dds, 95):.1f}%")

    # Trades per sim
    trade_counts = []
    for i in range(min(100, N_SIMS)):
        r = simulate(signals, shuffle_seed=i + 10000)
        trade_counts.append(len(r["trades"]))
    print(f"    Avg trades/sim: {sum(trade_counts)/len(trade_counts):.0f}")

    print()
    print("=" * 70)

    await client.auth.sign_out()


if __name__ == "__main__":
    asyncio.run(main())
