"""Simulation results, CSV export, and summary printing."""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from sim.config import TradeParams


@dataclass
class SimResult:
    """Aggregate result of N Monte Carlo paths for one parameter combo."""

    params: TradeParams

    # Final bankroll stats
    median_final: float = 0.0
    mean_final: float = 0.0
    p5_final: float = 0.0
    p95_final: float = 0.0

    # Per-trade stats
    ev_per_trade: float = 0.0

    # Risk
    probability_of_ruin: float = 0.0
    max_drawdown_median: float = 0.0
    max_drawdown_p95: float = 0.0

    # Time to milestones (median days, None = <50% of paths reach it)
    days_to_1k: float | None = None
    days_to_10k: float | None = None
    days_to_50k: float | None = None
    days_to_100k: float | None = None
    days_to_500k: float | None = None
    days_to_1m: float | None = None

    # Raw trajectories for charting (shape: N x num_days, only if requested)
    trajectories: np.ndarray | None = field(default=None, repr=False)


MILESTONES = [1_000, 10_000, 50_000, 100_000, 500_000, 1_000_000]
MILESTONE_ATTRS = [
    "days_to_1k", "days_to_10k", "days_to_50k",
    "days_to_100k", "days_to_500k", "days_to_1m",
]

CSV_COLUMNS = [
    "win_rate", "entry_price", "stop_loss_pct", "trades_per_day",
    "sizing_strategy", "max_concurrent", "drift_penalty", "liquidity",
    "median_final", "mean_final", "p5_final", "p95_final",
    "ev_per_trade", "probability_of_ruin",
    "max_drawdown_median", "max_drawdown_p95",
    "days_to_1k", "days_to_10k", "days_to_50k",
    "days_to_100k", "days_to_500k", "days_to_1m",
]


def write_csv(results: list[SimResult], path: Path) -> None:
    """Write sweep results to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in results:
            row = {
                "win_rate": r.params.win_rate,
                "entry_price": r.params.entry_price,
                "stop_loss_pct": r.params.stop_loss_pct,
                "trades_per_day": r.params.trades_per_day,
                "sizing_strategy": r.params.sizing_strategy,
                "max_concurrent": r.params.max_concurrent,
                "drift_penalty": r.params.drift_penalty,
                "liquidity": r.params.liquidity,
                "median_final": f"{r.median_final:.2f}",
                "mean_final": f"{r.mean_final:.2f}",
                "p5_final": f"{r.p5_final:.2f}",
                "p95_final": f"{r.p95_final:.2f}",
                "ev_per_trade": f"{r.ev_per_trade:.6f}",
                "probability_of_ruin": f"{r.probability_of_ruin:.4f}",
                "max_drawdown_median": f"{r.max_drawdown_median:.4f}",
                "max_drawdown_p95": f"{r.max_drawdown_p95:.4f}",
                "days_to_1k": f"{r.days_to_1k:.1f}" if r.days_to_1k is not None else "",
                "days_to_10k": f"{r.days_to_10k:.1f}" if r.days_to_10k is not None else "",
                "days_to_50k": f"{r.days_to_50k:.1f}" if r.days_to_50k is not None else "",
                "days_to_100k": f"{r.days_to_100k:.1f}" if r.days_to_100k is not None else "",
                "days_to_500k": f"{r.days_to_500k:.1f}" if r.days_to_500k is not None else "",
                "days_to_1m": f"{r.days_to_1m:.1f}" if r.days_to_1m is not None else "",
            }
            writer.writerow(row)


def print_summary(results: list[SimResult]) -> None:
    """Print top/bottom parameter combos to stdout."""
    if not results:
        print("No results to summarize.")
        return

    _print_section(
        "TOP 10 BY MEDIAN FINAL BANKROLL",
        sorted(results, key=lambda r: r.median_final, reverse=True)[:10],
    )
    _print_section(
        "TOP 10 BY EV PER TRADE",
        sorted(results, key=lambda r: r.ev_per_trade, reverse=True)[:10],
    )
    _print_section(
        "LOWEST RUIN PROBABILITY (non-zero median)",
        sorted(
            [r for r in results if r.median_final > 0],
            key=lambda r: r.probability_of_ruin,
        )[:10],
    )
    _print_section(
        "FASTEST TO $10K (median days)",
        sorted(
            [r for r in results if r.days_to_10k is not None],
            key=lambda r: r.days_to_10k,  # type: ignore[arg-type]
        )[:10],
    )


def _print_section(title: str, results: list[SimResult]) -> None:
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}")
    header = (
        f"{'WR':>5} {'Entry':>6} {'SL%':>5} {'T/D':>4} {'Strategy':>10} "
        f"{'Conc':>4} {'Drift':>5} {'Liq':>7} | {'Median$':>10} {'EV/Trade':>9} "
        f"{'Ruin%':>6} {'DD-p95':>7} | {'->1k':>5} {'->10k':>6} {'->100k':>7}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        p = r.params
        d1k = f"{r.days_to_1k:.0f}d" if r.days_to_1k is not None else "-"
        d10k = f"{r.days_to_10k:.0f}d" if r.days_to_10k is not None else "-"
        d100k = f"{r.days_to_100k:.0f}d" if r.days_to_100k is not None else "-"
        print(
            f"{p.win_rate:>5.2f} {p.entry_price:>6.2f} {p.stop_loss_pct:>5.2f} "
            f"{p.trades_per_day:>4d} {p.sizing_strategy:>10} "
            f"{p.max_concurrent:>4d} {p.drift_penalty:>5.2f} {p.liquidity:>6,.0f} | "
            f"{r.median_final:>10,.0f} {r.ev_per_trade:>9.4f} "
            f"{r.probability_of_ruin * 100:>5.1f}% {r.max_drawdown_p95:>6.1f}% | "
            f"{d1k:>5} {d10k:>6} {d100k:>7}"
        )


def print_sensitivity(results: list[SimResult]) -> None:
    """Show marginal effect of each knob on median final bankroll."""
    if not results:
        return

    print(f"\n{'=' * 80}")
    print("  SENSITIVITY ANALYSIS (marginal effect on median final bankroll)")
    print(f"{'=' * 80}")

    knobs = [
        ("win_rate", lambda p: p.win_rate),
        ("entry_price", lambda p: p.entry_price),
        ("stop_loss_pct", lambda p: p.stop_loss_pct),
        ("trades_per_day", lambda p: p.trades_per_day),
        ("sizing_strategy", lambda p: p.sizing_strategy),
        ("max_concurrent", lambda p: p.max_concurrent),
        ("drift_penalty", lambda p: p.drift_penalty),
        ("liquidity", lambda p: p.liquidity),
    ]

    for knob_name, getter in knobs:
        # Group results by this knob's value
        groups: dict[object, list[float]] = {}
        for r in results:
            val = getter(r.params)
            groups.setdefault(val, []).append(r.median_final)

        if len(groups) < 2:
            continue

        print(f"\n  {knob_name}:")
        for val in sorted(groups.keys(), key=lambda x: (isinstance(x, str), x)):
            medians = groups[val]
            avg = sum(medians) / len(medians)
            print(f"    {str(val):>15} -> avg median final: ${avg:>12,.0f}")
