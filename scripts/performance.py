"""Bot performance report — lifecycle tracking with real Kalshi P&L.

Queries Kalshi API for settlements/fills (source of truth for money),
and parses alert logs for signal-level analytics.

Usage:
    python scripts/performance.py              # Full report
    python scripts/performance.py --sync       # Rsync data from VM first
    python scripts/performance.py --version v  # Report for specific version
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.clients.kalshi import KalshiClient
from shared.config import Settings

CST = ZoneInfo("America/Chicago")
DATA_DIR = Path("data")
LIFECYCLE_FILE = DATA_DIR / "lifecycle.json"
ALERTS_DIR = DATA_DIR / "alerts"

# ---- Data loading -----------------------------------------------------------


def load_lifecycle(version: str | None = None) -> dict:
    """Load lifecycle config, return the requested version's data."""
    data = json.loads(LIFECYCLE_FILE.read_text())
    v = version or data["current_version"]
    if v not in data["versions"]:
        print(f"Unknown version: {v}")
        print(f"Available: {', '.join(data['versions'].keys())}")
        sys.exit(1)
    config = data["versions"][v]
    config["version"] = v
    return config


def _parse_ts(ts_str: str) -> datetime:
    """Parse ISO timestamps from Kalshi API (Python 3.9 compatible)."""
    # Strip trailing Z
    ts_str = ts_str.replace("Z", "+00:00")
    # Remove fractional seconds entirely — we don't need sub-second precision
    ts_str = re.sub(r"\.\d+", "", ts_str)
    return datetime.fromisoformat(ts_str)


async def fetch_fills(client: KalshiClient, since_utc: str) -> list[dict]:
    """Fetch all fills from Kalshi API after a given UTC timestamp."""
    all_fills = []
    cursor = None
    since_dt = _parse_ts(since_utc + "+00:00")

    while True:
        params: dict = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = await client._get("/portfolio/fills", params=params)
        data = resp.json()
        fills = data.get("fills", [])
        if not fills:
            break

        for f in fills:
            created = _parse_ts(f["created_time"])
            if created >= since_dt:
                all_fills.append(f)

        cursor = data.get("cursor")
        if not cursor or len(fills) < 100:
            break

    return all_fills


async def fetch_settlements(
    client: KalshiClient, valid_tickers: set[str],
) -> list[dict]:
    """Fetch settlements from Kalshi API, filtered to only our tracked tickers."""
    all_settlements = []
    cursor = None

    while True:
        params: dict = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = await client._get("/portfolio/settlements", params=params)
        data = resp.json()
        settlements = data.get("settlements", [])
        if not settlements:
            break

        for s in settlements:
            if s["ticker"] in valid_tickers:
                all_settlements.append(s)

        cursor = data.get("cursor")
        if not cursor or len(settlements) < 100:
            break

    return all_settlements


async def fetch_balance(client: KalshiClient) -> dict:
    """Fetch current balance + portfolio value."""
    return await client.get_balance()


def parse_alert_logs(since_date: str) -> dict:
    """Parse local alert logs for signal/skip/fill analytics."""
    stats = {
        "signals": 0,
        "skips": defaultdict(int),  # reason -> count
        "fills": 0,
        "rounds_traded": 0,
        "rounds_empty": 0,
    }

    since = datetime.strptime(since_date, "%Y-%m-%d").date()

    for logfile in sorted(ALERTS_DIR.glob("*.log")):
        file_date = datetime.strptime(logfile.stem, "%Y-%m-%d").date()
        if file_date < since:
            continue

        for line in logfile.read_text().splitlines():
            # Skip continuation lines (indented)
            if not line.startswith("["):
                continue

            if "] SIGNAL:" in line:
                stats["signals"] += 1
            elif "] SKIP:" in line:
                # Extract skip reason
                skip_match = re.search(r"SKIP: \w+: (.+)", line)
                reason = skip_match.group(1) if skip_match else "unknown"
                # Normalize common reasons
                if "no edge" in reason:
                    stats["skips"]["price too high"] += 1
                elif "no liquidity" in reason:
                    stats["skips"]["no liquidity"] += 1
                elif "IOC no fill" in reason:
                    stats["skips"]["IOC no fill"] += 1
                elif "risk" in reason:
                    stats["skips"]["risk blocked"] += 1
                elif "trade cap" in reason:
                    stats["skips"]["round trade cap"] += 1
                else:
                    stats["skips"][reason] += 1
            elif "] FILL:" in line:
                stats["fills"] += 1
            elif "trade(s):" in line:
                stats["rounds_traded"] += 1
            elif "No signals" in line:
                stats["rounds_empty"] += 1

    return stats


