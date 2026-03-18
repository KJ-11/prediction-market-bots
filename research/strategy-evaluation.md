# Strategy Evaluation

## V3 Analysis Update (Mar 17, 2026 — 2,529 rounds)

13. **bot-v2 (all coins): NEGATIVE EV — stop or modify** (2026-03-17)
    - T+250-500, dist>0.15%, BTC/ETH/XRP: estimated EV=$-0.016/trade across all coins
    - 153 of 170 parameter combos tested are negative EV at the ask
    - The market is well-calibrated: accuracy scales with distance, but so do prices
    - **Recommendation: stop live trading or switch to ETH-only**

14. **ETH-only strategies: marginally positive, not significant** (2026-03-17)
    - ETH T+200-450 d>0.10%: 80.4% acc, ask $0.77, EV=$0.014, 32/day — best daily EV
    - ETH T+250-500 d>0.20%: 88.2% acc, ask $0.86, EV=$0.012, 16/day — best WR
    - ETH T+200-450 d>0.25%: 88.6% acc, ask $0.86, EV=$0.016, 8.8/day — best EV/trade
    - **None are statistically significant at 95%** — need more data (target: 500+ signal events)
    - At bid entry, almost all ETH d>0.15% strategies are positive EV ($0.02-$0.04/trade)

15. **Price-capped entry (T+350-600, ask≤$0.80, all coins): promising** (2026-03-17)
    - 212 signals, 81.1% accuracy, med $0.77, EV=$0.021/trade, 26.5/day
    - Only trades cheap contracts — the $0.60-$0.80 range is miscalibrated by 3-6%
    - Needs validation: are cheap contracts cheap because the market is uncertain (correctly) or because it's slow to update (exploitable)?

16. **Book confirmation adds nothing** (2026-03-17)
    - Distance + book agrees: marginally worse than distance alone
    - The book IS the distance signal, just with lag

17. **Momentum signal: redundant** (2026-03-17)
    - 60.6% overall accuracy, scales with magnitude
    - Entirely collinear with distance from strike — adds no independent information

## Deferred (Future Bots)
1. **AI/ML Models** — train on spot trajectory features once we have 500+ rounds
2. **Whale Detection** — Kalshi exposes full orderbook + trade tape
3. **Cross-Platform: Polymarket to Kalshi** — on-chain trades visible, different resolution sources (Chainlink vs CF Benchmarks). Blocked on PM collector bug (inverted tokens).
4. **Interactive Telegram Bot** — Two-way Telegram bot for status/control (/status, /balance, /stop, /trades)
5. **Order book depth strategy** — Use `yes_bid_size_fp`/`yes_ask_size_fp` from WS and `GET /markets/{ticker}/orderbook` to detect large resting orders, whale positioning, or liquidity vacuums as trading signals
6. **Multi-level order filling** — Walk the book across price levels instead of capping to top-of-book. Not needed until balance exceeds ~$5k.
7. **Polymarket trading** — different resolution source (Chainlink/Binance), systematic miscalibration in 0.30-0.80 range
8. **Limit order execution** — Shift from IOC at ask to resting limit orders at bid/mid. Could 2-3x EV per trade. Requires understanding fill rates, queue priority, and cancellation logic on Kalshi.
9. **Calibration-based strategy** — Trade the $0.60-$0.80 miscalibration directly, independent of spot distance signal. Market underprices contracts in this range by 3-6%.

## Resolved / No Longer Relevant
- **Rust execution layer** — NOT needed. Bottleneck is HTTP round trip (~100-200ms), not Python (~0.1ms). Fixed with price cushion + VPS colocation.
- **Fill rate improvement** — Fixed with IOC orders + 2c price cushion + VPS. Fill rate now 92%+.
