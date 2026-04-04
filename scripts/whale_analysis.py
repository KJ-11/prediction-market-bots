"""Whale strategy analysis — series scan, filter sweep, multi-signal backtest.

Single script that fetches data once from Supabase, then:
1. Scans for missing series prefixes
2. Runs filter sensitivity sweep (WR × signals/day matrix)
3. Runs fixed backtest with multi-signal-per-market + best filters
4. Reports P&L curve from $100
"""

from __future__ import annotations

import asyncio
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from dateutil.parser import isoparse
from dotenv import dotenv_values


# --- Existing config from v3 ---
WHALE_THRESHOLD = 1000
STOP_LOSS_PCT = 0.15
SLIPPAGE_CENTS = 0.01
STARTING_BANKROLL = 100.0
MAX_CONCURRENT = 2
SETTLE_BUFFER_MIN = 5
PRICE_MIN = 0.85
PRICE_MAX = 0.95

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


def extract_series_prefix(ticker: str) -> str | None:
    """Extract series prefix from ticker, e.g. KXMLBGAME-26APR03-... -> KXMLBGAME"""
    m = re.match(r'^([A-Z0-9]+?)-\d{2}[A-Z]{3}\d{2}', ticker)
    if m:
        return m.group(1)
    # Some tickers use a different format — try splitting on first dash
    parts = ticker.split('-')
    if len(parts) >= 2:
        return parts[0]
    return None


def get_risk_cap(bankroll: float) -> float:
    for floor, ceiling, pct in PHASES:
        if floor <= bankroll < ceiling:
            return bankroll * pct
    return bankroll * PHASES[-1][2]


# ─────────────────────────────────────────────────────────────────────
# Data fetching (shared across all analyses)
# ─────────────────────────────────────────────────────────────────────

async def fetch_data(env_path: Path):
    """Fetch all data from Supabase. Returns (by_market, markets, events, last_trade_times)."""
    env = dotenv_values(env_path)
    from supabase import create_async_client
    client = await create_async_client(env["SUPABASE_URL"], env["SUPABASE_SECRET_KEY"])

    pids = await client.table("platforms").select("id,slug").execute()
    k_id = [p["id"] for p in pids.data if p["slug"] == "kalshi"][0]

    # Fetch whale trades in price range
    print("Fetching whale trades (85-95c)...")
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
    print(f"Fetched {len(all_trades)} whale trades (85-95c)")

    by_market = defaultdict(list)
    for t in all_trades:
        by_market[t["market_id"]].append(t)

    # Fetch markets
    mids = list(by_market.keys())
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

    # Fetch ALL whale trades (any price) for settlement timing
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

    await client.auth.sign_out()

    return by_market, markets, events, last_trade_times


# ─────────────────────────────────────────────────────────────────────
# 1. Series scan
# ─────────────────────────────────────────────────────────────────────

