# Open Questions & TODOs

## CRITICAL: V3 Analysis Says Stop Live Trading (2026-03-17)

**Status (2026-03-18):** bot-v2 paused, strategy rework underway. PM collector bugs fixed (2026-03-17). Codebase cleanup in progress — consolidating research, archiving dead code, preparing clean foundation for next strategy iteration.

V3 comprehensive analysis (2,529 rounds, 9 days) found:
- **bot-v2 all-coin EV is negative** (~$-0.016/trade). BTC and XRP drag ETH down.
- **ETH-only is marginally positive** (~$+0.012/trade) but NOT statistically significant
- **153 of 170 tested parameter combos are negative EV at the ask**

**Immediate actions:**
1. ~~Switch to paper trading or kill live bot~~ — DONE, bot-v2 paused
2. ~~Fix PM collector (inverted up/down tokens)~~ — DONE (2026-03-17)
3. Continue collecting data for 2-3 more weeks
4. Re-analyze at 5,000+ rounds per coin

## Confidence Parameter — RESOLVED, MOOT
The 0.88 confidence parameter is irrelevant if the strategy is negative EV. When we resume live trading with a validated strategy, confidence should be set to the observed WR from 500+ signal events, not hardcoded.

## Exit Management
Currently hold all positions to expiry. Potential improvements:
- **Profit lock-in**: If contract moves significantly in our favor, sell early
- **Stop loss**: Cut losses if contract moves against us past threshold
- **Time-based exit**: Near round close, sell near-breakeven positions to avoid coin-flip
- Need data: how often do contracts at 0.80+ revert? How often do losers recover?

## SOL Re-evaluation
- Dropped in v2 (79.2% WR in 644-round backtest, worst coin). Collector still running.
- Re-evaluate after 1000+ SOL rounds of collected data: genuinely worse or small-sample?

## Scaling & Liquidity
Added slippage/ask_size/volume columns to trade log (Mar 9). Once we have ~50-100 fills:
- Slippage per coin — average fill_price - signal_price in cents
- Fill rate per coin — % of IOC orders that get no-filled
- Capacity ceiling — at what position size does slippage eat the edge?

## VPS Optimization
- Actual fill rate on VPS vs laptop baseline?
- Latency to Kalshi from GCP <GCP_ZONE>? (Kalshi likely AWS us-east-1)
- e2-small ($12/month) running 21 Docker services — monitor CPU/memory
- Move to AWS us-east-1 if fill rate still a problem?

## Performance Analytics Enhancements
- Max intraday drawdown tracking
- Running P&L curve (matplotlib)
- Rolling win rate (detect edge decay)
- Per-coin concentration risk metric

## Polymarket
See `research/polymarket-roadmap.md` for the full plan on what to do with PM data.

## Open Questions
- Can edge scale with multiple contracts per trade? Liquidity says yes, need live data
- Can we access CF Benchmarks feed directly for XRP?
- Should confidence be dynamic (rolling WR) instead of hardcoded 0.88?
- v2 window (T+250-500) and threshold (0.15%) based on 3 days of data — validate with more
