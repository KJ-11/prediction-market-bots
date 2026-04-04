"""E2E backtest v5 — expanded series, kill switch sensitivity, long horizon.

Changes from v4:
- Runs with EXPANDED series (all sports/econ with 20+ whale trades)
- Kill switch sensitivity: test 40%, 50%, 60%, disabled
- Long horizon: tiles 35 days of signals to project 6 months
- Shows compounding curves and time-to-milestones
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
SETTLE_BUFFER_MIN = 5

CATEGORIES = {"sports", "economics"}

KNOWN_SERIES = {
    "KXMLBGAME", "KXNBAGAME", "KXNHLGAME", "KXIPLGAME", "KXUFCFIGHT",
    "KXATPMATCH", "KXATPCHALLENGERMATCH", "KXWTAMATCH",
    "KXMLBHR", "KXMLB1HTOTAL", "KXMLBHIT", "KXMLBSTRIKEOUT",
    "KXNBA1HTOTAL", "KXNBAPLAYER", "KXNBAPLAYERPTS",
    "KXNBAAST", "KXNBA2D", "KXNBAREB",
    "KXNHL1HTOTAL", "KXNHLGOAL", "KXNHLPTS",
    "KXMLBHRR", "KXT20MATCH",
    "KXWTI", "KXINXU", "KXINXD", "KXGOLD", "KXSILVER", "KXNATGAS",
}

PHASES = [
    (50_000, 0.10),
    (5_000, 0.20),
    (1_000, 0.30),
    (500, 0.50),
    (0, 1.00),
]

MAX_CONSECUTIVE_LOSSES = 3
DAILY_LOSS_LIMIT_PCT = 20.0
FEE_COEFF = 0.07


def kalshi_fee(price: float, contracts: int) -> float:
    raw = FEE_COEFF * contracts * price * (1 - price)
    return math.ceil(raw * 100) / 100


def parse_event_date(ticker: str):
    m = re.search(r'(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})', ticker, re.IGNORECASE)
    if not m:
        return None
    y = 2000 + int(m.group(1))
    months = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
    mo = months[m.group(2).upper()]
    return f"{y}-{mo:02d}-{int(m.group(3)):02d}"


def extract_series_prefix(ticker: str) -> str | None:
    m = re.match(r'^([A-Z0-9]+?)-\d{2}[A-Z]{3}\d{2}', ticker)
    if m:
        return m.group(1)
    parts = ticker.split('-')
    if len(parts) >= 2:
        return parts[0]
    return None


def get_alloc_pct(balance: float) -> float:
    for threshold, pct in PHASES:
        if balance >= threshold:
            return pct
    return 1.0


def compute_size(price: float, sizing_balance: float) -> tuple[int, float]:
    if price <= 0 or price >= 1.0 or sizing_balance <= 0:
        return 0, 0.0

    alloc_pct = get_alloc_pct(sizing_balance)
    dollar_amount = sizing_balance * alloc_pct

    cost_per = price + kalshi_fee(price, 1)
    if cost_per <= 0:
        return 0, 0.0

    size = int(dollar_amount / cost_per)

    while size > 0:
        fee = kalshi_fee(price, size)
        total_cost = price * size + fee
        if total_cost <= sizing_balance:
            return size, total_cost
        size -= 1

    return 0, 0.0


# ─── Signal ───

@dataclass
class Signal:
    market_id: str
    ticker: str
    event_id: str
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


def find_signals(by_market, markets, events, last_trade_times, *, series_set):
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

        event_id = m.get("event_id", "")
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
                market_id=mid, ticker=ticker, event_id=event_id,
                event_date=event_date, category=cat,
                consensus_side=cons_side, consensus_pct=cons_pct,
                whale_count=len(window), total_notional=total,
                avg_entry_price=avg_price, signal_time=start_time,
                settle_time=settle_time, resolution=resolution,
                won=won, lockup_minutes=max(lockup, 1),
            ))
            break

    return signals


# ─── Simulation ───

@dataclass
class OpenPosition:
    entry_cost: float
    entry_price: float
    contracts: int
    won: bool
    settle_day: int  # day index when position settles
    event_id: str
    market_id: str


def simulate_abstract(
    daily_signals: list[list[dict]],
    *,
    kill_switch_pct: float = 40.0,
    starting_bankroll: float = STARTING_BANKROLL,
) -> dict:
    """Simulate over a list of days, each containing signals as dicts.

    Each signal dict: {won: bool, entry_price: float, lockup_days: int, event_id: str, market_id: str}
    This abstraction lets us tile days for longer horizons.
    """
    bankroll = starting_bankroll
    peak = bankroll
    consecutive_losses = 0
    skip_next = False
    killed = False

    open_positions: list[OpenPosition] = []
    trades_executed = []
    daily_bankrolls = []
    traded_markets: set[str] = set()

    for day_idx, day_sigs in enumerate(daily_signals):
        day_start = bankroll + sum(p.entry_cost for p in open_positions)
        stopped_for_day = False

        if killed:
            daily_bankrolls.append(bankroll + sum(p.entry_cost for p in open_positions))
            continue

        # Settle positions
        still_open = []
        for pos in open_positions:
            if day_idx >= pos.settle_day:
                if pos.won:
                    payout = pos.contracts * 1.0
                    pnl = payout - pos.entry_cost
                else:
                    stop_price = pos.entry_price * (1 - STOP_LOSS_PCT)
                    exit_fee = kalshi_fee(stop_price, pos.contracts)
                    proceeds = stop_price * pos.contracts - exit_fee
                    pnl = proceeds - pos.entry_cost

                bankroll += pos.entry_cost + pnl
                bankroll = max(bankroll, 0)
                trades_executed.append({"won": pos.won, "pnl": pnl})

                if pos.won:
                    consecutive_losses = 0
                else:
                    consecutive_losses += 1
                    if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                        skip_next = True

                locked_value = sum(p.entry_cost for p in still_open)
                total_equity = bankroll + locked_value
                peak = max(peak, total_equity)
                if kill_switch_pct and peak > 0 and (peak - total_equity) / peak * 100 >= kill_switch_pct:
                    killed = True
            else:
                still_open.append(pos)
        open_positions = still_open

        if killed:
            daily_bankrolls.append(bankroll + sum(p.entry_cost for p in open_positions))
            continue

        for sig in day_sigs:
            if killed or stopped_for_day or bankroll <= 0:
                break

            if skip_next:
                skip_next = False
                continue

            if sig["market_id"] in traded_markets:
                continue

            if len(open_positions) >= MAX_CONCURRENT:
                continue

            open_event_ids = {p.event_id for p in open_positions}
            if sig["event_id"] and sig["event_id"] in open_event_ids:
                continue

            locked = sum(p.entry_cost for p in open_positions)
            available = bankroll - locked
            if available <= 0:
                continue

            open_slots = MAX_CONCURRENT - len(open_positions)
            sizing_balance = available / open_slots if open_slots > 0 else available

            entry_price = min(sig["entry_price"] + SLIPPAGE_CENTS, 0.99)
            if entry_price < PRICE_MIN or entry_price > PRICE_MAX + SLIPPAGE_CENTS:
                continue

            contracts, total_cost = compute_size(entry_price, sizing_balance)
            if contracts <= 0:
                continue

            bankroll -= total_cost

            # Lockup in days (round up)
            lockup_days = max(1, math.ceil(sig["lockup_min"] / (24 * 60)))

            open_positions.append(OpenPosition(
                entry_cost=total_cost, entry_price=entry_price,
                contracts=contracts, won=sig["won"],
                settle_day=day_idx + lockup_days,
                event_id=sig["event_id"], market_id=sig["market_id"],
            ))
            traded_markets.add(sig["market_id"])

            # Daily loss check
            total_eq = bankroll + sum(p.entry_cost for p in open_positions)
            if day_start > 0 and (day_start - total_eq) / day_start * 100 >= DAILY_LOSS_LIMIT_PCT:
                stopped_for_day = True

        daily_bankrolls.append(bankroll + sum(p.entry_cost for p in open_positions))

    # Settle remaining
    for pos in open_positions:
        if pos.won:
            pnl = pos.contracts * 1.0 - pos.entry_cost
        else:
            sp = pos.entry_price * (1 - STOP_LOSS_PCT)
            ef = kalshi_fee(sp, pos.contracts)
            pnl = (sp * pos.contracts - ef) - pos.entry_cost
        bankroll += pos.entry_cost + pnl
        bankroll = max(bankroll, 0)
        trades_executed.append({"won": pos.won, "pnl": pnl})

    final = bankroll
    return {
        "trades": trades_executed,
        "final_bankroll": final,
        "daily_bankrolls": daily_bankrolls,
        "killed": killed,
    }


def signals_to_daily(signals: list[Signal], n_days: int) -> list[list[dict]]:
    """Convert signals to per-day lists sorted by signal_time."""
    signals_sorted = sorted(signals, key=lambda s: s.signal_time)

    # Map each signal to its day index
    if not signals_sorted:
        return [[] for _ in range(n_days)]

    first_day = signals_sorted[0].signal_time.replace(hour=0, minute=0, second=0, microsecond=0)
    daily: list[list[dict]] = [[] for _ in range(n_days)]

    for s in signals_sorted:
        day_idx = (s.signal_time - first_day).days
        if 0 <= day_idx < n_days:
            daily[day_idx].append({
                "won": s.won,
                "entry_price": s.avg_entry_price,
                "lockup_min": s.lockup_minutes,
                "event_id": s.event_id,
                "market_id": s.market_id,
            })

    return daily


def tile_days(daily_signals: list[list[dict]], target_days: int, rng=None) -> list[list[dict]]:
    """Tile daily signals to reach target_days. Each tile gets shuffled and
    market_ids are made unique per tile to avoid cross-tile dedup."""
    n_src = len(daily_signals)
    result = []

    tile = 0
    while len(result) < target_days:
        for day_idx in range(n_src):
            if len(result) >= target_days:
                break
            day = daily_signals[day_idx]
            # Make market/event IDs unique per tile
            new_day = []
            for sig in day:
                new_sig = dict(sig)
                new_sig["market_id"] = f"{sig['market_id']}_t{tile}"
                new_sig["event_id"] = f"{sig['event_id']}_t{tile}" if sig["event_id"] else ""
                new_day.append(new_sig)

            if rng is not None:
                rng.shuffle(new_day)

            result.append(new_day)
        tile += 1

    return result


# ─── Data fetching ───

async def fetch_data(env_path: Path):
    env = dotenv_values(env_path)
    from supabase import create_async_client
    client = await create_async_client(env["SUPABASE_URL"], env["SUPABASE_SECRET_KEY"])

    pids = await client.table("platforms").select("id,slug").execute()
    k_id = [p["id"] for p in pids.data if p["slug"] == "kalshi"][0]

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
    print(f"Fetched {len(all_trades)} whale trades")

    by_market = defaultdict(list)
    for t in all_trades:
        by_market[t["market_id"]].append(t)

    mids = list(by_market.keys())
    print(f"Fetching {len(mids)} markets...")
    markets = {}
    for i in range(0, len(mids), 50):
        resp = await client.table("markets").select(
            "id, external_id, resolution, resolved, event_id"
        ).in_("id", mids[i:i + 50]).execute()
        for m in resp.data:
            markets[m["id"]] = m

    eids = list(set(m.get("event_id") for m in markets.values() if m.get("event_id")))
    print(f"Fetching {len(eids)} events...")
    events = {}
    for i in range(0, len(eids), 50):
        resp = await client.table("events").select("id, category").in_("id", eids[i:i + 50]).execute()
        for e in resp.data:
            events[e["id"]] = e

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


# ─── Main ───

async def main():
    env_path = Path("/Users/kj/Code/Profitlabs/profitlabs-ml-pipeline/.env")
    if len(sys.argv) > 1:
        env_path = Path(sys.argv[1])

    by_market, markets, events, last_trade_times = await fetch_data(env_path)

    # Build expanded series: KNOWN_SERIES + all sports/econ with 20+ trades
    prefix_stats = defaultdict(lambda: {"trades": 0, "categories": set()})
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
        if cat:
            prefix_stats[prefix]["categories"].add(cat)
        prefix_stats[prefix]["trades"] += len(trades)

    expanded_series = set(KNOWN_SERIES)
    for prefix, stats in prefix_stats.items():
        if stats["categories"] & CATEGORIES and stats["trades"] >= 20:
            expanded_series.add(prefix)

    # ─── Find signals for both sets ───
    print("\nFinding signals...")
    sigs_known = find_signals(by_market, markets, events, last_trade_times, series_set=KNOWN_SERIES)
    sigs_expanded = find_signals(by_market, markets, events, last_trade_times, series_set=expanded_series)
    sigs_known.sort(key=lambda s: s.signal_time)
    sigs_expanded.sort(key=lambda s: s.signal_time)

    all_dates = set(s.signal_time.strftime("%Y-%m-%d") for s in sigs_expanded)
    n_days = max(len(all_dates), 1)

    wins_k = sum(1 for s in sigs_known if s.won)
    wins_e = sum(1 for s in sigs_expanded if s.won)

    print(f"\n{'='*80}")
    print(f"  SIGNAL SUMMARY ({n_days} days of data)")
    print(f"{'='*80}")
    print(f"  Known series ({len(KNOWN_SERIES)}):   {len(sigs_known)} signals ({len(sigs_known)/n_days:.1f}/day), {wins_k/len(sigs_known)*100:.1f}% WR")
    print(f"  Expanded ({len(expanded_series)}): {len(sigs_expanded)} signals ({len(sigs_expanded)/n_days:.1f}/day), {wins_e/len(sigs_expanded)*100:.1f}% WR")

    # ─── 35-day backtest: known vs expanded vs kill switch variants ───
    for label, sigs in [("KNOWN_SERIES", sigs_known), ("EXPANDED", sigs_expanded)]:
        daily = signals_to_daily(sigs, n_days)

        print(f"\n{'='*80}")
        print(f"  35-DAY BACKTEST — {label}")
        print(f"{'='*80}")

        for ks_pct in [40.0, 50.0, 60.0, 0.0]:
            ks_label = f"{ks_pct:.0f}%" if ks_pct > 0 else "OFF"

            # Monte Carlo
            mc_finals = []
            mc_trades = []
            mc_killed = 0
            N_SIMS = 500

            for i in range(N_SIMS):
                rng = np.random.default_rng(i)
                shuffled = []
                for d in daily:
                    sd = list(d)
                    rng.shuffle(sd)
                    # Make market IDs unique per sim to avoid cross-sim dedup artifacts
                    sd = [{**s, "market_id": f"{s['market_id']}_s{i}"} for s in sd]
                    sd = [{**s, "event_id": f"{s['event_id']}_s{i}" if s["event_id"] else ""} for s in sd]
                    shuffled.append(sd)

                r = simulate_abstract(shuffled, kill_switch_pct=ks_pct)
                mc_finals.append(r["final_bankroll"])
                mc_trades.append(len(r["trades"]))
                if r["killed"]:
                    mc_killed += 1

            finals = np.array(mc_finals)
            print(f"\n  Kill switch: {ks_label}")
            print(f"    Median: ${np.median(finals):,.2f} | Mean: ${np.mean(finals):,.2f}")
            print(f"    p5: ${np.percentile(finals, 5):,.2f} | p25: ${np.percentile(finals, 25):,.2f} | p75: ${np.percentile(finals, 75):,.2f} | p95: ${np.percentile(finals, 95):,.2f}")
            print(f"    Trades: {np.mean(mc_trades):.0f} avg | Killed: {mc_killed}/{N_SIMS} ({mc_killed/N_SIMS*100:.1f}%)")
            print(f"    Ruin (<$1): {(finals <= 1).mean()*100:.1f}%")

    # ─── 6-month projection (tile signals) ───
    TARGET_DAYS = 180

    print(f"\n{'='*80}")
    print(f"  6-MONTH PROJECTION ({TARGET_DAYS} days, tiled from {n_days} days)")
    print(f"{'='*80}")

    for label, sigs in [("KNOWN_SERIES", sigs_known), ("EXPANDED", sigs_expanded)]:
        daily = signals_to_daily(sigs, n_days)

        print(f"\n  --- {label} ---")

        for ks_pct in [40.0, 60.0, 0.0]:
            ks_label = f"{ks_pct:.0f}%" if ks_pct > 0 else "OFF"

            mc_finals = []
            mc_killed = 0
            mc_trades = []
            mc_monthly = [[] for _ in range(6)]
            N_SIMS = 500

            for i in range(N_SIMS):
                rng = np.random.default_rng(i + 1000)
                tiled = tile_days(daily, TARGET_DAYS, rng=rng)
                r = simulate_abstract(tiled, kill_switch_pct=ks_pct)
                mc_finals.append(r["final_bankroll"])
                mc_trades.append(len(r["trades"]))
                if r["killed"]:
                    mc_killed += 1

                # Monthly snapshots
                for m in range(6):
                    day_idx = min((m + 1) * 30 - 1, len(r["daily_bankrolls"]) - 1)
                    if day_idx >= 0 and day_idx < len(r["daily_bankrolls"]):
                        mc_monthly[m].append(r["daily_bankrolls"][day_idx])

            finals = np.array(mc_finals)
            print(f"\n  Kill switch: {ks_label}")
            print(f"    Final (6mo): median ${np.median(finals):,.2f} | p5 ${np.percentile(finals, 5):,.2f} | p95 ${np.percentile(finals, 95):,.2f}")
            print(f"    Trades: {np.mean(mc_trades):.0f} avg | Killed: {mc_killed}/{N_SIMS} ({mc_killed/N_SIMS*100:.1f}%)")

            # Monthly curve
            print(f"    Monthly median curve:")
            for m in range(6):
                if mc_monthly[m]:
                    arr = np.array(mc_monthly[m])
                    print(f"      Month {m+1}: ${np.median(arr):>10,.2f} (p5=${np.percentile(arr,5):>10,.2f}, p95=${np.percentile(arr,95):>10,.2f})")

            # Time to milestones
            milestones = [1_000, 10_000, 100_000]
            for target in milestones:
                # What fraction of sims reach this milestone, and by when?
                reached = 0
                first_day = []
                for i in range(N_SIMS):
                    rng = np.random.default_rng(i + 1000)
                    tiled = tile_days(daily, TARGET_DAYS, rng=rng)
                    r = simulate_abstract(tiled, kill_switch_pct=ks_pct)
                    for d_idx, bal in enumerate(r["daily_bankrolls"]):
                        if bal >= target:
                            reached += 1
                            first_day.append(d_idx)
                            break
                if reached > 0:
                    pct = reached / N_SIMS * 100
                    med_days = int(np.median(first_day))
                    print(f"    ${target:>7,}: {pct:.0f}% reach it, median {med_days} days")
                else:
                    print(f"    ${target:>7,}: 0% reach in {TARGET_DAYS} days")

    print(f"\n{'='*80}")


if __name__ == "__main__":
    asyncio.run(main())
