# Open Questions & TODOs

## Exit Management
Currently hold all positions to expiry. Potential improvements:
- **Profit lock-in**: If contract moves significantly in our favor, sell early
- **Stop loss**: Cut losses if contract moves against us past threshold
- **Time-based exit**: Near round close, sell near-breakeven positions to avoid coin-flip
- Need data: how often do contracts at 0.80+ revert? How often do losers recover?

## Persistent Paper Balance
Paper engine resets to $50 on every bot restart, losing P&L history. Save/load balance from disk so cumulative paper performance is tracked across restarts.

## Open Questions
- Is the T+600-800 dist>0.2% edge stable across market regimes? (Need multi-day validation)
- Can edge scale with multiple contracts per trade?
- VPS latency benefit for fill rates?
- Can we get access to CF Benchmarks feed directly for XRP?
- Optimal entry within T+600-800 window — enter at first signal or wait for max distance?