def scan_series(by_market, markets, events):
    """Find all series prefixes with whale activity, compare to KNOWN_SERIES."""
    prefix_stats = defaultdict(lambda: {"markets": set(), "trades": 0, "notional": 0.0, "categories": set()})

    for mid, trades in by_market.items():
        m = markets.get(mid)
        if not m:
            continue
        ticker = m.get("external_id", "")
        prefix = extract_series_prefix(ticker)
        if not prefix:
            continue

        e = events.get(m.get("event_id", ""), {})
        cat = (e.get("category") or "").lower()

        stats = prefix_stats[prefix]
        stats["markets"].add(mid)
        stats["trades"] += len(trades)
        stats["notional"] += sum(float(t["notional"]) for t in trades)
        if cat:
            stats["categories"].add(cat)

    # Sort by trade count descending
    sorted_prefixes = sorted(prefix_stats.items(), key=lambda x: -x[1]["trades"])

    print()
    print("=" * 80)
    print("  1. SERIES PREFIX SCAN")
    print("=" * 80)
    print()
    print(f"  {'Prefix':<30} {'Markets':>8} {'Trades':>8} {'Notional':>12} {'Known':>6}  Categories")
    print(f"  {'─'*30} {'─'*8} {'─'*8} {'─'*12} {'─'*6}  {'─'*20}")

    missing_prefixes = []
    for prefix, stats in sorted_prefixes:
        known = "✓" if prefix in KNOWN_SERIES else "✗"
        cats = ", ".join(sorted(stats["categories"]))
        print(f"  {prefix:<30} {len(stats['markets']):>8} {stats['trades']:>8} ${stats['notional']:>11,.0f} {known:>6}  {cats}")
        if prefix not in KNOWN_SERIES and stats["trades"] >= 5:
            missing_prefixes.append(prefix)

    if missing_prefixes:
        print()
        print(f"  MISSING from KNOWN_SERIES ({len(missing_prefixes)} prefixes with 5+ trades):")
        for p in missing_prefixes:
            s = prefix_stats[p]
            print(f"    {p} — {s['trades']} trades, {len(s['markets'])} markets, {', '.join(sorted(s['categories']))}")
    else:
        print()
        print("  No significant missing prefixes found.")

    # Build expanded series set
    expanded = set(KNOWN_SERIES)
    for prefix, stats in sorted_prefixes:
        cats = stats["categories"]
        # Only add if it's in a relevant category and has meaningful activity
        if cats & CATEGORIES and stats["trades"] >= 3:
            expanded.add(prefix)

    print()
    print(f"  Original KNOWN_SERIES: {len(KNOWN_SERIES)} prefixes")
    print(f"  Expanded series set: {len(expanded)} prefixes (+{len(expanded - KNOWN_SERIES)} new)")
    if expanded - KNOWN_SERIES:
        print(f"  New additions: {sorted(expanded - KNOWN_SERIES)}")

    return expanded


# ─────────────────────────────────────────────────────────────────────
# 2. Signal finder (parameterized, multi-signal-per-market)
# ─────────────────────────────────────────────────────────────────────

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
    signal_time: datetime
    settle_time: datetime
    resolution: str
    won: bool
    lockup_minutes: float


def find_signals(
    by_market, markets, events, last_trade_times,
    *,
    series_set: set[str],
    min_whale_count: int = 3,
    consensus_pct: float = 90.0,
    window_minutes: int = 30,
    multi_signal: bool = False,
) -> list[Signal]:
    """Find whale signals. If multi_signal=True, emit multiple per market with cooldown."""
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
        if not any(ticker.startswith(s) for s in series_set):
            continue

        resolution = m["resolution"].lower()
        settle_time = last_trade_times.get(mid)
        if not settle_time:
            continue
        settle_time = settle_time + timedelta(minutes=SETTLE_BUFFER_MIN)

        sorted_trades = sorted(trades, key=lambda t: t["traded_at"])

        # Track cooldown: after emitting a signal, skip past that window
        cooldown_until = None

        for i, t_start in enumerate(sorted_trades):
            start_time = isoparse(str(t_start["traded_at"]))

            # Skip if within cooldown from previous signal on this market
            if cooldown_until and start_time < cooldown_until:
                continue

            window_end = start_time + timedelta(minutes=window_minutes)

            window = [t_start]
            for j in range(i + 1, len(sorted_trades)):
                if isoparse(str(sorted_trades[j]["traded_at"])) <= window_end:
                    window.append(sorted_trades[j])
                else:
                    break

            if len(window) < min_whale_count:
                continue

            yes_v = sum(float(t["notional"]) for t in window if t["outcome"].lower() == "yes")
            no_v = sum(float(t["notional"]) for t in window if t["outcome"].lower() == "no")
            total = yes_v + no_v
            if total == 0:
                continue
            cons_side = "yes" if yes_v >= no_v else "no"
            cons_pct = max(yes_v, no_v) / total * 100
            if cons_pct < consensus_pct:
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

            if not multi_signal:
                break  # original behavior: one signal per market

            # Set cooldown: skip past this window before looking for next signal
            cooldown_until = window_end

    return signals


# ─────────────────────────────────────────────────────────────────────
# 3. Filter sensitivity sweep
# ─────────────────────────────────────────────────────────────────────