# ---- Metrics computation ----------------------------------------------------


def coin_from_ticker(event_ticker: str) -> str:
    """KXBTC15M-... -> BTC"""
    prefix = event_ticker.split("-")[0]
    return prefix[2:-3]


def compute_metrics(settlements: list[dict]) -> dict:
    """Compute all performance metrics from settlements."""
    trades = []
    by_coin: dict[str, list] = defaultdict(list)
    by_price_bucket: dict[str, list] = defaultdict(list)

    for s in settlements:
        # Skip zero-position settlements (market existed but we had no position)
        yes_count = int(s.get("yes_count", 0))
        no_count = int(s.get("no_count", 0))
        if yes_count == 0 and no_count == 0:
            continue

        coin = coin_from_ticker(s["event_ticker"])
        revenue = int(s["revenue"])  # cents
        cost = int(s.get("yes_total_cost", 0)) + int(s.get("no_total_cost", 0))
        fee_cents = round(float(s["fee_cost"]) * 100)
        pnl = revenue - cost - fee_cents
        count = yes_count + no_count

        # Determine entry price (cost per contract)
        price_cents = cost // count if count else 0
        price = price_cents / 100

        won = pnl > 0

        trade = {
            "coin": coin,
            "ticker": s["ticker"],
            "result": s["market_result"],
            "count": count,
            "cost": cost,
            "revenue": revenue,
            "fee": fee_cents,
            "pnl": pnl,
            "price": price,
            "won": won,
            "side": "yes" if yes_count > 0 else "no",
            "settled_time": s["settled_time"],
        }
        trades.append(trade)
        by_coin[coin].append(trade)

        # Price buckets
        if price < 0.75:
            bucket = "<$0.75"
        elif price < 0.80:
            bucket = "$0.75-0.79"
        elif price < 0.85:
            bucket = "$0.80-0.84"
        else:
            bucket = "$0.85+"
        by_price_bucket[bucket].append(trade)

    total_pnl = sum(t["pnl"] for t in trades)
    total_cost = sum(t["cost"] for t in trades)
    total_fees = sum(t["fee"] for t in trades)
    total_revenue = sum(t["revenue"] for t in trades)
    wins = [t for t in trades if t["won"]]
    losses = [t for t in trades if not t["won"]]

    return {
        "trades": trades,
        "by_coin": dict(by_coin),
        "by_price_bucket": dict(by_price_bucket),
        "total_pnl": total_pnl,
        "total_cost": total_cost,
        "total_fees": total_fees,
        "total_revenue": total_revenue,
        "wins": len(wins),
        "losses": len(losses),
        "avg_win": sum(t["pnl"] for t in wins) / len(wins) if wins else 0,
        "avg_loss": sum(t["pnl"] for t in losses) / len(losses) if losses else 0,
        "largest_win": max((t["pnl"] for t in wins), default=0),
        "largest_loss": min((t["pnl"] for t in losses), default=0),
    }


# ---- Report formatting ------------------------------------------------------


def _cents(c: int | float) -> str:
    """Format cents as dollars."""
    return f"${c / 100:+.2f}" if c >= 0 or c < 0 else f"${c / 100:.2f}"


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.0f}%" if total > 0 else "N/A"


