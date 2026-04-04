"""Matplotlib charts for simulation results."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from sim.outputs import SimResult


def generate_all_charts(
    results: list[SimResult],
    output_dir: Path,
    single_result: SimResult | None = None,
) -> None:
    """Generate all chart types. Requires matplotlib."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping charts. pip install matplotlib")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    if single_result and single_result.trajectories is not None:
        _plot_growth_curves(single_result, output_dir, plt)
        _plot_drawdown_histogram(single_result, output_dir, plt)

    if results:
        _plot_sensitivity_tornado(results, output_dir, plt)
        _plot_heatmaps(results, output_dir, plt)

    print(f"Charts saved to {output_dir}/")


def _plot_growth_curves(result: SimResult, output_dir: Path, plt) -> None:
    """Percentile bands of bankroll over time."""
    traj = result.trajectories
    if traj is None:
        return

    days = np.arange(1, traj.shape[1] + 1)
    p5 = np.percentile(traj, 5, axis=0)
    p25 = np.percentile(traj, 25, axis=0)
    p50 = np.median(traj, axis=0)
    p75 = np.percentile(traj, 75, axis=0)
    p95 = np.percentile(traj, 95, axis=0)

    fig, ax = plt.subplots(figsize=(12, 7))
    ax.fill_between(days, p5, p95, alpha=0.15, color="blue", label="p5-p95")
    ax.fill_between(days, p25, p75, alpha=0.3, color="blue", label="p25-p75")
    ax.plot(days, p50, color="blue", linewidth=2, label="Median")

    # Milestone lines
    for milestone, label in [
        (1_000, "$1k"), (10_000, "$10k"), (100_000, "$100k"), (1_000_000, "$1M"),
    ]:
        if p95[-1] >= milestone * 0.1:
            ax.axhline(y=milestone, color="gray", linestyle="--", alpha=0.5)
            ax.text(days[-1] * 0.98, milestone * 1.1, label, ha="right", fontsize=9, color="gray")

    ax.set_yscale("log")
    ax.set_xlabel("Days")
    ax.set_ylabel("Bankroll ($)")
    p = result.params
    ax.set_title(
        f"Growth Curves — WR={p.win_rate:.0%} Entry={p.entry_price} "
        f"SL={p.stop_loss_pct:.0%} {p.sizing_strategy} {p.trades_per_day}t/d"
    )
    ax.legend()
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_dir / "growth_curves.png", dpi=150)
    plt.close(fig)


def _plot_drawdown_histogram(result: SimResult, output_dir: Path, plt) -> None:
    """Max drawdown distribution."""
    traj = result.trajectories
    if traj is None:
        return

    # Compute max drawdown per path
    running_max = np.maximum.accumulate(traj, axis=1)
    drawdowns = np.where(running_max > 0, (running_max - traj) / running_max, 0.0)
    max_dd = drawdowns.max(axis=1) * 100

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(max_dd, bins=50, color="red", alpha=0.7, edgecolor="darkred")
    ax.axvline(np.median(max_dd), color="black", linestyle="--", label=f"Median: {np.median(max_dd):.1f}%")
    ax.axvline(np.percentile(max_dd, 95), color="orange", linestyle="--", label=f"p95: {np.percentile(max_dd, 95):.1f}%")
    ax.set_xlabel("Max Drawdown (%)")
    ax.set_ylabel("Count")
    ax.set_title("Max Drawdown Distribution")
    ax.legend()
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_dir / "drawdown_histogram.png", dpi=150)
    plt.close(fig)


def _plot_sensitivity_tornado(results: list[SimResult], output_dir: Path, plt) -> None:
    """Bar chart: which knob has the largest impact on median final bankroll."""
    knobs = [
        ("win_rate", lambda p: p.win_rate),
        ("entry_price", lambda p: p.entry_price),
        ("stop_loss_pct", lambda p: p.stop_loss_pct),
        ("trades_per_day", lambda p: p.trades_per_day),
        ("sizing_strategy", lambda p: p.sizing_strategy),
        ("max_concurrent", lambda p: p.max_concurrent),
        ("drift_penalty", lambda p: p.drift_penalty),
    ]

    impacts = {}
    for knob_name, getter in knobs:
        groups: dict[object, list[float]] = {}
        for r in results:
            val = getter(r.params)
            groups.setdefault(val, []).append(r.median_final)

        if len(groups) < 2:
            continue

        avgs = [sum(v) / len(v) for v in groups.values()]
        impacts[knob_name] = max(avgs) - min(avgs)

    if not impacts:
        return

    sorted_knobs = sorted(impacts.items(), key=lambda x: x[1], reverse=True)
    names = [k for k, _ in sorted_knobs]
    values = [v for _, v in sorted_knobs]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.barh(names, values, color="steelblue")
    ax.set_xlabel("Impact on Median Final Bankroll ($)")
    ax.set_title("Sensitivity Analysis — Which Knob Matters Most")
    ax.grid(True, alpha=0.3, axis="x")

    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + max(values) * 0.01, bar.get_y() + bar.get_height() / 2,
                f"${val:,.0f}", va="center", fontsize=9)

    fig.tight_layout()
    fig.savefig(output_dir / "sensitivity_tornado.png", dpi=150)
    plt.close(fig)


def _plot_heatmaps(results: list[SimResult], output_dir: Path, plt) -> None:
    """Win rate vs entry price heatmap, colored by EV/trade. One per sizing strategy."""
    strategies = set(r.params.sizing_strategy for r in results)

    for strategy in sorted(strategies):
        subset = [r for r in results if r.params.sizing_strategy == strategy]

        # Average EV across other dimensions for each (win_rate, entry_price) cell
        cells: dict[tuple[float, float], list[float]] = {}
        for r in subset:
            key = (r.params.win_rate, r.params.entry_price)
            cells.setdefault(key, []).append(r.ev_per_trade)

        if not cells:
            continue

        win_rates = sorted(set(k[0] for k in cells))
        entry_prices = sorted(set(k[1] for k in cells))

        grid = np.full((len(win_rates), len(entry_prices)), np.nan)
        for (wr, ep), evs in cells.items():
            i = win_rates.index(wr)
            j = entry_prices.index(ep)
            grid[i, j] = sum(evs) / len(evs)

        fig, ax = plt.subplots(figsize=(8, 6))
        im = ax.imshow(grid, aspect="auto", cmap="RdYlGn", origin="lower")
        ax.set_xticks(range(len(entry_prices)))
        ax.set_xticklabels([f"{ep:.2f}" for ep in entry_prices])
        ax.set_yticks(range(len(win_rates)))
        ax.set_yticklabels([f"{wr:.0%}" for wr in win_rates])
        ax.set_xlabel("Entry Price")
        ax.set_ylabel("Win Rate")
        ax.set_title(f"Avg EV/Trade — {strategy}")

        # Annotate cells
        for i in range(len(win_rates)):
            for j in range(len(entry_prices)):
                if not np.isnan(grid[i, j]):
                    ax.text(j, i, f"{grid[i, j]:.4f}", ha="center", va="center", fontsize=8)

        fig.colorbar(im, label="EV per trade ($)")
        fig.tight_layout()
        fig.savefig(output_dir / f"heatmap_{strategy}.png", dpi=150)
        plt.close(fig)
