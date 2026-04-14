"""Core Monte Carlo simulation engine.

Vectorized with numpy — simulates all N paths simultaneously, day by day.
"""

from __future__ import annotations

import itertools
import logging
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from sim.config import (
    SimConfig,
    SweepConfig,
    TradeParams,
)
from sim.fees import kalshi_fee
from sim.outputs import MILESTONE_ATTRS, MILESTONES, SimResult
from sim.sizing import compute_contracts, get_risk_cap

logger = logging.getLogger(__name__)


def simulate_scenario(
    params: TradeParams,
    config: SimConfig,
    store_trajectories: bool = False,
) -> SimResult:
    """Run N Monte Carlo paths for a single parameter combo.

    The day loop is sequential (bankroll depends on prior day), but all N
    paths are processed in parallel via numpy vectorization.
    """
    N = config.num_simulations
    D = config.num_days
    T = params.trades_per_day
    effective_wr = max(0.0, min(1.0, params.win_rate - params.drift_penalty))
    entry = params.entry_price
    stop_pct = params.stop_loss_pct
    liquidity = params.liquidity
    phases = config.phases

    rng = np.random.default_rng(config.seed)

    # Pre-generate all random outcomes: True = win, False = loss
    outcomes = rng.random((N, D, T)) < effective_wr

    # Bankroll trajectories: end-of-day values
    bankrolls = np.full((N, D), config.starting_bankroll)
    current = np.full(N, config.starting_bankroll)

    # Track max drawdown per path
    peak = current.copy()
    max_drawdown_pct = np.zeros(N)

    # Track first day each milestone is reached
    milestone_days = np.full((N, len(MILESTONES)), np.nan)

    # Track total trades and total profit for EV calculation
    total_trades = np.zeros(N)
    total_profit = np.zeros(N)

    # Process trades in batches of max_concurrent per day.
    # Each batch = positions open simultaneously (bankroll split between them).
    # Batches within a day are sequential (prior batch resolves first).
    mc = params.max_concurrent
    stop_price = entry * (1.0 - stop_pct)

    for day in range(D):
        trades_remaining = T
        batch_start = 0

        while trades_remaining > 0:
            alive = current > 0
            if not np.any(alive):
                break

            batch_size = min(mc, trades_remaining)
            trades_remaining -= batch_size

            # For concurrent positions, each trade gets 1/batch_size of bankroll
            available = current / batch_size

            batch_pnl = np.zeros(N)

            for slot in range(batch_size):
                tidx = batch_start + slot
                if tidx >= T:
                    break

                contracts = np.zeros(N, dtype=int)
                entry_costs = np.zeros(N)

                for i in range(N):
                    if not alive[i] or available[i] <= 0:
                        continue
                    risk_cap = get_risk_cap(current[i], phases)
                    c = compute_contracts(
                        available[i], entry, effective_wr,
                        params.sizing_strategy, params.kelly_fraction,
                        risk_cap, liquidity,
                    )
                    if c <= 0:
                        continue
                    fee = kalshi_fee(entry, c)
                    cost = entry * c + fee
                    if cost > available[i]:
                        c = int(available[i] / (entry + kalshi_fee(entry, 1)))
                        if c <= 0:
                            continue
                        fee = kalshi_fee(entry, c)
                        cost = entry * c + fee
                    contracts[i] = c
                    entry_costs[i] = cost

                trading = contracts > 0
                if not np.any(trading):
                    continue

                total_trades += trading.astype(float)

                wins = outcomes[:, day, tidx] & trading
                losses = ~outcomes[:, day, tidx] & trading

                # Win: payout = contracts * $1.00 (no exit fee on resolution)
                win_profit = np.where(wins, contracts * 1.0 - entry_costs, 0.0)

                # Loss: exit at stop price, pay exit fee
                loss_proceeds = np.zeros(N)
                loss_indices = np.where(losses)[0]
                for i in loss_indices:
                    c = contracts[i]
                    exit_fee = kalshi_fee(stop_price, c)
                    loss_proceeds[i] = stop_price * c - exit_fee

                loss_pnl = np.where(losses, loss_proceeds - entry_costs, 0.0)
                batch_pnl += win_profit + loss_pnl

            total_profit += batch_pnl
            current += batch_pnl
            current = np.maximum(current, 0.0)

            batch_start += batch_size

        # End of day
        bankrolls[:, day] = current

        peak = np.maximum(peak, current)
        drawdown = np.where(peak > 0, (peak - current) / peak, 0.0)
        max_drawdown_pct = np.maximum(max_drawdown_pct, drawdown)

        for m_idx, milestone in enumerate(MILESTONES):
            newly_reached = (current >= milestone) & np.isnan(milestone_days[:, m_idx])
            milestone_days[newly_reached, m_idx] = day + 1

    # Compute aggregate stats
    finals = bankrolls[:, -1]
    ruined = finals <= 0

    traded_mask = total_trades > 0
    safe_trades = np.where(traded_mask, total_trades, 1.0)
    ev_per_trade_arr = np.where(traded_mask, total_profit / safe_trades, 0.0)

    milestone_results = {}
    for m_idx, attr in enumerate(MILESTONE_ATTRS):
        col = milestone_days[:, m_idx]
        reached = ~np.isnan(col)
        if reached.sum() >= N * 0.5:
            milestone_results[attr] = float(np.nanmedian(col))
        else:
            milestone_results[attr] = None

    return SimResult(
        params=params,
        median_final=float(np.median(finals)),
        mean_final=float(np.mean(finals)),
        p5_final=float(np.percentile(finals, 5)),
        p95_final=float(np.percentile(finals, 95)),
        ev_per_trade=float(np.mean(ev_per_trade_arr[traded_mask])) if np.any(traded_mask) else 0.0,
        probability_of_ruin=float(ruined.mean()),
        max_drawdown_median=float(np.median(max_drawdown_pct)) * 100,
        max_drawdown_p95=float(np.percentile(max_drawdown_pct, 95)) * 100,
        trajectories=bankrolls if store_trajectories else None,
        **milestone_results,
    )


