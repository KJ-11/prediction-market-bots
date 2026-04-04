"""E2E backtest v4 — matches live bot spec exactly.

Fixes from v3/analysis:
1. Sizing: balance / open_slots first, then phase alloc (matches live bot main.py:156-158)
2. Event-level dedup: only one position per event (matches live bot main.py:97-122)
3. All losses are stop-loss exits (never hold to resolution and lose)
4. Forward-looking window (correct — catches fresh bursts)
5. Markets NOT locked on failed evaluation (correct — later bursts can qualify)
6. Series list from live bot KNOWN_SERIES as authority + expansion scan

Data: ProfitLabs Supabase. Notional = price * size (confirmed signal.py:205).
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


# --- Config (matches live bot) ---
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

# Authority list from live bot (bots/kalshi_whale/discovery.py series list)
KNOWN_SERIES = {
    # Sports — game outcomes
    "KXMLBGAME", "KXNBAGAME", "KXNHLGAME", "KXIPLGAME", "KXUFCFIGHT",
    # Sports — match outcomes
    "KXATPMATCH", "KXATPCHALLENGERMATCH", "KXWTAMATCH",
    # Sports — player/game props
    "KXMLBHR", "KXMLB1HTOTAL", "KXMLBHIT", "KXMLBSTRIKEOUT",
    "KXNBA1HTOTAL", "KXNBAPLAYER", "KXNBAPLAYERPTS",
    "KXNBAAST", "KXNBA2D", "KXNBAREB",
    "KXNHL1HTOTAL", "KXNHLGOAL", "KXNHLPTS",
    "KXMLBHRR",
    # Sports — other
    "KXT20MATCH",
    # Economics — daily settlement
    "KXWTI", "KXINXU", "KXINXD", "KXGOLD", "KXSILVER", "KXNATGAS",
}

# Phase-based sizing (matches live bot sizing.py)
PHASES = [
    (50_000, 0.10),
    (5_000, 0.20),
    (1_000, 0.30),
    (500, 0.50),
    (0, 1.00),
]

MAX_CONSECUTIVE_LOSSES = 3
DAILY_LOSS_LIMIT_PCT = 20.0
KILL_SWITCH_PCT = 40.0
FEE_COEFF = 0.07


def kalshi_fee(price: float, contracts: int) -> float:
    """Kalshi taker fee — ceil(0.07 * C * P * (1-P)) to next cent."""
    raw = FEE_COEFF * contracts * price * (1 - price)
    return math.ceil(raw * 100) / 100


def parse_event_date(ticker: str):
    """Extract event date from ticker (YYMMMDD format)."""
    m = re.search(r'(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})', ticker, re.IGNORECASE)
    if not m:
        return None
    y = 2000 + int(m.group(1))
    months = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
    mo = months[m.group(2).upper()]
    return f"{y}-{mo:02d}-{int(m.group(3)):02d}"


def extract_series_prefix(ticker: str) -> str | None:
    """Extract series prefix: KXMLBGAME-26APR03-NYY -> KXMLBGAME"""
    m = re.match(r'^([A-Z0-9]+?)-\d{2}[A-Z]{3}\d{2}', ticker)
    if m:
        return m.group(1)
    parts = ticker.split('-')
    if len(parts) >= 2:
        return parts[0]
    return None


def derive_event_key(ticker: str) -> str | None:
    """Derive event grouping key from ticker.

    Markets in the same event share series+date+game identifier.
    E.g. KXMLBGAME-26APR03-NYY and KXMLBGAME-26APR03-BOS are the same event.

    For game outcomes: series + date portion = event key
    E.g. KXMLBGAME-26APR03 (without the team suffix)

    For props: series + date + game = event key
    E.g. KXMLBHR-26APR03-NYY-JUDGE (same event as KXMLBHR-26APR03-NYY-SOTO)
    """
    # Use event_id from DB instead (more reliable). This is a fallback.
    # For now, we'll use the DB event_id field.
    return None


def get_alloc_pct(bankroll: float) -> float:
    """Phase-based allocation percentage (matches live bot sizing.py)."""
    for threshold, pct in PHASES:
        if bankroll >= threshold:
            return pct
    return 1.0


def compute_size(price: float, sizing_balance: float) -> tuple[int, float]:
    """Compute contracts and total cost. Matches live bot sizing.py exactly.

    Args:
        price: Entry price
        sizing_balance: Balance AFTER dividing by open slots

    Returns:
        (contracts, total_cost)
    """
    if price <= 0 or price >= 1.0 or sizing_balance <= 0:
        return 0, 0.0

    alloc_pct = get_alloc_pct(sizing_balance)
    dollar_amount = sizing_balance * alloc_pct

    cost_per = price + kalshi_fee(price, 1)
    if cost_per <= 0:
        return 0, 0.0

    size = int(dollar_amount / cost_per)

    # Verify total cost doesn't exceed sizing_balance
    while size > 0:
        fee = kalshi_fee(price, size)
        total_cost = price * size + fee
        if total_cost <= sizing_balance:
            return size, total_cost
        size -= 1

    return 0, 0.0


# ─────────────────────────────────────────────────────────────────────
# Signal detection
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    market_id: str
    ticker: str
    event_id: str  # for event-level dedup
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
    min_whale_count: int = MIN_WHALE_COUNT,
    consensus_pct: float = CONSENSUS_PCT,
    window_minutes: int = WINDOW_MINUTES,
) -> list[Signal]:
    """Find whale signals with forward-looking window. One signal per market."""
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

            # Price check: consensus-side trades in price range
            consensus_trades = [
                t for t in window
                if t["outcome"].lower() == cons_side
                and PRICE_MIN <= float(t["price"]) <= PRICE_MAX
            ]
            if not consensus_trades:
                continue

            avg_price = sum(float(t["price"]) for t in consensus_trades) / len(consensus_trades)

            # Date filter: trade must be on event day or day before
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
            break  # one signal per market

    return signals


# ─────────────────────────────────────────────────────────────────────
# Simulation
# ─────────────────────────────────────────────────────────────────────

@dataclass
class OpenPosition:
    entry_cost: float
    entry_price: float
    contracts: int
    signal: Signal
    settle_time: datetime
    market_id: str
    event_id: str


def simulate(signals: list[Signal], shuffle_seed=None) -> dict:
    """Simulate with correct sizing, event dedup, stop-loss-only exits."""
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
    traded_markets: set[str] = set()  # one signal per market ever
    trades_executed = []
    daily_bankrolls = {}
    signals_seen = 0
    signals_skipped_concurrent = 0
    signals_skipped_event = 0
    signals_skipped_size = 0

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
                    # Win: $1 per contract, no settlement fee
                    payout = pos.contracts * 1.0
                    pnl = payout - pos.entry_cost
                else:
                    # Loss: stop loss exit (all losses are stops per spec)
                    stop_price = pos.entry_price * (1 - STOP_LOSS_PCT)
                    exit_fee = kalshi_fee(stop_price, pos.contracts)
                    proceeds = stop_price * pos.contracts - exit_fee
                    pnl = proceeds - pos.entry_cost

                bankroll += pos.entry_cost + pnl  # return capital + pnl
                bankroll = max(bankroll, 0)
                trades_executed.append({
                    "won": pos.signal.won, "pnl": pnl,
                    "entry_price": pos.entry_price,
                    "contracts": pos.contracts, "entry_cost": pos.entry_cost,
                    "lockup_min": pos.signal.lockup_minutes,
                    "market_id": pos.market_id,
                    "category": pos.signal.category,
                    "ticker": pos.signal.ticker,
                })

                if pos.signal.won:
                    consecutive_losses = 0
                else:
                    consecutive_losses += 1
                    if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                        skip_next = True

                # Kill switch: check total equity (cash + locked capital)
                # not just cash, to avoid false triggers from capital being
                # locked in open positions
                locked_value = sum(p.entry_cost for p in still_open)
                total_equity = bankroll + locked_value
                peak = max(peak, total_equity)
                if peak > 0 and (peak - total_equity) / peak * 100 >= KILL_SWITCH_PCT:
                    killed = True
            else:
                still_open.append(pos)
        open_positions = still_open

        if killed or stopped_for_day or bankroll <= 0:
            continue

        signals_seen += 1

        if skip_next:
            skip_next = False
            continue

        # One signal per market
        if sig.market_id in traded_markets:
            continue

        # Max concurrent
        if len(open_positions) >= MAX_CONCURRENT:
            signals_skipped_concurrent += 1
            continue

        # Event-level dedup: no two positions on same event
        open_event_ids = {p.event_id for p in open_positions}
        if sig.event_id and sig.event_id in open_event_ids:
            signals_skipped_event += 1
            continue

        # --- Sizing (matches live bot exactly) ---
        # Step 1: available balance = bankroll minus locked capital
        locked = sum(p.entry_cost for p in open_positions)
        available = bankroll - locked
        if available <= 0:
            continue

        # Step 2: divide by open slots (live bot main.py:156-158)
        open_slots = MAX_CONCURRENT - len(open_positions)
        sizing_balance = available / open_slots if open_slots > 0 else available

        # Step 3: entry price (use avg whale price + slippage as proxy for ask)
        entry_price = min(sig.avg_entry_price + SLIPPAGE_CENTS, 0.99)
        if entry_price < PRICE_MIN or entry_price > PRICE_MAX + SLIPPAGE_CENTS:
            continue

        # Step 4: compute size
        contracts, total_cost = compute_size(entry_price, sizing_balance)
        if contracts <= 0:
            signals_skipped_size += 1
            continue

        # Deduct from bankroll
        bankroll -= total_cost

        open_positions.append(OpenPosition(
            entry_cost=total_cost, entry_price=entry_price,
            contracts=contracts, signal=sig, settle_time=sig.settle_time,
            market_id=sig.market_id, event_id=sig.event_id,
        ))
        traded_markets.add(sig.market_id)

        # Daily loss check
        if day_start_bankroll > 0:
            day_loss_pct = (day_start_bankroll - bankroll) / day_start_bankroll * 100
            if day_loss_pct >= DAILY_LOSS_LIMIT_PCT:
                stopped_for_day = True

    # Settle remaining
    for pos in open_positions:
        if pos.signal.won:
            payout = pos.contracts * 1.0
            pnl = payout - pos.entry_cost
        else:
            stop_price = pos.entry_price * (1 - STOP_LOSS_PCT)
            exit_fee = kalshi_fee(stop_price, pos.contracts)
            proceeds = stop_price * pos.contracts - exit_fee
            pnl = proceeds - pos.entry_cost

        bankroll += pos.entry_cost + pnl
        bankroll = max(bankroll, 0)
        trades_executed.append({
            "won": pos.signal.won, "pnl": pnl,
            "entry_price": pos.entry_price,
            "contracts": pos.contracts, "entry_cost": pos.entry_cost,
            "lockup_min": pos.signal.lockup_minutes,
            "market_id": pos.market_id,
            "category": pos.signal.category,
            "ticker": pos.signal.ticker,
        })

    if current_day:
        daily_bankrolls[current_day] = bankroll

    return {
        "trades": trades_executed,
        "final_bankroll": bankroll,
        "daily_bankrolls": daily_bankrolls,
        "killed": killed,
        "signals_seen": signals_seen,
        "signals_skipped_concurrent": signals_skipped_concurrent,
        "signals_skipped_event": signals_skipped_event,
        "signals_skipped_size": signals_skipped_size,
    }


# ─────────────────────────────────────────────────────────────────────
# Series scan
# ─────────────────────────────────────────────────────────────────────

def scan_series(by_market, markets, events):
    """Find all series prefixes with whale activity, compare to KNOWN_SERIES."""
    prefix_stats = defaultdict(lambda: {
        "markets": set(), "trades": 0, "notional": 0.0,
        "categories": set(), "wins": 0, "resolved": 0,
    })

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

        # Track resolution for WR by series
        if m.get("resolved") and m.get("resolution"):
            stats["resolved"] += 1

    # Sort by trade count descending
    sorted_prefixes = sorted(prefix_stats.items(), key=lambda x: -x[1]["trades"])

    print()
    print("=" * 90)
    print("  SERIES PREFIX SCAN")
    print("=" * 90)
    print()
    print(f"  {'Prefix':<30} {'Mkts':>6} {'Trades':>7} {'Notional':>12} {'Known':>6}  Cat")
    print(f"  {'─'*30} {'─'*6} {'─'*7} {'─'*12} {'─'*6}  {'─'*15}")

    # Only show sports/economics series with meaningful activity
    missing_high_volume = []
    for prefix, stats in sorted_prefixes:
        cats = stats["categories"]
        if not (cats & CATEGORIES):
            continue
        if stats["trades"] < 5:
            continue
        known = "✓" if prefix in KNOWN_SERIES else "✗"
        cat_str = ", ".join(sorted(cats))
        print(f"  {prefix:<30} {len(stats['markets']):>6} {stats['trades']:>7} ${stats['notional']:>11,.0f} {known:>6}  {cat_str}")
        if prefix not in KNOWN_SERIES and stats["trades"] >= 20:
            missing_high_volume.append((prefix, stats))

    if missing_high_volume:
        print()
        print(f"  HIGH-VOLUME MISSING SERIES ({len(missing_high_volume)} with 20+ whale trades):")
        for p, s in missing_high_volume:
            print(f"    {p:<30} {s['trades']:>5} trades, {len(s['markets']):>5} markets")

    return missing_high_volume


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

async def main():
    env_path = Path("/Users/kj/Code/Profitlabs/profitlabs-ml-pipeline/.env")
    if len(sys.argv) > 1:
        env_path = Path(sys.argv[1])

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

    # ─── Series scan ───
    missing_series = scan_series(by_market, markets, events)

    # ─── Find signals with KNOWN_SERIES ───
    print("\n" + "=" * 90)
    print("  SIGNAL ANALYSIS (KNOWN_SERIES only)")
    print("=" * 90)

    signals = find_signals(
        by_market, markets, events, last_trade_times,
        series_set=KNOWN_SERIES,
    )
    signals.sort(key=lambda s: s.signal_time)

    if not signals:
        print("  No signals found.")
        return

    # Count days
    all_dates = set(s.signal_time.strftime("%Y-%m-%d") for s in signals)
    n_days = max(len(all_dates), 1)
    date_range = f"{signals[0].signal_time.strftime('%Y-%m-%d')} to {signals[-1].signal_time.strftime('%Y-%m-%d')}"

    wins = sum(1 for s in signals if s.won)
    wr = wins / len(signals) * 100

    print(f"\n  Date range: {date_range} ({n_days} days)")
    print(f"  Total signals: {len(signals)} ({len(signals)/n_days:.1f}/day)")
    print(f"  Win rate: {wr:.1f}% ({wins}/{len(signals)})")

    # Lockup stats
    lockups = sorted(s.lockup_minutes for s in signals)
    print(f"\n  Lockup: median={lockups[len(lockups)//2]:.0f}min, "
          f"p25={lockups[len(lockups)//4]:.0f}min, "
          f"p75={lockups[3*len(lockups)//4]:.0f}min")

    # Category breakdown
    cat_stats = defaultdict(lambda: {"total": 0, "wins": 0})
    for s in signals:
        cat_stats[s.category]["total"] += 1
        if s.won:
            cat_stats[s.category]["wins"] += 1

    print(f"\n  By category:")
    for cat in sorted(cat_stats.keys()):
        cs = cat_stats[cat]
        print(f"    {cat:<12} {cs['total']:>5} signals, {cs['wins']/cs['total']*100:.1f}% WR")

    # Series breakdown
    series_stats = defaultdict(lambda: {"total": 0, "wins": 0})
    for s in signals:
        prefix = extract_series_prefix(s.ticker) or "unknown"
        series_stats[prefix]["total"] += 1
        if s.won:
            series_stats[prefix]["wins"] += 1

    print(f"\n  By series (top 20):")
    for prefix, ss in sorted(series_stats.items(), key=lambda x: -x[1]["total"])[:20]:
        print(f"    {prefix:<30} {ss['total']:>5} signals, {ss['wins']/ss['total']*100:.1f}% WR")

    # ─── Deterministic simulation ───
    print("\n" + "=" * 90)
    print("  DETERMINISTIC P&L (chronological order)")
    print("=" * 90)

    result = simulate(signals)
    trades = result["trades"]
    final = result["final_bankroll"]

    if not trades:
        print("  No trades executed.")
        return

    t_wins = sum(1 for t in trades if t["won"])
    t_losses = len(trades) - t_wins
    total_pnl = sum(t["pnl"] for t in trades)
    avg_lockup = sum(t["lockup_min"] for t in trades) / len(trades)
    win_pnls = [t["pnl"] for t in trades if t["won"]]
    loss_pnls = [t["pnl"] for t in trades if not t["won"]]

    print(f"\n  Trades: {len(trades)} ({len(trades)/n_days:.1f}/day)")
    print(f"  Win/Loss: {t_wins}/{t_losses} ({t_wins/len(trades)*100:.1f}% WR)")
    print(f"  Starting: ${STARTING_BANKROLL:.2f}")
    print(f"  Final: ${final:,.2f}")
    print(f"  Return: {(final/STARTING_BANKROLL - 1)*100:.1f}%")
    print(f"  Total P&L: ${total_pnl:,.2f}")
    if win_pnls:
        print(f"  Avg win: ${sum(win_pnls)/len(win_pnls):.4f}")
    if loss_pnls:
        print(f"  Avg loss: ${sum(loss_pnls)/len(loss_pnls):.4f}")
    print(f"  Avg lockup: {avg_lockup:.0f} min")
    print(f"  Killed: {result['killed']}")
    print(f"\n  Signal flow:")
    print(f"    Signals seen: {result['signals_seen']}")
    print(f"    Skipped (concurrent): {result['signals_skipped_concurrent']}")
    print(f"    Skipped (event dedup): {result['signals_skipped_event']}")
    print(f"    Skipped (size=0): {result['signals_skipped_size']}")

    # Category breakdown of executed trades
    trade_cats = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        tc = trade_cats[t["category"]]
        tc["total"] += 1
        if t["won"]:
            tc["wins"] += 1
        tc["pnl"] += t["pnl"]

    print(f"\n  Executed trades by category:")
    for cat in sorted(trade_cats.keys()):
        tc = trade_cats[cat]
        print(f"    {cat:<12} {tc['total']:>3} trades, {tc['wins']/tc['total']*100:.1f}% WR, ${tc['pnl']:.2f} P&L")

    # Daily bankroll
    print(f"\n  Daily bankroll:")
    for day, bal in sorted(result["daily_bankrolls"].items()):
        print(f"    {day}: ${bal:,.2f}")

    # ─── Monte Carlo ───
    print("\n" + "=" * 90)
    N_SIMS = 1000
    print(f"  MONTE CARLO ({N_SIMS} sims, shuffled within each day)")
    print("=" * 90)

    mc_finals = []
    mc_trades = []
    for i in range(N_SIMS):
        r = simulate(signals, shuffle_seed=i)
        mc_finals.append(r["final_bankroll"])
        mc_trades.append(len(r["trades"]))

    finals = np.array(mc_finals)
    print(f"\n  Final bankroll:")
    print(f"    Median: ${np.median(finals):,.2f}")
    print(f"    Mean: ${np.mean(finals):,.2f}")
    print(f"    p5: ${np.percentile(finals, 5):,.2f}")
    print(f"    p25: ${np.percentile(finals, 25):,.2f}")
    print(f"    p75: ${np.percentile(finals, 75):,.2f}")
    print(f"    p95: ${np.percentile(finals, 95):,.2f}")
    print(f"    Ruin (<$1): {(finals <= 1).mean()*100:.1f}%")
    print(f"    Avg trades/sim: {np.mean(mc_trades):.0f}")

    # ─── Expanded series comparison ───
    if missing_series:
        expanded = set(KNOWN_SERIES)
        for prefix, stats in missing_series:
            expanded.add(prefix)

        print("\n" + "=" * 90)
        print(f"  EXPANDED SERIES COMPARISON (+{len(expanded) - len(KNOWN_SERIES)} series)")
        print("=" * 90)

        signals_exp = find_signals(
            by_market, markets, events, last_trade_times,
            series_set=expanded,
        )
        signals_exp.sort(key=lambda s: s.signal_time)

        if signals_exp:
            wins_exp = sum(1 for s in signals_exp if s.won)
            print(f"\n  Known series: {len(signals)} signals, {wr:.1f}% WR")
            print(f"  Expanded:     {len(signals_exp)} signals, {wins_exp/len(signals_exp)*100:.1f}% WR (+{len(signals_exp)-len(signals)} signals)")

            # Show new series contribution
            new_sigs = [s for s in signals_exp if not any(s.ticker.startswith(k) for k in KNOWN_SERIES)]
            new_wins = sum(1 for s in new_sigs if s.won)
            if new_sigs:
                print(f"  New series only: {len(new_sigs)} signals, {new_wins/len(new_sigs)*100:.1f}% WR")

                new_series_stats = defaultdict(lambda: {"total": 0, "wins": 0})
                for s in new_sigs:
                    p = extract_series_prefix(s.ticker) or "?"
                    new_series_stats[p]["total"] += 1
                    if s.won:
                        new_series_stats[p]["wins"] += 1

                print(f"\n  New series breakdown:")
                for p, ns in sorted(new_series_stats.items(), key=lambda x: -x[1]["total"]):
                    if ns["total"] >= 3:
                        print(f"    {p:<30} {ns['total']:>5} signals, {ns['wins']/ns['total']*100:.1f}% WR")

            # Run sim with expanded
            result_exp = simulate(signals_exp)
            if result_exp["trades"]:
                print(f"\n  Expanded sim: {len(result_exp['trades'])} trades, ${result_exp['final_bankroll']:,.2f} final (vs ${final:,.2f})")

    print("\n" + "=" * 90)


if __name__ == "__main__":
    asyncio.run(main())