def print_report(
    config: dict,
    metrics: dict,
    signal_stats: dict | None,
    balance: dict,
) -> None:
    """Print the full performance report."""
    start_bal = Decimal(config["start_balance"])
    cash = Decimal(str(balance["balance"])) / 100
    portfolio = Decimal(str(balance["portfolio_value"])) / 100
    current = cash + portfolio
    total_return = current - start_bal
    return_pct = (total_return / start_bal * 100) if start_bal > 0 else 0

    total_trades = metrics["wins"] + metrics["losses"]
    win_rate = metrics["wins"] / total_trades * 100 if total_trades else 0

    # Header
    print()
    print(f"  {config['version']} | Started: {config['start_date']} | {config['description']}")
    print(f"  Coins: {', '.join(config['coins'])}")
    deposits = sum(Decimal(d["amount"]) for d in config.get("deposits", []))
    withdrawals = sum(Decimal(w["amount"]) for w in config.get("withdrawals", []))
    print(f"  Deposits: ${deposits:.2f} | Withdrawals: ${withdrawals:.2f}")
    print()
    print("=" * 64)

    # Portfolio
    print()
    print(f"  Starting Balance:  ${start_bal:.2f}")
    print(f"  Current Balance:   ${current:.2f}  (${cash:.2f} cash + ${portfolio:.2f} positions)")
    print(f"  Return:            ${total_return:+.2f}  ({return_pct:+.1f}%)")
    print()
    print("-" * 64)

    # Overall stats
    print()
    print(f"  Trades: {total_trades}  ({metrics['wins']}W / {metrics['losses']}L)  —  {win_rate:.0f}% win rate")
    print(f"  P&L (settled):     {_cents(metrics['total_pnl'])}")
    print(f"  Capital deployed:  ${metrics['total_cost'] / 100:.2f}  across {total_trades} trades")
    print(f"  Total fees:        ${metrics['total_fees'] / 100:.2f}")
    print()

    # Edge analysis
    print("  Edge Analysis:")
    print(f"    Avg win:      {_cents(metrics['avg_win'])}")
    print(f"    Avg loss:     {_cents(metrics['avg_loss'])}")
    if metrics["avg_loss"] != 0:
        ratio = abs(metrics["avg_win"] / metrics["avg_loss"])
        print(f"    Win/loss ratio: {ratio:.2f}x")
    print(f"    Largest win:  {_cents(metrics['largest_win'])}")
    print(f"    Largest loss: {_cents(metrics['largest_loss'])}")
    print()
    print("-" * 64)

    # Per-coin breakdown
    print()
    print("  Per-Coin Breakdown:")
    print(f"  {'Coin':<6s} {'W/L':>8s} {'WR%':>6s} {'P&L':>10s} {'Avg Price':>10s} {'Trades':>7s}")
    print(f"  {'-'*6} {'-'*8} {'-'*6} {'-'*10} {'-'*10} {'-'*7}")

    for coin in sorted(metrics["by_coin"]):
        coin_trades = metrics["by_coin"][coin]
        cw = sum(1 for t in coin_trades if t["won"])
        cl = sum(1 for t in coin_trades if not t["won"])
        cpnl = sum(t["pnl"] for t in coin_trades)
        cavg = sum(t["price"] for t in coin_trades) / len(coin_trades)
        cwr = _pct(cw, cw + cl)
        print(f"  {coin:<6s} {cw}W/{cl}L{' '*(5-len(str(cw))-len(str(cl)))} {cwr:>6s} {_cents(cpnl):>10s} {'${:.2f}'.format(cavg):>10s} {len(coin_trades):>7d}")

    print()
    print("-" * 64)

    # Win rate by price bucket
    print()
    print("  Win Rate by Entry Price:")
    print(f"  {'Bucket':<12s} {'W/L':>8s} {'WR%':>6s} {'P&L':>10s}")
    print(f"  {'-'*12} {'-'*8} {'-'*6} {'-'*10}")

    for bucket in ["<$0.75", "$0.75-0.79", "$0.80-0.84", "$0.85+"]:
        if bucket not in metrics["by_price_bucket"]:
            continue
        bt = metrics["by_price_bucket"][bucket]
        bw = sum(1 for t in bt if t["won"])
        bl = sum(1 for t in bt if not t["won"])
        bpnl = sum(t["pnl"] for t in bt)
        bwr = _pct(bw, bw + bl)
        print(f"  {bucket:<12s} {bw}W/{bl}L{' '*(5-len(str(bw))-len(str(bl)))} {bwr:>6s} {_cents(bpnl):>10s}")

    # Signal analytics (from alert logs)
    if signal_stats and signal_stats["signals"] > 0:
        print()
        print("-" * 64)
        print()
        print("  Signal Analytics (from alert logs):")
        total_signals = signal_stats["signals"]
        fills = signal_stats["fills"]
        fill_rate = fills / total_signals * 100 if total_signals > 0 else 0
        print(f"    Signals generated: {total_signals}")
        print(f"    Fills:             {fills} ({fill_rate:.0f}% fill rate)")
        print(f"    Rounds traded:     {signal_stats['rounds_traded']}")
        print(f"    Rounds empty:      {signal_stats['rounds_empty']}")

        if signal_stats["skips"]:
            total_skips = sum(signal_stats["skips"].values())
            print(f"    Skips:             {total_skips}")
            for reason, count in sorted(
                signal_stats["skips"].items(), key=lambda x: -x[1],
            ):
                pct = count / total_skips * 100
                print(f"      {reason:<25s} {count:3d} ({pct:.0f}%)")

    # Trade log
    print()
    print("-" * 64)
    print()
    print("  Trade Log:")
    print(f"  {'Time (CST)':<14s} {'Coin':<5s} {'Side':<4s} {'Price':>7s} {'Size':>5s} {'Result':>7s} {'P&L':>8s}")
    print(f"  {'-'*14} {'-'*5} {'-'*4} {'-'*7} {'-'*5} {'-'*7} {'-'*8}")

    for t in sorted(metrics["trades"], key=lambda x: x["settled_time"]):
        settled = _parse_ts(t["settled_time"]).astimezone(CST)
        time_str = settled.strftime("%m/%d %H:%M")
        icon = "W" if t["won"] else "L"
        print(
            f"  {time_str:<14s} {t['coin']:<5s} {t['side']:<4s} "
            f"${t['price']:>5.2f} {t['count']:>5d} "
            f"{'  ' + icon:>7s} {_cents(t['pnl']):>8s}"
        )

    print()
    print("=" * 64)
    print()


