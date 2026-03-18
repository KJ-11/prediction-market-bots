"""Analyze liquidity in Kalshi crypto 15-minute markets.

Looks at volume deltas, bid-ask spreads, and fill capacity
during the T+300-540 trading window using collected round data.
"""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "rounds"
SERIES_LIST = ["KXBTC15M", "KXETH15M", "KXSOL15M"]
DATES = ["2026-03-08", "2026-03-09"]

# Checkpoints in seconds elapsed
CHECKPOINTS = [240, 300, 360, 420, 480, 540, 600, 660, 720, 780, 840]
WINDOW_START = 300
WINDOW_END = 540
DIST_THRESHOLD = Decimal("0.002")


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


def get_volume_at_checkpoint(rows: list[dict], target_elapsed: int) -> int | None:
    """Find the volume value closest to the target elapsed time."""
    best_row = None
    best_diff = float("inf")
    for row in rows:
        try:
            elapsed = float(row["seconds_elapsed"])
        except (ValueError, KeyError):
            continue
        diff = abs(elapsed - target_elapsed)
        if diff < best_diff and diff <= 5:  # within 5 seconds tolerance
            best_diff = diff
            best_row = row
    if best_row is None:
        return None
    vol_str = best_row.get("volume", "")
    if not vol_str:
        return None
    try:
        return int(float(vol_str))
    except (ValueError, TypeError):
        return None


def get_spread_in_window(rows: list[dict], start: int, end: int) -> list[Decimal]:
    """Get bid-ask spreads during a time window."""
    spreads = []
    for row in rows:
        try:
            elapsed = float(row["seconds_elapsed"])
        except (ValueError, KeyError):
            continue
        if elapsed < start or elapsed > end:
            continue
        try:
            yes_bid = Decimal(row.get("yes_bid", ""))
            yes_ask = Decimal(row.get("yes_ask", ""))
        except (InvalidOperation, TypeError):
            continue
        if yes_bid > 0 and yes_ask > 0:
            spreads.append(yes_ask - yes_bid)
    return spreads


def has_signal(rows: list[dict]) -> bool:
    """Check if spot distance > 0.2% from strike in T+300-540."""
    for row in rows:
        try:
            elapsed = float(row["seconds_elapsed"])
        except (ValueError, KeyError):
            continue
        if elapsed < WINDOW_START or elapsed > WINDOW_END:
            continue
        try:
            spot = Decimal(row.get("spot_price", ""))
            strike = Decimal(row.get("strike", ""))
        except (InvalidOperation, TypeError):
            continue
        if strike == 0:
            continue
        dist = abs(spot - strike) / strike
        if dist >= DIST_THRESHOLD:
            return True
    return False


def get_signal_entry_elapsed(rows: list[dict]) -> float | None:
    """Return the seconds_elapsed of the first signal row."""
    for row in rows:
        try:
            elapsed = float(row["seconds_elapsed"])
        except (ValueError, KeyError):
            continue
        if elapsed < WINDOW_START or elapsed > WINDOW_END:
            continue
        try:
            spot = Decimal(row.get("spot_price", ""))
            strike = Decimal(row.get("strike", ""))
        except (InvalidOperation, TypeError):
            continue
        if strike == 0:
            continue
        dist = abs(spot - strike) / strike
        if dist >= DIST_THRESHOLD:
            return elapsed
    return None


def get_volume_after_entry(rows: list[dict], entry_elapsed: float, window_secs: int = 60) -> int | None:
    """Volume traded in the N seconds after entry point."""
    vol_at_entry = None
    vol_after = None
    for row in rows:
        try:
            elapsed = float(row["seconds_elapsed"])
        except (ValueError, KeyError):
            continue
        vol_str = row.get("volume", "")
        if not vol_str:
            continue
        try:
            vol = int(float(vol_str))
        except (ValueError, TypeError):
            continue
        # Closest to entry
        if vol_at_entry is None or abs(elapsed - entry_elapsed) < abs(vol_at_entry[1] - entry_elapsed):
            if abs(elapsed - entry_elapsed) <= 3:
                vol_at_entry = (vol, elapsed)
        # Closest to entry + window_secs
        target = entry_elapsed + window_secs
        if vol_after is None or abs(elapsed - target) < abs(vol_after[1] - target):
            if abs(elapsed - target) <= 5:
                vol_after = (vol, elapsed)
    if vol_at_entry is not None and vol_after is not None:
        return vol_after[0] - vol_at_entry[0]
    return None


