# Open Questions & TODOs

## Exit Management
Currently hold all positions to expiry. Potential improvements:
- **Profit lock-in**: If contract moves significantly in our favor, sell early
- **Stop loss**: Cut losses if contract moves against us past threshold
- **Time-based exit**: Near round close, sell near-breakeven positions to avoid coin-flip
- Need data: how often do contracts at 0.80+ revert? How often do losers recover?

## ~~Persistent Paper Balance~~ — DONE
~~Paper engine resets to $50 on every bot restart, losing P&L history.~~
Implemented: balance persists to `data/paper_balance.json`, loaded on startup.

## VPS / Infrastructure — DEPLOYED
Deployed to GCP VM (<GCP_ZONE>, e2-small). Open sub-questions:
- **Actual fill rate improvement?** — measure fill rate on VPS vs laptop baseline (~100-200ms). Are we seeing fills now or still resting?
- **Latency to Kalshi?** — measure actual round-trip from GCP <GCP_ZONE>. Kalshi likely in AWS us-east-1 (N. Virginia), GCP us-east4 is Ashburn — should be low but unverified.
- **Cost optimization** — e2-small is ~$12/month. Is this the right size? Monitor CPU/memory usage.
- **Should we move to AWS us-east-1?** — if Kalshi is there, same-region would cut latency further. Only worth it if fill rate is still a problem.

## Performance Analytics — Deferred Enhancements
`scripts/performance.py` covers the basics. Future additions:
- **Max intraday drawdown** — track peak-to-trough balance within a day. Requires per-trade balance tracking (fill cost → settlement revenue)
- **Concentration risk metric** — what % of P&L came from one coin? Flag if over-reliant on SOL
- **Running P&L curve** — plot cumulative P&L over time (matplotlib or terminal sparkline)
- **Rolling win rate** — last N trades win rate, detect edge decay
- **Multi-day trend analysis** — daily P&L comparison, good days vs bad days (need more data first)
- **Enhanced Telegram daily report** — replace current basic midnight summary with richer stats from performance.py metrics
- **CI/CD version tagging** — automate `bot-vN` tagging on deploy. Currently manual (`git tag -a bot-v1`)

## Scaling & Liquidity Analysis — Needs Data
Added slippage/ask_size/volume columns to trade log CSV (2026-03-09). Once we have ~50-100 fills with the new columns, build a script to answer:
- **Slippage per coin** — average fill_price - signal_price in cents. Are some coins consistently worse?
- **Fill rate per coin** — what % of IOC orders get no-filled? Trending up = liquidity problem
- **Ask size distribution per coin** — how thin are these books? How often does the liquidity cap bind?
- **Volume per round by coin and time of day** — are some hours/coins structurally thinner?
- **Capacity ceiling estimation** — at what position size does slippage eat the edge? Use real data, not guesses

This analysis determines whether we need per-coin liquidity tiers, volume-aware position caps, or if current approach is fine.

## Open Questions
- Is the T+600-800 dist>0.2% edge stable across market regimes? (Need multi-day validation)
- Can edge scale with multiple contracts per trade?
- Can we get access to CF Benchmarks feed directly for XRP?
- Optimal entry within T+600-800 window — enter at first signal or wait for max distance?
