"""12-month projection — known series, no kill switch, $100 start."""

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


WHALE_THRESHOLD = 1000
MIN_WHALE_COUNT = 3
CONSENSUS_PCT = 90.0
WINDOW_MINUTES = 30
PRICE_MIN = 0.85
PRICE_MAX = 0.95
STOP_LOSS_PCT = 0.15
SLIPPAGE_CENTS = 0.01
MAX_CONCURRENT = 2
SETTLE_BUFFER_MIN = 5
FEE_COEFF = 0.07
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

PHASES = [(50_000, 0.10), (5_000, 0.20), (1_000, 0.30), (500, 0.50), (0, 1.00)]
MAX_CONSECUTIVE_LOSSES = 3
DAILY_LOSS_LIMIT_PCT = 20.0


def kalshi_fee(price, contracts):
    return math.ceil(FEE_COEFF * contracts * price * (1 - price) * 100) / 100

def parse_event_date(ticker):
    m = re.search(r'(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})', ticker, re.IGNORECASE)
    if not m: return None
    y = 2000 + int(m.group(1))
    mo = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}[m.group(2).upper()]
    return f"{y}-{mo:02d}-{int(m.group(3)):02d}"

def get_alloc_pct(balance):
    for threshold, pct in PHASES:
        if balance >= threshold: return pct
    return 1.0

def compute_size(price, sizing_balance):
    if price <= 0 or price >= 1.0 or sizing_balance <= 0: return 0, 0.0
    alloc_pct = get_alloc_pct(sizing_balance)
    dollar_amount = sizing_balance * alloc_pct
    cost_per = price + kalshi_fee(price, 1)
    if cost_per <= 0: return 0, 0.0
    size = int(dollar_amount / cost_per)
    while size > 0:
        fee = kalshi_fee(price, size)
        total_cost = price * size + fee
        if total_cost <= sizing_balance: return size, total_cost
        size -= 1
    return 0, 0.0


@dataclass
class Signal:
    market_id: str; ticker: str; event_id: str; event_date: str; category: str
    consensus_side: str; consensus_pct: float; whale_count: int
    total_notional: float; avg_entry_price: float
    signal_time: datetime; settle_time: datetime
    resolution: str; won: bool; lockup_minutes: float


def find_signals(by_market, markets, events, last_trade_times, *, series_set):
    signals = []
    for mid, trades in by_market.items():
        m = markets.get(mid)
        if not m or not m.get("resolved") or not m.get("resolution"): continue
        e = events.get(m.get("event_id", ""), {})
        cat = (e.get("category") or "").lower()
        if cat not in CATEGORIES: continue
        ticker = m.get("external_id", "")
        event_date = parse_event_date(ticker)
        if not event_date: continue
        if not any(ticker.startswith(s) for s in series_set): continue
        resolution = m["resolution"].lower()
        settle_time = last_trade_times.get(mid)
        if not settle_time: continue
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
                else: break
            if len(window) < MIN_WHALE_COUNT: continue
            yes_v = sum(float(t["notional"]) for t in window if t["outcome"].lower() == "yes")
            no_v = sum(float(t["notional"]) for t in window if t["outcome"].lower() == "no")
            total = yes_v + no_v
            if total == 0: continue
            cons_side = "yes" if yes_v >= no_v else "no"
            cons_pct = max(yes_v, no_v) / total * 100
            if cons_pct < CONSENSUS_PCT: continue
            consensus_trades = [t for t in window if t["outcome"].lower() == cons_side and PRICE_MIN <= float(t["price"]) <= PRICE_MAX]
            if not consensus_trades: continue
            avg_price = sum(float(t["price"]) for t in consensus_trades) / len(consensus_trades)
            trade_date = start_time.strftime("%Y-%m-%d")
            evt_dt = datetime.strptime(event_date, "%Y-%m-%d")
            trd_dt = datetime.strptime(trade_date, "%Y-%m-%d")
            if not (trd_dt == evt_dt or trd_dt == evt_dt - timedelta(days=1)): continue
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


@dataclass
class Pos:
    cost: float; price: float; contracts: int; won: bool
    settle_day: int; event_id: str; market_id: str