def fmt_stats(values: list[int | float], label: str = "") -> str:
    if not values:
        return f"{label}: no data"
    mn = min(values)
    mx = max(values)
    avg = statistics.mean(values)
    med = statistics.median(values)
    return f"{label}avg={avg:.1f}  med={med:.1f}  min={mn}  max={mx}  n={len(values)}"


def main() -> None:
    all_rounds: list[RoundData] = []
    for date in DATES:
        for series in SERIES_LIST:
            filepath = DATA_DIR / f"{series}-{date}.csv"
            if not filepath.exists():
                print(f"  [skip] {filepath.name} not found")
                continue
            rounds = load_rounds(filepath, series)
            all_rounds.extend(rounds)
            print(f"  Loaded {len(rounds)} rounds from {filepath.name}")

    print(f"\nTotal rounds loaded: {len(all_rounds)}")

    # ── 1. Volume at checkpoints and deltas ──────────────────────────
    checkpoint_volumes: dict[int, list[int]] = defaultdict(list)
    delta_volumes: dict[str, list[int]] = defaultdict(list)
    total_round_volumes: list[int] = []
    window_volumes: list[int] = []

    # Signal vs no-signal split
    signal_window_vols: list[int] = []
    nosignal_window_vols: list[int] = []
    signal_total_vols: list[int] = []
    nosignal_total_vols: list[int] = []

    # Per-series stats
    series_window_vols: dict[str, list[int]] = defaultdict(list)

    # Spreads
    window_spreads: list[Decimal] = []
    signal_spreads: list[Decimal] = []
    nosignal_spreads: list[Decimal] = []

    # Fill capacity
    fill_after_60s: list[int] = []
    fill_after_60s_by_series: dict[str, list[int]] = defaultdict(list)

    for rd in all_rounds:
        if len(rd.rows) < 10:
            continue  # skip very short rounds

        # Volume at each checkpoint
        cp_vols: dict[int, int] = {}
        for cp in CHECKPOINTS:
            v = get_volume_at_checkpoint(rd.rows, cp)
            if v is not None:
                checkpoint_volumes[cp].append(v)
                cp_vols[cp] = v

        # Deltas between consecutive checkpoints
        for i in range(len(CHECKPOINTS) - 1):
            cp_a, cp_b = CHECKPOINTS[i], CHECKPOINTS[i + 1]
            if cp_a in cp_vols and cp_b in cp_vols:
                delta = cp_vols[cp_b] - cp_vols[cp_a]
                label = f"T+{cp_a}-{cp_b}"
                delta_volumes[label].append(delta)

        # Total round volume (last checkpoint minus first)
        first_vol = cp_vols.get(CHECKPOINTS[0])
        last_vol = cp_vols.get(CHECKPOINTS[-1])
        if first_vol is not None and last_vol is not None:
            total_round_volumes.append(last_vol)

        # Window volume (T+300 to T+540)
        v300 = cp_vols.get(300)
        v540 = cp_vols.get(540)
        is_signal = has_signal(rd.rows)

        if v300 is not None and v540 is not None:
            wv = v540 - v300
            window_volumes.append(wv)
            series_window_vols[rd.series].append(wv)
            if is_signal:
                signal_window_vols.append(wv)
            else:
                nosignal_window_vols.append(wv)

        if last_vol is not None:
            if is_signal:
                signal_total_vols.append(last_vol)
            else:
                nosignal_total_vols.append(last_vol)

        # Spreads in window
        spreads = get_spread_in_window(rd.rows, WINDOW_START, WINDOW_END)
        window_spreads.extend(spreads)
        if is_signal:
            signal_spreads.extend(spreads)
        else:
            nosignal_spreads.extend(spreads)

        # Fill capacity: volume in 60s after signal entry
        entry_t = get_signal_entry_elapsed(rd.rows)
        if entry_t is not None:
            fill = get_volume_after_entry(rd.rows, entry_t, 60)
            if fill is not None:
                fill_after_60s.append(fill)
                fill_after_60s_by_series[rd.series].append(fill)

    # ── Print results ────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("VOLUME AT CHECKPOINTS (cumulative contracts traded)")
    print("=" * 72)
    for cp in CHECKPOINTS:
        vals = checkpoint_volumes.get(cp, [])
        print(f"  T+{cp:>3d}:  {fmt_stats(vals)}")

    print("\n" + "=" * 72)
    print("VOLUME DELTAS (contracts traded per 60s interval)")
    print("=" * 72)
    for i in range(len(CHECKPOINTS) - 1):
        label = f"T+{CHECKPOINTS[i]}-{CHECKPOINTS[i+1]}"
        vals = delta_volumes.get(label, [])
        marker = " <-- TRADING WINDOW" if WINDOW_START <= CHECKPOINTS[i] < WINDOW_END else ""
        print(f"  {label}:  {fmt_stats(vals)}{marker}")

    print("\n" + "=" * 72)
    print("TOTAL ROUND VOLUME (cumulative at T+840)")
    print("=" * 72)
    print(f"  {fmt_stats(total_round_volumes)}")

    print("\n" + "=" * 72)
    print("VOLUME IN TRADING WINDOW (T+300 to T+540)")
    print("=" * 72)
    print(f"  All rounds:  {fmt_stats(window_volumes)}")
    for series in SERIES_LIST:
        vals = series_window_vols.get(series, [])
        print(f"  {series}:   {fmt_stats(vals)}")

    print("\n" + "=" * 72)
    print("SIGNAL vs NO-SIGNAL ROUNDS")
    print("=" * 72)
    print(f"  Signal rounds (dist > 0.2% in window):")
    print(f"    Window vol (T+300-540):  {fmt_stats(signal_window_vols)}")
    print(f"    Total vol (at T+840):    {fmt_stats(signal_total_vols)}")
    print(f"  No-signal rounds:")
    print(f"    Window vol (T+300-540):  {fmt_stats(nosignal_window_vols)}")
    print(f"    Total vol (at T+840):    {fmt_stats(nosignal_total_vols)}")

    print("\n" + "=" * 72)
    print("BID-ASK SPREADS IN TRADING WINDOW (T+300-540)")
    print("=" * 72)
    if window_spreads:
        ws = [float(s) for s in window_spreads]
        print(f"  All:       avg={statistics.mean(ws):.3f}  med={statistics.median(ws):.3f}  min={min(ws):.2f}  max={max(ws):.2f}  n={len(ws)}")
    if signal_spreads:
        ss = [float(s) for s in signal_spreads]
        print(f"  Signal:    avg={statistics.mean(ss):.3f}  med={statistics.median(ss):.3f}  min={min(ss):.2f}  max={max(ss):.2f}  n={len(ss)}")
    if nosignal_spreads:
        ns = [float(s) for s in nosignal_spreads]
        print(f"  No-signal: avg={statistics.mean(ns):.3f}  med={statistics.median(ns):.3f}  min={min(ns):.2f}  max={max(ns):.2f}  n={len(ns)}")
    # Spread distribution
    if window_spreads:
        spread_counts: dict[str, int] = defaultdict(int)
        for s in window_spreads:
            cent = int(s * 100)
            spread_counts[f"{cent}c"] += 1
        print(f"\n  Spread distribution:")
        for k in sorted(spread_counts.keys()):
            pct = spread_counts[k] / len(window_spreads) * 100
            print(f"    {k:>4s}: {spread_counts[k]:>6d} ({pct:.1f}%)")

    print("\n" + "=" * 72)
    print("FILL CAPACITY: Volume in 60s after signal entry")
    print("=" * 72)
    print(f"  All coins:  {fmt_stats(fill_after_60s)}")
    for series in SERIES_LIST:
        vals = fill_after_60s_by_series.get(series, [])
        if vals:
            print(f"  {series}:   {fmt_stats(vals)}")

    # Volume pattern: is it steady or spiky?
    print("\n" + "=" * 72)
    print("VOLUME PATTERN: Steady vs Spiky")
    print("=" * 72)
    print("  Coefficient of variation (stdev/mean) for each 60s interval:")
    for i in range(len(CHECKPOINTS) - 1):
        label = f"T+{CHECKPOINTS[i]}-{CHECKPOINTS[i+1]}"
        vals = delta_volumes.get(label, [])
        if len(vals) >= 2:
            avg = statistics.mean(vals)
            std = statistics.stdev(vals)
            cv = std / avg if avg > 0 else float("inf")
            print(f"    {label}: CV={cv:.2f}  (std={std:.1f}, mean={avg:.1f})")

    # Percentile distribution for window volume
    if window_volumes:
        wv_sorted = sorted(window_volumes)
        n = len(wv_sorted)
        print(f"\n  Window volume (T+300-540) percentiles:")
        for pct in [10, 25, 50, 75, 90, 95]:
            idx = min(int(n * pct / 100), n - 1)
            print(f"    P{pct:>2d}: {wv_sorted[idx]} contracts")

    if fill_after_60s:
        fs = sorted(fill_after_60s)
        n = len(fs)
        print(f"\n  Fill capacity (60s post-entry) percentiles:")
        for pct in [10, 25, 50, 75, 90, 95]:
            idx = min(int(n * pct / 100), n - 1)
            print(f"    P{pct:>2d}: {fs[idx]} contracts")


if __name__ == "__main__":
    main()
