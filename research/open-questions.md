# Open Questions & TODOs

## Confidence Parameter Validation — CRITICAL, ONGOING
Strategy hardcodes `confidence=0.88` (from 219-round backtest). Kelly sizer uses this to size positions.
- v2 backtest (644 rounds): 83.3% WR. Kelly produces positive sizes at v2's cheaper avg price ($0.76).
- **The 0.88 confidence is the single biggest assumption in the system.**

**Action plan:**
1. Collect 50+ v2 trades without changing anything
2. At 50 trades, evaluate:
   - WR 85%+: confidence=0.88 justified, deposit to $100, compound
   - WR 80-84%: lower confidence param to match actual WR, reduce sizing
   - WR <80%: stop live, return to paper, investigate
3. Longer term: consider making confidence dynamic (rolling WR from last N trades)

**Simulation results (Mar 9):**
- At 88% true WR: $1,000 in ~5 days, $5,000 in ~7 days
- At 85% true WR: $1,000 in ~7 days, $5,000 in ~10 days
- At 80% true WR: $1,000 in ~29 days, 5% bust risk (oversized for actual edge)

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
