# Polymarket Roadmap

What to do once we have sufficient PM data. Covers analysis, strategy evaluation, and infrastructure.

## Data Collection Status
- **16 collectors running**: 4 coins (BTC, ETH, SOL, XRP) x 4 durations (5m, 15m, 1h, 4h)
- **Data sources per collector**: Coinbase WS (spot), Kraken WS (cross-validation), PM Market WS (book/trades), PM RTDS (Chainlink/Binance resolution price)
- **Output**: `data/rounds/polymarket/{COIN}-{duration}-YYYY-MM-DD.csv`, 1 snapshot/sec + round_end row
- **Columns**: timestamp, slug, condition_id, coin, end_date, seconds_remaining, up/down token IDs, up/down bid/ask, midpoint, spread, last_trade_price/side, volume, spot (Coinbase), kraken, rtds, row_type, outcome

## Milestone 1: Initial Analysis (after ~48h / 500+ rounds per duration)

### Data Quality Checks
- [ ] Are all 16 collectors producing data? Any gaps or crashes?
- [ ] Outcome detection working? (WS `market_resolved` vs fallback)
- [ ] RTDS price vs Coinbase/Kraken — how closely do they track?
- [ ] Round timing — are 5m/15m/1h/4h rounds opening on predictable boundaries?

### Basic Statistics
Build `scripts/analyze_polymarket.py` (similar to `analyze_rounds.py`):
- [ ] Rounds per day per duration — how many rounds are we capturing?
- [ ] Spread distribution by coin and duration — how wide are PM books?
- [ ] Volume distribution — which coin/duration combinations are liquid?
- [ ] Midpoint trajectory — how does up/down pricing evolve within a round?
- [ ] RTDS vs spot divergence — does Chainlink/Binance price differ meaningfully from Coinbase?

### Cross-Platform Comparison (PM vs Kalshi)
- [ ] Resolution correlation — do PM and Kalshi 15m rounds resolve the same way? (Different sources: Chainlink vs CF Benchmarks)
- [ ] Price correlation — PM midpoint vs Kalshi yes_bid at same time points
- [ ] Spread comparison — PM spread vs Kalshi spread at same elapsed times
- [ ] Volume comparison — PM contracts vs Kalshi contracts per round

## Milestone 2: Strategy Evaluation (after ~1 week / 2000+ rounds)

### Pure PM Strategies
Apply same methodology as Kalshi (spot distance from implied strike):
- [ ] Can we derive a "strike" from PM? The up/down binary implies a reference price — check if RTDS at round open serves as strike
- [ ] Spot distance accuracy by time window — replicate the Kalshi analysis for PM
- [ ] Fee-adjusted EV — PM fee structure may differ from Kalshi
- [ ] Liquidity constraints — PM books may be thinner than Kalshi for crypto

### Cross-Platform Arbitrage
- [ ] PM resolves on Chainlink (Binance), Kalshi resolves on CF Benchmarks — are there divergences?
- [ ] Latency: PM outcome visible on-chain before Kalshi settlement?
- [ ] If PM 15m resolves "up" and Kalshi 15m hasn't settled yet, can we buy YES on Kalshi with near-certainty?
- [ ] Fee/slippage analysis — does the arb spread cover both platforms' fees?

### Duration-Specific Opportunities
- [ ] Do longer durations (1h, 4h) have different dynamics? More mean-reversion? Wider spreads?
- [ ] Is there a distance threshold that works for 1h/4h like 0.15% works for Kalshi 15m?
- [ ] Cross-duration signals — does 5m resolution predict 15m/1h outcome?

## Milestone 3: Paper Trading (after strategy validated)

### Infrastructure Needed
- [ ] `shared/clients/polymarket.py` — already has REST client, needs order placement
- [ ] `shared/execution/polymarket.py` — execution engine for PM (CLOB API with signed orders)
- [ ] `bots/polymarket_crypto/` — bot module (discovery, strategy, sizing, main loop)
- [ ] Paper execution engine works already (shared) — just need PM-specific order types
- [ ] PM uses on-chain CLOB with API keys — need to set up API credentials

### Key Differences from Kalshi
- PM has **two tokens** (up/down) vs Kalshi's single order book (yes/no)
- PM orders require **CLOB API signing** (not just API key auth)
- PM settlement is **on-chain** — potentially slower, gas costs for claiming
- PM may have **different fee structure** — maker/taker rates, gas fees
- PM has **no position limits** like Kalshi's $25k/strike

## Milestone 4: Live PM Trading

### Prerequisites
- [ ] 50+ paper trades with positive EV
- [ ] Fee structure fully understood and modeled in sizer
- [ ] USDC funding on Polygon (PM runs on Polygon chain)
- [ ] API credentials and signing set up
- [ ] Risk limits calibrated for PM-specific dynamics

### VPS Considerations
- Same GCP VM can run PM bot alongside Kalshi bot
- PM WS connections are already running (collectors) — bot just adds order execution
- Monitor resource usage — 21 services + PM bot may need larger instance