# ---- CLI --------------------------------------------------------------------


def sync_data() -> None:
    """Rsync alert logs, trade logs, and round snapshots from VM."""
    print("Syncing data from VM...")
    remote = "kj@35.245.140.169:~/prediction-market-bots/data"
    ssh_opts = ["-e", "ssh -i ~/.ssh/google_compute_engine"]
    syncs = [
        (f"{remote}/alerts/", str(ALERTS_DIR) + "/"),
        (f"{remote}/trades/", str(DATA_DIR / "trades") + "/"),
        (f"{remote}/rounds/", str(DATA_DIR / "rounds") + "/"),
    ]
    for src, dst in syncs:
        Path(dst).mkdir(parents=True, exist_ok=True)
        cmd = ["rsync", "-avz", *ssh_opts, src, dst]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Sync failed for {src}: {result.stderr}")
            sys.exit(1)
    print("Sync complete.")


async def async_main(args: argparse.Namespace) -> None:
    if args.sync:
        sync_data()

    config = load_lifecycle(args.version)
    settings = Settings()
    client = KalshiClient(
        api_key_id=settings.kalshi_api_key_id,
        private_key_pem=settings.kalshi_private_key,
    )

    try:
        # Use start_time_utc to filter fills, then match settlements to those tickers
        start_time = config.get("start_time_utc", config["start_date"] + "T00:00:00")
        fills = await fetch_fills(client, start_time)
        # Get unique market tickers from our fills
        valid_tickers = {f["ticker"] for f in fills}
        settlements = await fetch_settlements(client, valid_tickers)
        balance = await fetch_balance(client)
    finally:
        await client.close()

    metrics = compute_metrics(settlements)

    # Parse alert logs if available locally
    signal_stats = None
    if ALERTS_DIR.exists() and any(ALERTS_DIR.glob("*.log")):
        signal_stats = parse_alert_logs(config["start_date"])
    else:
        print("(Alert logs not found locally. Run with --sync to fetch from VM.)")

    print_report(config, metrics, signal_stats, balance)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bot performance report")
    parser.add_argument(
        "--sync", action="store_true",
        help="Rsync alert logs from VM before reporting",
    )
    parser.add_argument(
        "--version", default=None,
        help="Report for specific bot version (default: current)",
    )
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
