# Strategy Evaluation

## Ruled Out (Validated with 109+ rounds)
1. **Yes/No Arbitrage** — single order book, impossible
2. **Spread Farming** — negative EV after fees
3. **Late-Round Snipe T-60s** — contract already at $0.99+, negative P&L
4. **Late-Round Snipe T-120s** — 87-89% win rate but avg P&L is -$0.01 to -$0.03. Contract already priced correctly.
5. **Early Momentum T+0 to T+180s** — 55-65% accuracy, noise. Mean reversion dominates.
6. **Cross-Coin Consensus** — Dissenting coin follows consensus only 57-67%. Not enough after fees. **Strategy abandoned.**
7. **XRP Spot-Based Trading** — Coinbase spot has ZERO predictive power for XRP (46-53% accuracy). CF Benchmarks source differs.
8. **Contract-Confirms-Spot at Mid-Range** — 60-70% win, negative P&L. Fees at $0.50-0.70 entry eat the edge.

## Historical (Invalidated by V3 Analysis)

*Mar 17 V3 analysis (2,529 rounds) found these strategies are negative EV at the ask. The market is well-calibrated: accuracy scales with distance, but so do prices. See `research/v3-analysis-summary.md`.*

9. **Late-Mid-Round Spot Distance (T+600-800, dist>0.2%)** — original strategy (pre-v1)
   - BTC: 48 trades, 100% win, +$2.82
   - ETH: 57 trades, 98.2% win, +$2.81
   - SOL: 55 trades, 98.2% win, +$3.37
   - ~$0.05/trade avg, ~21 trades/day, ~$9/day
   - **V3: negative EV at ask, market already priced correctly at T>600**
10. **ETH Mid-Round (T+300-600, dist>0.2%)** — secondary signal (pre-v1)
    - 49 trades, 91.8% win, +$0.086/trade, +$4.19 total
    - **V3: marginally positive for ETH only, not significant**
11. **bot-v1: T+300-540, dist>0.2%, Kelly 0.25, BTC/ETH/SOL/XRP** — first live version
    - 56 live trades, 82% WR, -$3.91 P&L (thin edge eaten by losses on SOL)
    - Backtest (644 rounds): 84.1% WR, EV/contract +$0.049, BE WR 81.6%
    - SOL dragged results: 79.2% WR vs 88%+ for BTC/ETH
    - **V3: negative EV across all coins at the ask**
12. **bot-v2: T+250-500, dist>0.15%, Kelly 0.30, BTC/ETH/XRP** — Mar 10
    - Backtest (644 rounds, tick-accurate): 83.3% WR, EV/contract +$0.065, BE WR 76.8%
    - Higher EV despite lower WR — cheaper avg price ($0.76 vs $0.80) fattens profit margin
    - 95% CI lower bound (79.2%) is 2.4% above break-even (vs 1.3% for v1)
    - More trades per day = faster compounding
    - Changes: drop SOL, widen window earlier, lower threshold, bump Kelly 25→30%
    - Higher avg PnL per trade but lower win rate
    - **V3: estimated EV=$-0.016/trade across all coins, negative EV**

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
7. **Polymarket trading** — see `research/polymarket-roadmap.md` for analysis plan and strategy ideas
8. **Limit order execution** — Shift from IOC at ask to resting limit orders at bid/mid. Could 2-3x EV per trade. Requires understanding fill rates, queue priority, and cancellation logic on Kalshi.
9. **Calibration-based strategy** — Trade the $0.60-$0.80 miscalibration directly, independent of spot distance signal. Market underprices contracts in this range by 3-6%.

## Resolved / No Longer Relevant
- **Rust execution layer** — NOT needed. Bottleneck is HTTP round trip (~100-200ms), not Python (~0.1ms). Fixed with price cushion + VPS colocation.
- **Fill rate improvement** — Fixed with IOC orders + 2c price cushion + VPS. Fill rate now 92%+.
