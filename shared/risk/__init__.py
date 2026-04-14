"""Risk management primitives.

Three independent guards, checked before every order:
    KillSwitch      — file-based + error-based hard stop
    RiskLimits      — per-trade size, exposure, rate limits
    CircuitBreaker  — consecutive losses, daily loss, drawdown

Each guard is in its own module; this package re-exports them so existing
`from shared.risk import ...` imports keep working.
"""

from shared.risk.circuit_breaker import CircuitBreaker
from shared.risk.kill_switch import KillSwitch, KillSwitchTriggered
from shared.risk.limits import RiskCheckResult, RiskLimits

__all__ = [
    "CircuitBreaker",
    "KillSwitch",
    "KillSwitchTriggered",
    "RiskCheckResult",
    "RiskLimits",
]
