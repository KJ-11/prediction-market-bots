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

## Validated & Profitable
9. **Late-Mid-Round Spot Distance (T+600-800, dist>0.2%)** — THE winning strategy
   - BTC: 48 trades, 100% win, +$2.82
   - ETH: 57 trades, 98.2% win, +$2.81
   - SOL: 55 trades, 98.2% win, +$3.37
   - ~$0.05/trade avg, ~21 trades/day, ~$9/day
10. **ETH Mid-Round (T+300-600, dist>0.2%)** — secondary signal
    - 49 trades, 91.8% win, +$0.086/trade, +$4.19 total
    - Higher avg PnL per trade but lower win rate

## Deferred (Future Bots)
11. **AI/ML Models** — train on spot trajectory features once we have 500+ rounds
12. **Whale Detection** — Kalshi exposes full orderbook + trade tape
13. **Cross-Platform: Polymarket to Kalshi** — on-chain trades visible
14. **Interactive Telegram Bot** — Two-way Telegram bot for status/control (/status, /balance, /stop, /trades)
15. **Rust execution layer** — NOT needed for current latency problem. The bottleneck is HTTP round trip to Kalshi API (~100-200ms), not Python processing (~0.1ms). Rust shaves microseconds off strategy evaluation but can't reduce network latency. Becomes relevant only if: (a) competing with other bots on the same WS tick (sub-ms matters), (b) strategy moves to reacting to individual price changes rather than minutes-scale windows, (c) we build a Rust WS client that places orders via WS protocol instead of REST (if Kalshi supports it). Current fix: price cushion + VPS colocation near Kalshi's servers (likely AWS us-east-1) reduces round trip from ~100ms to ~1-5ms — 100x improvement without changing language.
16. **Order book depth strategy** — Use `yes_bid_size_fp`/`yes_ask_size_fp` from WS and `GET /markets/{ticker}/orderbook` to detect large resting orders, whale positioning, or liquidity vacuums as trading signals
17. **Multi-level order filling** — Current liquidity cap uses top-of-book size only (WS `yes_ask_size_fp`). Real depth is massive (2k-190k contracts across price levels). At scale (500+ contracts), should walk the book across multiple price levels instead of capping to top-of-book. Use `GET /markets/{ticker}/orderbook` to compute fillable size across levels. Not needed until balance exceeds ~$5k.
18. **Fill rate improvement (CRITICAL)** — Live orders rest (`status=open`) 100% of the time because the WS price snapshot is stale by the time the HTTP order reaches Kalshi (~100-200ms). The bid/ask moves in that window and our exact-price limit order misses. Root cause analysis:
    - **NOT Python speed** — strategy eval is ~0.1ms, irrelevant
    - **NOT order format** — price conversion is correct (verified)
    - **IS network latency** — HTTP POST round trip ~100-200ms from laptop, bid moves in that time
    - **Fix priority**: (1) Price cushion: add 1-2 cents above ask, costs $0.01-0.02/contract but fills. (2) VPS colocation: AWS us-east-1 near Kalshi reduces round trip to ~1-5ms. (3) Market orders instead of limit (if Kalshi supports). (4) Track fill rate metric to measure improvement.
    - Shadow paper engine tracks theoretical P&L assuming instant fills — compare vs live to quantify the cost of latency.
