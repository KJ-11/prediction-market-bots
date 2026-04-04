"""Position sizing strategies for simulation.

Mirrors the Kelly logic from bots/kalshi_crypto/sizing.py but uses floats
for performance in the Monte Carlo hot loop.

Position size is capped by three independent constraints:
1. Sizing strategy output (full_port / half_port / kelly)
2. Risk tolerance (bankroll phase % cap)
3. Liquidity (order book depth at target price)
"""

from __future__ import annotations

from sim.config import BankrollPhase
from sim.fees import kalshi_fee


def get_risk_cap(bankroll: float, phases: tuple[BankrollPhase, ...]) -> float:
    """Max dollars per position based on risk tolerance (bankroll phase)."""
    for phase in phases:
        if phase.floor <= bankroll < phase.ceiling:
            return bankroll * phase.max_bet_pct
    return bankroll * phases[-1].max_bet_pct


def compute_contracts(
    bankroll: float,
    entry_price: float,
    win_rate: float,
    strategy: str,
    kelly_fraction: float,
    risk_cap: float,
    liquidity: float,
) -> int:
    """Compute number of contracts to buy.

    Capped by: min(strategy_size, risk_cap, liquidity) then affordability.
    Returns 0 if the trade isn't affordable or Kelly says don't bet.
    """
    if bankroll <= 0 or entry_price <= 0 or entry_price >= 1.0:
        return 0

    # Strategy-based dollar size (uncapped)
    if strategy == "full_port":
        dollar_size = bankroll
    elif strategy == "half_port":
        dollar_size = bankroll * 0.5
    elif strategy == "fractional_kelly":
        dollar_size = _kelly_dollar_size(bankroll, entry_price, win_rate, kelly_fraction)
    else:
        raise ValueError(f"Unknown sizing strategy: {strategy}")

    if dollar_size <= 0:
        return 0

    # Apply caps: risk tolerance and liquidity
    dollar_size = min(dollar_size, risk_cap, liquidity)

    # Convert dollars to contracts
    fee_per = kalshi_fee(entry_price, 1)
    cost_per = entry_price + fee_per
    if cost_per <= 0:
        return 0

    contracts = int(dollar_size / cost_per)

    # Verify affordability
    if contracts > 0:
        total_cost = entry_price * contracts + kalshi_fee(entry_price, contracts)
        while total_cost > bankroll and contracts > 0:
            contracts -= 1
            total_cost = entry_price * contracts + kalshi_fee(entry_price, contracts)

    return max(contracts, 0)


def _kelly_dollar_size(
    bankroll: float,
    entry_price: float,
    win_rate: float,
    kelly_fraction: float,
) -> float:
    """Fractional Kelly criterion — same math as bots/kalshi_crypto/sizing.py:84-125."""
    fee = kalshi_fee(entry_price, 1)
    cost_per = entry_price + fee
    net_win = 1.0 - entry_price - fee

    if net_win <= 0:
        return 0.0

    b = net_win / cost_per  # odds ratio
    p = win_rate
    q = 1.0 - p

    kelly_f = (p * b - q) / b if b > 0 else 0.0
    kelly_f *= kelly_fraction

    if kelly_f <= 0:
        return 0.0

    return bankroll * kelly_f