def simulate(daily_signals, *, starting_bankroll=100.0):
    bankroll = starting_bankroll
    open_pos: list[Pos] = []
    traded = set()
    consec_losses = 0
    skip_next = False
    trades = []
    daily_bal = []

    for day_idx, day_sigs in enumerate(daily_signals):
        day_start_eq = bankroll + sum(p.cost for p in open_pos)
        stopped = False

        # Settle
        still = []
        for p in open_pos:
            if day_idx >= p.settle_day:
                if p.won:
                    pnl = p.contracts * 1.0 - p.cost
                else:
                    sp = p.price * (1 - STOP_LOSS_PCT)
                    ef = kalshi_fee(sp, p.contracts)
                    pnl = (sp * p.contracts - ef) - p.cost
                bankroll += p.cost + pnl
                bankroll = max(bankroll, 0)
                trades.append({"won": p.won, "pnl": pnl})
                if p.won: consec_losses = 0
                else:
                    consec_losses += 1
                    if consec_losses >= MAX_CONSECUTIVE_LOSSES: skip_next = True
            else:
                still.append(p)
        open_pos = still

        for sig in day_sigs:
            if stopped or bankroll <= 0: break
            if skip_next: skip_next = False; continue
            if sig["mid"] in traded: continue
            if len(open_pos) >= MAX_CONCURRENT: continue
            open_eids = {p.event_id for p in open_pos}
            if sig["eid"] and sig["eid"] in open_eids: continue
            locked = sum(p.cost for p in open_pos)
            avail = bankroll - locked
            if avail <= 0: continue
            slots = MAX_CONCURRENT - len(open_pos)
            sb = avail / slots if slots > 0 else avail
            ep = min(sig["ep"] + SLIPPAGE_CENTS, 0.99)
            if ep < PRICE_MIN or ep > PRICE_MAX + SLIPPAGE_CENTS: continue
            c, tc = compute_size(ep, sb)
            if c <= 0: continue
            bankroll -= tc
            ld = max(1, math.ceil(sig["lockup"] / (24 * 60)))
            open_pos.append(Pos(cost=tc, price=ep, contracts=c, won=sig["won"],
                                settle_day=day_idx + ld, event_id=sig["eid"], market_id=sig["mid"]))
            traded.add(sig["mid"])
            eq = bankroll + sum(p.cost for p in open_pos)
            if day_start_eq > 0 and (day_start_eq - eq) / day_start_eq * 100 >= DAILY_LOSS_LIMIT_PCT:
                stopped = True

        daily_bal.append(bankroll + sum(p.cost for p in open_pos))

    # Settle remaining
    for p in open_pos:
        if p.won: pnl = p.contracts * 1.0 - p.cost
        else:
            sp = p.price * (1 - STOP_LOSS_PCT)
            ef = kalshi_fee(sp, p.contracts)
            pnl = (sp * p.contracts - ef) - p.cost
        bankroll += p.cost + pnl
        bankroll = max(bankroll, 0)
        trades.append({"won": p.won, "pnl": pnl})

    return {"final": bankroll, "trades": trades, "daily": daily_bal}


