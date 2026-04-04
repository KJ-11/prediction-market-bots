"""E2E backtest of whale-following strategy against exact spec.

Signal Criteria (ALL must be true):
1. Category: sports OR economics
2. Event date: parsed from ticker (YYMMMDD). Must be today or tomorrow.
3. 3+ whale trades ($1k+ each) within a 30-minute window on same market
4. 90%+ of whale volume in that window on one side
5. Best ask on consensus side is 85-95c (using trade price as proxy)

Entry: consensus side at avg whale price + 1c slippage
Exit: 15% stop loss OR hold to resolution
Sizing: risk-phased half_port
Risk: circuit breaker (3 consecutive losses → skip, 20% daily loss → stop)

Outputs:
1. Watchlist size per day (criteria 1+2)
2. Signal frequency (criteria 3+4+5 fire)
3. Trade frequency (after sizing + risk)
4. Win rate
5. P&L curve from $100
6. Monte Carlo confidence intervals
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
SLIPPAGE_CENTS = 0.01  # We enter 1c worse than whale avg
CATEGORIES = {"sports", "economics"}

# Known game-day series tickers (from your spec)
KNOWN_SERIES = {
    # Sports
    "KXMLBGAME", "KXNBAGAME", "KXNHLGAME", "KXATPMATCH", "KXWTAMATCH",
    "KXIPLGAME", "KXUFCFIGHT", "KXMLBHR", "KXNBA1HTOTAL", "KXMLB1HTOTAL",
    "KXNHL1HTOTAL", "KXATPCHALLENGERMATCH", "KXMLBHIT", "KXMLBSTRIKEOUT",
    "KXNBAPLAYER", "KXNBAPLAYERPTS", "KXNBAAST", "KXNBA2D", "KXNBAREB",
    "KXNHLGOAL", "KXNHLPTS", "KXMLBHRR",
    # Economics
    "KXWTI", "KXINXU", "KXINXD", "KXGOLD", "KXSILVER", "KXNATGAS",
    # T20
    "KXT20MATCH",
}
STARTING_BANKROLL = 100.0

# Risk phases: (floor, ceiling, max_bet_pct)
PHASES = [
    (0, 500, 1.0),
    (500, 1_000, 0.50),
    (1_000, 5_000, 0.30),
    (5_000, 50_000, 0.20),
    (50_000, float("inf"), 0.10),
]

# Circuit breaker
MAX_CONSECUTIVE_LOSSES = 3
DAILY_LOSS_LIMIT_PCT = 20.0

FEE_COEFF = 0.07
ENV_PATH = Path(__file__).resolve().parent.parent.parent / "Profitlabs" / "profitlabs-ml-pipeline" / ".env"


def kalshi_fee(price: float, contracts: int) -> float:
    return math.ceil(FEE_COEFF * contracts * price * (1 - price) * 100) / 100


def parse_event_date(ticker: str):
    m = re.search(r'(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})', ticker, re.IGNORECASE)
    if not m:
        return None
    year = 2000 + int(m.group(1))
    months = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
    month = months[m.group(2).upper()]
    day = int(m.group(3))
    return f"{year}-{month:02d}-{day:02d}"


def get_risk_cap(bankroll: float) -> float:
    for floor, ceiling, pct in PHASES:
        if floor <= bankroll < ceiling:
            return bankroll * pct
    return bankroll * PHASES[-1][2]


def compute_contracts(bankroll: float, entry_price: float) -> int:
    risk_cap = get_risk_cap(bankroll)
    dollar_size = risk_cap * 0.5  # half_port
    fee_per = kalshi_fee(entry_price, 1)
    cost_per = entry_price + fee_per
    if cost_per <= 0:
        return 0
    contracts = int(dollar_size / cost_per)
    # Verify affordability
    if contracts > 0:
        total = entry_price * contracts + kalshi_fee(entry_price, contracts)
        while total > bankroll and contracts > 0:
            contracts -= 1
            total = entry_price * contracts + kalshi_fee(entry_price, contracts)
    return max(contracts, 0)


@dataclass
class Signal:
    """A signal that fired: market + window of whale trades."""
    market_id: str
    ticker: str
    event_date: str
    category: str
    consensus_side: str  # "yes" or "no"
    consensus_pct: float
    whale_count: int
    total_notional: float
    avg_entry_price: float  # avg whale price on consensus side
    window_start: datetime
    window_end: datetime
    resolution: str  # market resolution
    won: bool


@dataclass
class Trade:
    """An executed trade."""
    signal: Signal
    entry_price: float
    contracts: int
    entry_cost: float  # price * contracts + fee
    pnl: float = 0.0
    won: bool = False
    stopped: bool = False


def find_signals(trades_by_market: dict, markets: dict, events: dict) -> list[Signal]:
    """Find all signals matching the spec criteria."""
    signals = []

    for mid, trades in trades_by_market.items():
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

        # Check if ticker matches a known series
        if not any(ticker.startswith(s) for s in KNOWN_SERIES):
            continue

        resolution = m["resolution"].lower()

        # Sort trades by time
        sorted_trades = sorted(trades, key=lambda t: t["traded_at"])

        # Sliding window: find 30-min windows with 3+ trades
        # For each trade, look forward 30 minutes
        for i, t_start in enumerate(sorted_trades):
            start_time = isoparse(str(t_start["traded_at"]))
            window_end = start_time + timedelta(minutes=WINDOW_MINUTES)

            window_trades = []
            for j in range(i, len(sorted_trades)):
                t_time = isoparse(str(sorted_trades[j]["traded_at"]))
                if t_time <= window_end:
                    window_trades.append(sorted_trades[j])
                else:
                    break

            if len(window_trades) < MIN_WHALE_COUNT:
                continue

            # Check price range — use trade prices
            prices = [float(t["price"]) for t in window_trades]
            if not any(PRICE_MIN <= p <= PRICE_MAX for p in prices):
                continue

            # Compute consensus in this window
            yes_vol = sum(float(t["notional"]) for t in window_trades if t["outcome"].lower() == "yes")
            no_vol = sum(float(t["notional"]) for t in window_trades if t["outcome"].lower() == "no")
            total_vol = yes_vol + no_vol
            if total_vol == 0:
                continue

            if yes_vol >= no_vol:
                consensus_side = "yes"
                cons_pct = yes_vol / total_vol * 100
            else:
                consensus_side = "no"
                cons_pct = no_vol / total_vol * 100

            if cons_pct < CONSENSUS_PCT:
                continue

            # Avg entry price on consensus side (within our price range)
            consensus_trades = [
                t for t in window_trades
                if t["outcome"].lower() == consensus_side
                and PRICE_MIN <= float(t["price"]) <= PRICE_MAX
            ]
            if not consensus_trades:
                continue

            avg_price = sum(float(t["price"]) for t in consensus_trades) / len(consensus_trades)

            # Check trade date matches event date (today/tomorrow filter)
            trade_date = start_time.strftime("%Y-%m-%d")
            event_dt = datetime.strptime(event_date, "%Y-%m-%d")
            trade_dt = datetime.strptime(trade_date, "%Y-%m-%d")
            # Allow today or day before (for overnight events)
            if not (trade_dt == event_dt or trade_dt == event_dt - timedelta(days=1)):
                continue

            won = consensus_side == resolution

            signals.append(Signal(
                market_id=mid,
                ticker=ticker,
                event_date=event_date,
                category=cat,
                consensus_side=consensus_side,
                consensus_pct=cons_pct,
                whale_count=len(window_trades),
                total_notional=total_vol,
                avg_entry_price=avg_price,
                window_start=start_time,
                window_end=isoparse(str(window_trades[-1]["traded_at"])),
                resolution=resolution,
                won=won,
            ))

            # Only take the FIRST signal per market (no re-entry)
            break

    return signals


MAX_CONCURRENT = 2
KILL_SWITCH_PCT = 40.0  # Stop if total capital loss exceeds this

def simulate_trading(signals: list[Signal], seed: int = 42) -> dict:
    """Simulate trading with sizing, risk controls, concurrent limits, and P&L.

    Key: positions are NOT instantly resolved. Capital is locked until the
    event date ends. We model this by tracking open positions and only
    freeing capital when the event date passes (end of that day).
    """
    signals = sorted(signals, key=lambda s: s.window_start)

    bankroll = STARTING_BANKROLL
    peak_bankroll = bankroll
    day_start_bankroll = bankroll
    current_day = None
    consecutive_losses = 0
    skip_next = False
    stopped_for_day = False
    killed = False
    trades = []
    daily_pnl = defaultdict(float)
    bankroll_history = [(signals[0].window_start if signals else datetime.now(timezone.utc), bankroll)]

    # Track open positions: list of (entry_cost, event_date, signal)
    open_positions = []
    traded_markets = set()  # One signal per market

    for sig in signals:
        day = sig.window_start.strftime("%Y-%m-%d")

        # New day: settle positions whose event date has passed
        if day != current_day:
            current_day = day
            day_start_bankroll = bankroll
            stopped_for_day = False

            # Settle positions from completed event dates
            still_open = []
            for pos_cost, pos_event_date, pos_sig, pos_entry_price, pos_contracts in open_positions:
                if pos_event_date < day:
                    # Resolve this position
                    if pos_sig.won:
                        pnl = pos_contracts * 1.0 - pos_cost
                    else:
                        stop_price = pos_entry_price * (1 - STOP_LOSS_PCT)
                        exit_fee = kalshi_fee(stop_price, pos_contracts)
                        pnl = (stop_price * pos_contracts - exit_fee) - pos_cost

                    bankroll += pnl
                    bankroll = max(bankroll, 0)
                    daily_pnl[day] += pnl

                    trade = Trade(
                        signal=pos_sig, entry_price=pos_entry_price,
                        contracts=pos_contracts, entry_cost=pos_cost,
                        pnl=pnl, won=pos_sig.won, stopped=not pos_sig.won,
                    )
                    trades.append(trade)
                    bankroll_history.append((sig.window_start, bankroll))

                    if pos_sig.won:
                        consecutive_losses = 0
                    else:
                        consecutive_losses += 1
                        if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                            skip_next = True
                else:
                    still_open.append((pos_cost, pos_event_date, pos_sig, pos_entry_price, pos_contracts))
            open_positions = still_open

            # Kill switch check
            if peak_bankroll > 0 and (peak_bankroll - bankroll) / peak_bankroll * 100 >= KILL_SWITCH_PCT:
                killed = True

        if killed or stopped_for_day:
            continue

        if skip_next:
            skip_next = False
            continue

        if bankroll <= 0:
            continue

        # Max concurrent check
        if len(open_positions) >= MAX_CONCURRENT:
            continue

        # One signal per market
        if sig.market_id in traded_markets:
            continue

        # Available bankroll = total - locked in open positions
        locked = sum(c for c, _, _, _, _ in open_positions)
        available = bankroll - locked
        if available <= 0:
            continue

        entry_price = min(sig.avg_entry_price + SLIPPAGE_CENTS, 0.99)
        if entry_price < PRICE_MIN or entry_price > PRICE_MAX + SLIPPAGE_CENTS:
            continue

        # Size against available (not total) bankroll
        risk_cap = get_risk_cap(bankroll)
        dollar_size = min(risk_cap * 0.5, available)  # half_port capped by available
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
            contracts -= 1
            if contracts <= 0:
                continue
            entry_fee = kalshi_fee(entry_price, contracts)
            entry_cost = entry_price * contracts + entry_fee

        # Open the position
        open_positions.append((entry_cost, sig.event_date, sig, entry_price, contracts))
        traded_markets.add(sig.market_id)
        peak_bankroll = max(peak_bankroll, bankroll)

        # Daily loss check
        if day_start_bankroll > 0:
            # Include unrealized: locked capital could be lost
            daily_loss_pct = (day_start_bankroll - (bankroll - locked)) / day_start_bankroll * 100
            if daily_loss_pct >= DAILY_LOSS_LIMIT_PCT:
                stopped_for_day = True

    # Settle remaining open positions
    for pos_cost, pos_event_date, pos_sig, pos_entry_price, pos_contracts in open_positions:
        if pos_sig.won:
            pnl = pos_contracts * 1.0 - pos_cost
        else:
            stop_price = pos_entry_price * (1 - STOP_LOSS_PCT)
            exit_fee = kalshi_fee(stop_price, pos_contracts)
            pnl = (stop_price * pos_contracts - exit_fee) - pos_cost

        bankroll += pnl
        bankroll = max(bankroll, 0)

        trade = Trade(
            signal=pos_sig, entry_price=pos_entry_price,
            contracts=pos_contracts, entry_cost=pos_cost,
            pnl=pnl, won=pos_sig.won, stopped=not pos_sig.won,
        )
        trades.append(trade)

    bankroll_history.append((signals[-1].window_start if signals else datetime.now(timezone.utc), bankroll))

    return {
        "trades": trades,
        "bankroll_history": bankroll_history,
        "final_bankroll": bankroll,
        "daily_pnl": dict(daily_pnl),
        "killed": killed,
    }


def monte_carlo(signals: list[Signal], n_sims: int = 1000, seed: int = 42) -> dict:
    """Run Monte Carlo by shuffling signal order within each day.

    Models concurrent position limits and capital lockup properly:
    - Max 2 positions open at once
    - Capital locked until event date passes
    - Positions settle at start of next day
    """
    rng = np.random.default_rng(seed)

    by_day = defaultdict(list)
    for s in signals:
        by_day[s.window_start.strftime("%Y-%m-%d")].append(s)
    days = sorted(by_day.keys())

    finals = []
    trajectories = []

    for sim in range(n_sims):
        bankroll = STARTING_BANKROLL
        peak = bankroll
        consecutive_losses = 0
        skip_next = False
        killed = False
        daily_bankrolls = []
        # open_positions: list of (cost, event_date, signal, entry_price, contracts)
        open_positions = []
        traded_markets = set()

        for day in days:
            if killed:
                daily_bankrolls.append(bankroll)
                continue

            # Settle positions from prior days
            still_open = []
            for pos_cost, pos_ed, pos_sig, pos_ep, pos_c in open_positions:
                if pos_ed < day:
                    if pos_sig.won:
                        pnl = pos_c * 1.0 - pos_cost
                    else:
                        sp = pos_ep * (1 - STOP_LOSS_PCT)
                        ef = kalshi_fee(sp, pos_c)
                        pnl = (sp * pos_c - ef) - pos_cost
                    bankroll += pnl
                    bankroll = max(bankroll, 0)
                    if pos_sig.won:
                        consecutive_losses = 0
                    else:
                        consecutive_losses += 1
                        if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                            skip_next = True
                else:
                    still_open.append((pos_cost, pos_ed, pos_sig, pos_ep, pos_c))
            open_positions = still_open

            # Kill switch
            peak = max(peak, bankroll)
            if peak > 0 and (peak - bankroll) / peak * 100 >= KILL_SWITCH_PCT:
                killed = True
                daily_bankrolls.append(bankroll)
                continue

            day_signals = list(by_day[day])
            rng.shuffle(day_signals)
            day_start = bankroll
            stopped_for_day = False

            for sig in day_signals:
                if stopped_for_day or bankroll <= 0 or killed:
                    continue
                if skip_next:
                    skip_next = False
                    continue
                if len(open_positions) >= MAX_CONCURRENT:
                    continue
                if sig.market_id in traded_markets:
                    continue

                locked = sum(c for c, _, _, _, _ in open_positions)
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
                    contracts = int(available / cost_per) - 1
                    if contracts <= 0:
                        continue
                    entry_fee = kalshi_fee(entry_price, contracts)
                    entry_cost = entry_price * contracts + entry_fee

                open_positions.append((entry_cost, sig.event_date, sig, entry_price, contracts))
                traded_markets.add(sig.market_id)

                if day_start > 0:
                    locked_now = sum(c for c, _, _, _, _ in open_positions)
                    if (day_start - (bankroll - locked_now)) / day_start * 100 >= DAILY_LOSS_LIMIT_PCT:
                        stopped_for_day = True

            daily_bankrolls.append(bankroll)

        # Settle remaining
        for pos_cost, _, pos_sig, pos_ep, pos_c in open_positions:
            if pos_sig.won:
                pnl = pos_c * 1.0 - pos_cost
            else:
                sp = pos_ep * (1 - STOP_LOSS_PCT)
                ef = kalshi_fee(sp, pos_c)
                pnl = (sp * pos_c - ef) - pos_cost
            bankroll += pnl
            bankroll = max(bankroll, 0)

        finals.append(bankroll)
        trajectories.append(daily_bankrolls)

    return {
        "finals": np.array(finals),
        "trajectories": np.array(trajectories),
        "days": days,
    }


async def main():
    env_path = ENV_PATH
    if len(sys.argv) > 1:
        env_path = Path(sys.argv[1])

    if not env_path.exists():
        # Try the other common path
        env_path = Path("/Users/kj/Code/Profitlabs/profitlabs-ml-pipeline/.env")
    if not env_path.exists():
        print(f"Error: .env not found at {env_path}")
        sys.exit(1)

    env = dotenv_values(env_path)
    from supabase import create_async_client
    client = await create_async_client(env["SUPABASE_URL"], env["SUPABASE_SECRET_KEY"])

    pids = await client.table("platforms").select("id,slug").execute()
    k_id = [p["id"] for p in pids.data if p["slug"] == "kalshi"][0]

    # Fetch whale trades at target prices
    print("Fetching whale trades...")
    all_trades = []
    offset = 0
    while True:
        resp = await (
            client.table("trades")
            .select("market_id, price, notional, outcome, traded_at")
            .eq("platform_id", k_id)
            .gte("notional", WHALE_THRESHOLD)
            .gte("price", PRICE_MIN)
            .lte("price", PRICE_MAX)
            .order("traded_at", desc=True)
            .range(offset, offset + 999)
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
        batch = mids[i:i + 50]
        resp = await client.table("markets").select(
            "id, external_id, resolution, resolved, event_id, updated_at"
        ).in_("id", batch).execute()
        for m in resp.data:
            markets[m["id"]] = m

    # Fetch events
    eids = list(set(m.get("event_id") for m in markets.values() if m.get("event_id")))
    print(f"Fetching {len(eids)} events...")
    events = {}
    for i in range(0, len(eids), 50):
        batch = eids[i:i + 50]
        resp = await client.table("events").select("id, category, series_slug").in_("id", batch).execute()
        for e in resp.data:
            events[e["id"]] = e

    # Group trades by market
    by_market = defaultdict(list)
    for t in all_trades:
        by_market[t["market_id"]].append(t)

    # Find signals
    print("Finding signals...")
    signals = find_signals(by_market, markets, events)
    print(f"Found {len(signals)} signals")

    if not signals:
        print("No signals found.")
        return

    # Sort by time
    signals.sort(key=lambda s: s.window_start)
    date_range = f"{signals[0].window_start.strftime('%Y-%m-%d')} to {signals[-1].window_start.strftime('%Y-%m-%d')}"

    # --- SECTION 1: WATCHLIST SIZE ---
    print()
    print("=" * 70)
    print("  WHALE-FOLLOWING E2E BACKTEST")
    print("=" * 70)
    print(f"  Date range: {date_range}")
    print(f"  Criteria: {MIN_WHALE_COUNT}+ whales in {WINDOW_MINUTES}min, {CONSENSUS_PCT}%+ consensus, {PRICE_MIN}-{PRICE_MAX}c")
    print(f"  Categories: {', '.join(CATEGORIES)}")
    print(f"  Event date filter: ticker-parsed, same day or day before")

    # Count unique markets per day that have parseable event dates in sports/economics
    # (watchlist = markets we'd be monitoring)
    watchlist_by_day = defaultdict(set)
    for mid, m in markets.items():
        e = events.get(m.get("event_id", ""), {})
        cat = (e.get("category") or "").lower()
        if cat not in CATEGORIES:
            continue
        ticker = m.get("external_id", "")
        ed = parse_event_date(ticker)
        if ed:
            watchlist_by_day[ed].add(mid)

    wl_counts = [len(v) for v in watchlist_by_day.values()]
    if wl_counts:
        print(f"\n  WATCHLIST (sports+economics markets with event date):")
        print(f"    Avg markets/day: {sum(wl_counts)/len(wl_counts):.0f}")
        print(f"    Median: {sorted(wl_counts)[len(wl_counts)//2]}")

    # --- SECTION 2: SIGNAL FREQUENCY ---
    sig_by_day = defaultdict(list)
    for s in signals:
        sig_by_day[s.window_start.strftime("%Y-%m-%d")].append(s)

    sig_counts = [len(v) for v in sig_by_day.values()]
    active_days = len(sig_by_day)
    total_days = (signals[-1].window_start - signals[0].window_start).days + 1

    print(f"\n  SIGNAL FREQUENCY:")
    print(f"    Total signals: {len(signals)} across {active_days} active days ({total_days} calendar days)")
    if sig_counts:
        print(f"    Avg signals/day: {sum(sig_counts)/active_days:.1f}")
        print(f"    Median: {sorted(sig_counts)[len(sig_counts)//2]}")
        print(f"    Range: {min(sig_counts)}-{max(sig_counts)}")
    print(f"    Days with 0 signals: {total_days - active_days}")

    # --- SECTION 3: WIN RATE ---
    wins = sum(1 for s in signals if s.won)
    wr = wins / len(signals) * 100
    print(f"\n  WIN RATE:")
    print(f"    Overall: {wr:.1f}% ({wins}/{len(signals)})")

    # By category
    for cat in sorted(CATEGORIES):
        sub = [s for s in signals if s.category == cat]
        if sub:
            w = sum(1 for s in sub if s.won)
            print(f"    {cat}: {w/len(sub)*100:.1f}% ({w}/{len(sub)})")

    # By whale count
    for min_wh in [3, 5, 10]:
        sub = [s for s in signals if s.whale_count >= min_wh]
        if len(sub) >= 5:
            w = sum(1 for s in sub if s.won)
            print(f"    {min_wh}+ whales: {w/len(sub)*100:.1f}% ({w}/{len(sub)})")

    # --- SECTION 4: DETERMINISTIC P&L ---
    print(f"\n  DETERMINISTIC P&L (chronological order):")
    result = simulate_trading(signals)
    trades = result["trades"]
    final = result["final_bankroll"]

    if trades:
        trade_wins = sum(1 for t in trades if t.won)
        print(f"    Trades executed: {len(trades)}")
        print(f"    Wins: {trade_wins}, Losses: {len(trades)-trade_wins}")
        print(f"    Trade WR: {trade_wins/len(trades)*100:.1f}%")
        print(f"    Starting bankroll: ${STARTING_BANKROLL:.2f}")
        print(f"    Final bankroll: ${final:,.2f}")
        print(f"    Total return: {(final/STARTING_BANKROLL - 1)*100:.1f}%")
        total_pnl = sum(t.pnl for t in trades)
        print(f"    Total P&L: ${total_pnl:,.2f}")
        print(f"    Avg P&L/trade: ${total_pnl/len(trades):.2f}")

        # Milestones
        for milestone in [1_000, 10_000, 50_000, 100_000]:
            for ts, bal in result["bankroll_history"]:
                if bal >= milestone:
                    days_in = (ts - result["bankroll_history"][0][0]).days
                    print(f"    ${milestone:,} reached: day {days_in}")
                    break
            else:
                print(f"    ${milestone:,}: not reached")

    # --- SECTION 5: MONTE CARLO ---
    print(f"\n  MONTE CARLO ({1000} simulations, shuffled order within each day):")
    mc = monte_carlo(signals, n_sims=1000)
    finals = mc["finals"]

    print(f"    Median final: ${np.median(finals):,.2f}")
    print(f"    Mean final: ${np.mean(finals):,.2f}")
    print(f"    p5: ${np.percentile(finals, 5):,.2f}")
    print(f"    p95: ${np.percentile(finals, 95):,.2f}")
    print(f"    Ruin (bankroll=0): {(finals <= 0).mean()*100:.1f}%")

    # Max drawdown
    traj = mc["trajectories"]
    running_max = np.maximum.accumulate(traj, axis=1)
    drawdowns = np.where(running_max > 0, (running_max - traj) / running_max, 0)
    max_dd = drawdowns.max(axis=1) * 100
    print(f"    Max drawdown (median): {np.median(max_dd):.1f}%")
    print(f"    Max drawdown (p95): {np.percentile(max_dd, 95):.1f}%")

    # Time to milestones
    print(f"\n    Time to milestones (median days):")
    for milestone in [1_000, 10_000, 50_000, 100_000, 500_000]:
        days_to = []
        for path in traj:
            for d, bal in enumerate(path):
                if bal >= milestone:
                    days_to.append(d + 1)
                    break
        if len(days_to) >= len(traj) * 0.5:
            med = sorted(days_to)[len(days_to) // 2]
            print(f"      ${milestone:>8,}: {med} days ({len(days_to)}/{len(traj)} paths reach it)")
        else:
            print(f"      ${milestone:>8,}: <50% of paths reach it ({len(days_to)}/{len(traj)})")

    print()
    print("=" * 70)

    await client.auth.sign_out()


if __name__ == "__main__":
    asyncio.run(main())