def run_filter_sweep(by_market, markets, events, last_trade_times, series_set):
    """Sweep filter parameters, show WR × signals/day matrix."""
    whale_counts = [1, 2, 3, 5]
    consensuses = [70.0, 80.0, 90.0]
    windows = [15, 30, 60]

    # Count unique days in data for signals/day calc
    all_dates = set()
    for mid, trades in by_market.items():
        for t in trades:
            all_dates.add(isoparse(str(t["traded_at"])).strftime("%Y-%m-%d"))
    n_days = max(len(all_dates), 1)

    print()
    print("=" * 80)
    print("  2. FILTER SENSITIVITY SWEEP")
    print(f"     (expanded series: {len(series_set)} prefixes, {n_days} days of data)")
    print("=" * 80)

    results = []

    for wc in whale_counts:
        for cp in consensuses:
            for wm in windows:
                sigs = find_signals(
                    by_market, markets, events, last_trade_times,
                    series_set=series_set,
                    min_whale_count=wc,
                    consensus_pct=cp,
                    window_minutes=wm,
                    multi_signal=False,  # single signal for WR measurement
                )
                n = len(sigs)
                wins = sum(1 for s in sigs if s.won)
                wr = wins / n * 100 if n > 0 else 0
                per_day = n / n_days

                # Compute avg entry price and breakeven WR
                if sigs:
                    avg_ep = sum(s.avg_entry_price for s in sigs) / len(sigs)
                    # Breakeven: need entry_price + fees < WR * 1.0 + (1-WR) * stop_price
                    fee_at_entry = kalshi_fee(avg_ep, 1)
                    cost = avg_ep + fee_at_entry
                    stop_price = avg_ep * (1 - STOP_LOSS_PCT)
                    stop_fee = kalshi_fee(stop_price, 1)
                    loss_recovery = stop_price - stop_fee
                    # breakeven: WR * 1.0 + (1-WR) * loss_recovery = cost
                    # WR = (cost - loss_recovery) / (1.0 - loss_recovery)
                    be_wr = (cost - loss_recovery) / (1.0 - loss_recovery) * 100 if (1.0 - loss_recovery) > 0 else 100
                else:
                    avg_ep = 0
                    be_wr = 0

                results.append({
                    "wc": wc, "cp": cp, "wm": wm,
                    "n": n, "wr": wr, "per_day": per_day,
                    "avg_ep": avg_ep, "be_wr": be_wr,
                    "edge": wr - be_wr,
                })

    # Print as table grouped by window
    for wm in windows:
        print(f"\n  Window: {wm} min")
        print(f"  {'Whales':>8} {'Cons%':>8} {'Signals':>8} {'Sig/day':>8} {'WR%':>8} {'BE_WR%':>8} {'Edge%':>8}")
        print(f"  {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*8}")
        for r in results:
            if r["wm"] != wm:
                continue
            edge_marker = " ←" if r["edge"] > 2 and r["per_day"] >= 2 else ""
            print(f"  {r['wc']:>8} {r['cp']:>7.0f}% {r['n']:>8} {r['per_day']:>7.1f} {r['wr']:>7.1f}% {r['be_wr']:>7.1f}% {r['edge']:>7.1f}%{edge_marker}")

    # Find sweet spot: edge > 2% AND per_day >= 2
    viable = [r for r in results if r["edge"] > 1.5 and r["per_day"] >= 2]
    if viable:
        # Sort by edge * per_day (maximize expected daily profit)
        viable.sort(key=lambda r: -r["edge"] * r["per_day"])
        best = viable[0]
        print(f"\n  SWEET SPOT: {best['wc']} whales, {best['cp']:.0f}% consensus, {best['wm']}min window")
        print(f"    → {best['n']} signals ({best['per_day']:.1f}/day), {best['wr']:.1f}% WR, {best['edge']:.1f}% edge")
    else:
        print("\n  No combo found with edge > 1.5% AND 2+ signals/day")
        # Show best by edge regardless
        by_edge = sorted(results, key=lambda r: -r["edge"])
        if by_edge:
            b = by_edge[0]
            print(f"  Highest edge: {b['wc']} whales, {b['cp']:.0f}% cons, {b['wm']}min → {b['wr']:.1f}% WR, {b['per_day']:.1f}/day, {b['edge']:.1f}% edge")

    return results