async def main():
    env_path = Path("/Users/kj/Code/Profitlabs/profitlabs-ml-pipeline/.env")
    env = dotenv_values(env_path)
    from supabase import create_async_client
    client = await create_async_client(env["SUPABASE_URL"], env["SUPABASE_SECRET_KEY"])
    pids = await client.table("platforms").select("id,slug").execute()
    k_id = [p["id"] for p in pids.data if p["slug"] == "kalshi"][0]

    print("Fetching trades...")
    all_trades = []
    offset = 0
    while True:
        resp = await client.table("trades").select("market_id,price,notional,outcome,traded_at"
        ).eq("platform_id", k_id).gte("notional", WHALE_THRESHOLD).gte("price", PRICE_MIN
        ).lte("price", PRICE_MAX).order("traded_at", desc=True).range(offset, offset+999).execute()
        if not resp.data: break
        all_trades.extend(resp.data)
        offset += 1000
        if len(all_trades) >= 500000: break
        if len(all_trades) % 10000 < 1000: print(f"  {len(all_trades)}...", flush=True)
    print(f"  {len(all_trades)} trades")

    by_market = defaultdict(list)
    for t in all_trades: by_market[t["market_id"]].append(t)
    mids = list(by_market.keys())

    print("Fetching markets...")
    markets = {}
    for i in range(0, len(mids), 50):
        resp = await client.table("markets").select("id,external_id,resolution,resolved,event_id").in_("id", mids[i:i+50]).execute()
        for m in resp.data: markets[m["id"]] = m

    eids = list(set(m.get("event_id") for m in markets.values() if m.get("event_id")))
    print("Fetching events...")
    events = {}
    for i in range(0, len(eids), 50):
        resp = await client.table("events").select("id,category").in_("id", eids[i:i+50]).execute()
        for e in resp.data: events[e["id"]] = e

    print("Fetching settlement times...")
    all_whale = []
    offset = 0
    while True:
        resp = await client.table("trades").select("market_id,traded_at"
        ).eq("platform_id", k_id).gte("notional", WHALE_THRESHOLD).order("traded_at", desc=True
        ).range(offset, offset+999).execute()
        if not resp.data: break
        all_whale.extend(resp.data)
        offset += 1000
        if len(all_whale) >= 700000: break
        if len(all_whale) % 100000 < 1000: print(f"  {len(all_whale)}...", flush=True)
    print(f"  {len(all_whale)} trades")

    last_trade_times = {}
    for t in all_whale:
        mid = t["market_id"]
        ts = isoparse(str(t["traded_at"]))
        if mid not in last_trade_times or ts > last_trade_times[mid]:
            last_trade_times[mid] = ts
    await client.auth.sign_out()

    print("Finding signals...")
    sigs = find_signals(by_market, markets, events, last_trade_times, series_set=KNOWN_SERIES)
    sigs.sort(key=lambda s: s.signal_time)
    all_dates = sorted(set(s.signal_time.strftime("%Y-%m-%d") for s in sigs))
    n_days = len(all_dates)
    wins = sum(1 for s in sigs if s.won)
    print(f"  {len(sigs)} signals, {n_days} days, {wins/len(sigs)*100:.1f}% WR")

    # Build daily signal lists
    first_day = sigs[0].signal_time.replace(hour=0, minute=0, second=0, microsecond=0)
    daily_src: list[list[dict]] = [[] for _ in range(n_days)]
    for s in sigs:
        d = (s.signal_time - first_day).days
        if 0 <= d < n_days:
            daily_src[d].append({
                "won": s.won, "ep": s.avg_entry_price,
                "lockup": s.lockup_minutes,
                "eid": s.event_id, "mid": s.market_id,
            })

    # Run projections
    N_SIMS = 500
    for target_days, label in [(365, "12 MONTHS")]:
        print(f"\n{'='*80}")
        print(f"  {label} PROJECTION ({target_days} days, tiled from {n_days})")
        print(f"  No kill switch | $100 start | {N_SIMS} Monte Carlo sims")
        print(f"{'='*80}")

        mc_finals = []
        mc_monthly = [[] for _ in range(12)]
        mc_trades = []

        for i in range(N_SIMS):
            rng = np.random.default_rng(i)

            # Tile days with unique IDs per tile
            tiled = []
            tile = 0
            while len(tiled) < target_days:
                for d in range(n_days):
                    if len(tiled) >= target_days: break
                    day = [{**s, "mid": f"{s['mid']}_t{tile}", "eid": f"{s['eid']}_t{tile}" if s["eid"] else ""} for s in daily_src[d]]
                    rng.shuffle(day)
                    tiled.append(day)
                tile += 1

            r = simulate(tiled)
            mc_finals.append(r["final"])
            mc_trades.append(len(r["trades"]))

            for m in range(12):
                d_idx = min((m + 1) * 30 - 1, len(r["daily"]) - 1)
                if 0 <= d_idx < len(r["daily"]):
                    mc_monthly[m].append(r["daily"][d_idx])

        finals = np.array(mc_finals)
        print(f"\n  Final bankroll:")
        print(f"    Median: ${np.median(finals):>15,.2f}")
        print(f"    Mean:   ${np.mean(finals):>15,.2f}")
        print(f"    p5:     ${np.percentile(finals, 5):>15,.2f}")
        print(f"    p25:    ${np.percentile(finals, 25):>15,.2f}")
        print(f"    p75:    ${np.percentile(finals, 75):>15,.2f}")
        print(f"    p95:    ${np.percentile(finals, 95):>15,.2f}")
        print(f"    Avg trades: {np.mean(mc_trades):.0f}")

        print(f"\n  Monthly compounding curve (median / p5 / p95):")
        for m in range(12):
            if mc_monthly[m]:
                arr = np.array(mc_monthly[m])
                med = np.median(arr)
                p5 = np.percentile(arr, 5)
                p95 = np.percentile(arr, 95)
                print(f"    Month {m+1:>2}: ${med:>15,.2f}  (p5=${p5:>12,.2f}  p95=${p95:>12,.2f})")

        # Milestones
        milestones = [1_000, 10_000, 50_000, 100_000, 500_000, 1_000_000]
        print(f"\n  Milestones:")
        for target in milestones:
            reached = 0
            first_days = []
            for i in range(N_SIMS):
                rng = np.random.default_rng(i)
                tiled = []
                tile = 0
                while len(tiled) < target_days:
                    for d in range(n_days):
                        if len(tiled) >= target_days: break
                        day = [{**s, "mid": f"{s['mid']}_t{tile}", "eid": f"{s['eid']}_t{tile}" if s["eid"] else ""} for s in daily_src[d]]
                        rng.shuffle(day)
                        tiled.append(day)
                    tile += 1
                r = simulate(tiled)
                for d_idx, bal in enumerate(r["daily"]):
                    if bal >= target:
                        reached += 1
                        first_days.append(d_idx)
                        break
            pct = reached / N_SIMS * 100
            if reached > 0:
                med_d = int(np.median(first_days))
                p25_d = int(np.percentile(first_days, 25))
                p75_d = int(np.percentile(first_days, 75))
                print(f"    ${target:>10,}: {pct:>5.1f}% reach | median {med_d:>3}d (p25={p25_d}d, p75={p75_d}d)")
            else:
                print(f"    ${target:>10,}: {pct:>5.1f}% reach in {target_days} days")

    print(f"\n{'='*80}")


if __name__ == "__main__":
    asyncio.run(main())