def _run_one(args: tuple[TradeParams, SimConfig]) -> SimResult:
    """Wrapper for multiprocessing — unpacks args tuple."""
    params, config = args
    return simulate_scenario(params, config)


def run_sweep(
    sweep: SweepConfig,
    config: SimConfig,
    max_workers: int | None = None,
) -> list[SimResult]:
    """Run the full parameter sweep with multiprocessing."""
    combos = list(itertools.product(
        sweep.win_rates,
        sweep.entry_prices,
        sweep.stop_loss_pcts,
        sweep.trades_per_day,
        sweep.sizing_strategies,
        sweep.max_concurrents,
        sweep.drift_penalties,
        sweep.liquidities,
    ))

    total = len(combos)
    print(f"Sweeping {total} parameter combos x {config.num_simulations} paths each...")

    tasks = []
    for wr, ep, sl, tpd, strat, mc, dp, liq in combos:
        params = TradeParams(
            win_rate=wr,
            entry_price=ep,
            stop_loss_pct=sl,
            trades_per_day=tpd,
            sizing_strategy=strat,
            max_concurrent=mc,
            drift_penalty=dp,
            liquidity=liq,
        )
        tasks.append((params, config))

    if max_workers is None:
        max_workers = min(os.cpu_count() or 4, 8)

    results: list[SimResult] = []
    done = 0

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        for result in pool.map(_run_one, tasks, chunksize=max(1, total // (max_workers * 4))):
            results.append(result)
            done += 1
            if done % max(1, total // 20) == 0 or done == total:
                pct = done / total * 100
                print(f"  [{done}/{total}] {pct:.0f}% complete", flush=True)

    return results