# ─────────────────────────────────────────────────────────────────────
# 4. Fixed backtest simulation
# ─────────────────────────────────────────────────────────────────────

@dataclass
class OpenPosition:
    entry_cost: float
    entry_price: float
    contracts: int
    signal: Signal
    settle_time: datetime
    market_id: str


def simulate(signals: list[Signal], shuffle_seed=None) -> dict:
    """Simulate with proper concurrent slot modeling + multi-signal-per-market."""
    sigs = sorted(signals, key=lambda s: s.signal_time)

    if shuffle_seed is not None:
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
    trades_executed = []
    daily_bankrolls = {}
    bankroll_curve = [(sigs[0].signal_time if sigs else datetime.now(), bankroll)]

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
                    "market_id": pos.market_id,
                })
                bankroll_curve.append((sig.signal_time, bankroll))

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

        # Multi-signal fix: check if we already have an open position on this market
        # (not "have we EVER traded this market")
        open_market_ids = {p.market_id for p in open_positions}
        if sig.market_id in open_market_ids:
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
            market_id=sig.market_id,
        ))

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
            "market_id": pos.market_id,
        })

    if current_day:
        daily_bankrolls[current_day] = bankroll

    bankroll_curve.append((
        sigs[-1].settle_time if sigs else datetime.now(), bankroll
    ))

    return {
        "trades": trades_executed,
        "final_bankroll": bankroll,
        "daily_bankrolls": daily_bankrolls,
        "killed": killed,
        "bankroll_curve": bankroll_curve,
    }


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

