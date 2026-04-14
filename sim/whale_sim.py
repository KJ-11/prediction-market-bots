"""CLI entry point for whale signal Monte Carlo simulation.

Usage:
    python -m sim.whale_sim                  # Full sweep
    python -m sim.whale_sim --quick          # Reduced grid, N=1000
    python -m sim.whale_sim --single         # One scenario
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from sim.config import QUICK_SWEEP, SimConfig, SweepConfig, TradeParams
from sim.engine import run_sweep, simulate_scenario
from sim.outputs import print_sensitivity, print_summary, write_csv

DEFAULT_OUTPUT_DIR = Path("data/sim_results")


def main() -> None:
    parser = argparse.ArgumentParser(description="Whale signal Monte Carlo simulation")
    parser.add_argument("--quick", action="store_true", help="Reduced grid, N=1000")
    parser.add_argument("--single", action="store_true", help="Run a single scenario")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument(
        "--sims", type=int, default=None,
        help="Override number of simulations per combo",
    )
    parser.add_argument("--days", type=int, default=365, help="Number of days to simulate")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed for reproducibility")

    # Single-scenario overrides
    parser.add_argument("--win-rate", type=float, default=0.93)
    parser.add_argument("--entry-price", type=float, default=0.92)
    parser.add_argument("--stop-loss", type=float, default=0.10)
    parser.add_argument("--trades-per-day", type=int, default=10)
    parser.add_argument("--sizing", type=str, default="half_port",
                        choices=["full_port", "half_port", "fractional_kelly"])
    parser.add_argument("--max-concurrent", type=int, default=1)
    parser.add_argument("--drift-penalty", type=float, default=0.0)
    parser.add_argument("--liquidity", type=float, default=5_000.0,
                        help="Max dollars fillable at target price (order book depth)")
    parser.add_argument("--bankroll", type=float, default=100.0)

    args = parser.parse_args()

    if args.single:
        _run_single(args)
    else:
        _run_sweep(args)


def _run_single(args: argparse.Namespace) -> None:
    """Run a single scenario and print the summary."""
    params = TradeParams(
        win_rate=args.win_rate,
        entry_price=args.entry_price,
        stop_loss_pct=args.stop_loss,
        trades_per_day=args.trades_per_day,
        sizing_strategy=args.sizing,
        max_concurrent=args.max_concurrent,
        drift_penalty=args.drift_penalty,
        liquidity=args.liquidity,
    )
    config = SimConfig(
        starting_bankroll=args.bankroll,
        num_simulations=args.sims or 10_000,
        num_days=args.days,
        seed=args.seed,
    )

    print(f"Running single scenario: {params}")
    print(
        f"Config: {config.num_simulations} sims x {config.num_days} days, "
        f"starting ${config.starting_bankroll}"
    )
    start = time.time()
    result = simulate_scenario(params, config, store_trajectories=True)
    elapsed = time.time() - start
    print(f"Completed in {elapsed:.1f}s\n")

    _print_single_result(result)


def _run_sweep(args: argparse.Namespace) -> None:
    """Run the full parameter sweep."""
    sweep = QUICK_SWEEP if args.quick else SweepConfig()
    config = SimConfig(
        starting_bankroll=args.bankroll,
        num_simulations=args.sims or (1_000 if args.quick else 10_000),
        num_days=args.days,
        seed=args.seed,
    )

    print(f"{'Quick' if args.quick else 'Full'} sweep: {sweep.total_combos} combos")
    start = time.time()
    results = run_sweep(sweep, config)
    elapsed = time.time() - start
    print(f"\nCompleted {len(results)} scenarios in {elapsed:.1f}s")

    # Write CSV
    csv_path = args.output / "sweep_results.csv"
    write_csv(results, csv_path)
    print(f"CSV written to {csv_path}")

    # Print summary
    print_summary(results)
    print_sensitivity(results)


def _print_single_result(r) -> None:
    """Pretty-print a single SimResult."""
    p = r.params
    print("=" * 60)
    print("  SIMULATION RESULTS")
    print("=" * 60)
    print(f"  Liquidity cap:             ${p.liquidity:>12,.0f}")
    print(f"  Final bankroll (median):   ${r.median_final:>12,.2f}")
    print(f"  Final bankroll (mean):     ${r.mean_final:>12,.2f}")
    print(f"  Final bankroll (p5):       ${r.p5_final:>12,.2f}")
    print(f"  Final bankroll (p95):      ${r.p95_final:>12,.2f}")
    print(f"  EV per trade:              ${r.ev_per_trade:>12.4f}")
    print(f"  Probability of ruin:        {r.probability_of_ruin:>11.1%}")
    print(f"  Max drawdown (median):      {r.max_drawdown_median:>11.1f}%")
    print(f"  Max drawdown (p95):         {r.max_drawdown_p95:>11.1f}%")
    print()
    print("  Time to milestones (median days):")
    milestones = [
        ("$1k", r.days_to_1k), ("$10k", r.days_to_10k),
        ("$50k", r.days_to_50k), ("$100k", r.days_to_100k),
        ("$500k", r.days_to_500k), ("$1M", r.days_to_1m),
    ]
    for label, days in milestones:
        if days is not None:
            print(f"    {label:>6}:  {days:>6.0f} days")
        else:
            print(f"    {label:>6}:  >50% never reach")
    print("=" * 60)


if __name__ == "__main__":
    main()
