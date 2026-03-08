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

## Open Questions
- Is the T+600-800 dist>0.2% edge stable across market regimes? (Need multi-day validation)
- Can edge scale with multiple contracts per trade?
- Can we get access to CF Benchmarks feed directly for XRP?
- Optimal entry within T+600-800 window — enter at first signal or wait for max distance?