async def main():
    env_path = Path("/Users/kj/Code/Profitlabs/profitlabs-ml-pipeline/.env")
    if len(sys.argv) > 1:
        env_path = Path(sys.argv[1])

    print("Fetching data from Supabase...")
    by_market, markets, events, last_trade_times = await fetch_data(env_path)

    # --- Step 1: Series scan ---
    expanded_series = scan_series(by_market, markets, events)

    # --- Step 2: Filter sensitivity sweep ---
    sweep_results = run_filter_sweep(by_market, markets, events, last_trade_times, expanded_series)

    # Find best viable combo for backtest
    viable = [r for r in sweep_results if r["edge"] > 1.5 and r["per_day"] >= 2]
    if viable:
        viable.sort(key=lambda r: -r["edge"] * r["per_day"])
        best = viable[0]
    else:
        # Fall back to original strict filters
        best = {"wc": 3, "cp": 90.0, "wm": 30}

    best_wc = best["wc"]
    best_cp = best["cp"]
    best_wm = best["wm"]

    # --- Step 3: Multi-signal backtest with best filters ---
    print()
    print("=" * 80)
    print(f"  3. MULTI-SIGNAL BACKTEST")
    print(f"     Filters: {best_wc} whales, {best_cp:.0f}% consensus, {best_wm}min window")
    print(f"     Series: {len(expanded_series)} prefixes, multi_signal=True")
    print("=" * 80)

    # Compare single vs multi signal count
    sigs_single = find_signals(
        by_market, markets, events, last_trade_times,
        series_set=expanded_series,
        min_whale_count=best_wc,
        consensus_pct=best_cp,
        window_minutes=best_wm,
        multi_signal=False,
    )
    sigs_multi = find_signals(
        by_market, markets, events, last_trade_times,
        series_set=expanded_series,
        min_whale_count=best_wc,
        consensus_pct=best_cp,
        window_minutes=best_wm,
        multi_signal=True,
    )
    sigs_multi.sort(key=lambda s: s.signal_time)

    all_dates = set()
    for s in sigs_multi:
        all_dates.add(s.signal_time.strftime("%Y-%m-%d"))
    n_days = max(len(all_dates), 1)

    wins_s = sum(1 for s in sigs_single if s.won)
    wins_m = sum(1 for s in sigs_multi if s.won)

    print(f"\n  Single-signal: {len(sigs_single)} signals, {wins_s/len(sigs_single)*100:.1f}% WR" if sigs_single else "\n  Single-signal: 0 signals")
    print(f"  Multi-signal:  {len(sigs_multi)} signals, {wins_m/len(sigs_multi)*100:.1f}% WR, {len(sigs_multi)/n_days:.1f}/day" if sigs_multi else "  Multi-signal: 0 signals")

    if not sigs_multi:
        print("  No signals to simulate.")
        return

    # --- Run simulation ---
    print("\n  DETERMINISTIC P&L (chronological):")
    result = simulate(sigs_multi)
    trades = result["trades"]
    final = result["final_bankroll"]

    if trades:
        wins = sum(1 for t in trades if t["won"])
        losses = len(trades) - wins
        total_pnl = sum(t["pnl"] for t in trades)
        avg_lockup = sum(t["lockup_min"] for t in trades) / len(trades)

        print(f"    Trades executed: {len(trades)} ({len(trades)/n_days:.1f}/day)")
        print(f"    Win/Loss: {wins}/{losses} ({wins/len(trades)*100:.1f}% WR)")
        print(f"    Starting: ${STARTING_BANKROLL:.2f}")
        print(f"    Final: ${final:,.2f}")
        print(f"    Return: {(final/STARTING_BANKROLL - 1)*100:.1f}%")
        print(f"    Total P&L: ${total_pnl:,.2f}")
        print(f"    Avg P&L/trade: ${total_pnl/len(trades):.4f}")
        print(f"    Avg lockup: {avg_lockup:.0f} min")
        print(f"    Killed: {result['killed']}")

        # Daily P&L
        print(f"\n    Daily bankroll:")
        for day, bal in sorted(result["daily_bankrolls"].items()):
            print(f"      {day}: ${bal:,.2f}")

    # --- Monte Carlo ---
    print()
    N_SIMS = 1000
    print(f"  MONTE CARLO ({N_SIMS} sims, shuffled within each day):")
    mc_finals = []
    mc_daily = []

    for i in range(N_SIMS):
        r = simulate(sigs_multi, shuffle_seed=i)
        mc_finals.append(r["final_bankroll"])
        mc_daily.append(list(r["daily_bankrolls"].values()))

    finals = np.array(mc_finals)
    print(f"    Median final: ${np.median(finals):,.2f}")
    print(f"    Mean final: ${np.mean(finals):,.2f}")
    print(f"    p5: ${np.percentile(finals, 5):,.2f}")
    print(f"    p25: ${np.percentile(finals, 25):,.2f}")
    print(f"    p75: ${np.percentile(finals, 75):,.2f}")
    print(f"    p95: ${np.percentile(finals, 95):,.2f}")
    print(f"    Ruin (<$1): {(finals <= 1).mean()*100:.1f}%")

    # Drawdown
    max_dds = []
    for path in mc_daily:
        if not path:
            continue
        arr = np.array(path)
        pk = np.maximum.accumulate(arr)
        dd = np.where(pk > 0, (pk - arr) / pk, 0)
        max_dds.append(dd.max() * 100)
    if max_dds:
        max_dds = np.array(max_dds)
        print(f"    Max drawdown (median): {max_dds_median:.1f}%" if (max_dds_median := np.median(max_dds)) else "")
        print(f"    Max drawdown (p95): {np.percentile(max_dds, 95):.1f}%")

    # Also run with ORIGINAL strict filters for comparison
    print()
    print("  COMPARISON: Original strict filters (3 whales, 90% cons, 30min, known series only)")
    sigs_orig = find_signals(
        by_market, markets, events, last_trade_times,
        series_set=KNOWN_SERIES,
        min_whale_count=3,
        consensus_pct=90.0,
        window_minutes=30,
        multi_signal=False,
    )
    if sigs_orig:
        sigs_orig.sort(key=lambda s: s.signal_time)
        r_orig = simulate(sigs_orig)
        t_orig = r_orig["trades"]
        if t_orig:
            w_orig = sum(1 for t in t_orig if t["won"])
            print(f"    Signals: {len(sigs_orig)}, Trades: {len(t_orig)}, WR: {w_orig/len(t_orig)*100:.1f}%")
            print(f"    Final: ${r_orig['final_bankroll']:,.2f} (vs ${final:,.2f} with new filters)")

    print()
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
