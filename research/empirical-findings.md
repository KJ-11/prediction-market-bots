# Empirical Findings

*Validated with 109+ rounds per coin across 24h collection (Mar 6-7, 2026). Analysis scripts: `scripts/analyze_rounds.py`, `scripts/analyze_liquidity.py`.*

## 1. Contract Repricing Speed (REVISED)
- Market is **faster than originally estimated** — median lag to 0.55/0.45 after crossing is 14-21s (NOT 10 min)
- Mean lag is higher (55-62s) due to occasional slow repricing outliers
- XRP: only 1s median lag — market is extremely fast
- No stickiness at 0.40-0.60 when spot is decisive (>0.5% from strike)

## 2. Spot Price Predictive Power (REVISED)
- Spot prediction accuracy ramps over time: 55-62% at T+60 → 74% at T+300 (ETH) → 80-89% at T+600-780
- **XRP Coinbase spot is USELESS**: 46-53% accuracy at ALL timepoints (coin flip). CF Benchmarks uses different source.
- Contract > 0.50 is predictive for XRP (86% at T+780) — market makers have CF Benchmarks feed
- Spot direction (momentum over 30s/60s) is noise: 40-65% accuracy

## 3. Distance from Strike is the Key Variable
- <0.1% from strike: near-random (47-67% depending on time)
- 0.1-0.5% from strike: 71-94% accurate (time-dependent)
- >0.5% from strike: 100% accurate in all tested windows (small samples, n=3-12)

## 4. Market Calibration Biases (CONFIRMED)
- Contracts priced $0.60-0.70 at T+0-120 win 75.6% (vs expected 65%)
- Late-round (T+600-900) contracts at $0.10-0.20 win 28.7% (vs expected 15%)
- Systematic mispricing at $0.40-0.50 in T+600-900: 58.1% win vs 45% expected (+13%)

## 5. Late-Round (T-120s) — NEGATIVE EV
- Spot accuracy at T-120s: 87-89% for BTC/ETH/SOL, 53% for XRP
- BUT contract is already correctly priced (avg buy at $0.88-0.92)
- **All late-round strategies are negative P&L after fees**
- The 14/14 wins from preliminary data were lucky — with 109 rounds, avg P&L is -$0.01 to -$0.03/trade

## 6. Cross-Leg Arbitrage: Not Possible
- Single order book: `yes_bid + no_ask = $1.00` always

## 7. Spread Farming: Not Viable
- Avg spread 1.45 cents vs fee at $0.50 of 1.75 cents. Negative EV.

## 8. Cross-Coin Consensus: DOES NOT WORK
- Outcome correlation: BTC-ETH 82%, BTC-SOL 81%, BTC-XRP 77%
- Dissenting coin follows consensus: only 57-67% of the time (decays with time)
- **Not a tradeable signal after fees**

## 9. Strike Crossing Persistence
- Early crossings (T+60-300): 48-66% stick. Mean reversion dominates.
- Mid crossings (T+300-600): 72-78% stick. Actionable.
- Late crossings (T+600-800): 67-71% stick.

## 10. Live Trading Results (Mar 8-9, 2026) — STRATEGY FAILED

91 fills across 47 traded rounds over ~36 hours. **34% round win rate, -$64 P&L.**

**Why it failed:**
- 71% of trades entered at 0.20-0.29% distance (minimum threshold) — weakest signals
- 30% of entries at $0.95+ — need 95%+ accuracy, impossible at low distance
- Avg entry price $0.92 — need 92% accuracy to break even, actual ~34%
- Avg win $2.36, avg loss $3.29 — losses bigger than wins (W/L ratio 0.72)
- Max loss streak: 7 consecutive rounds
- Every hour of day was net negative except 3 (02:00, 04:00, 18:00 CST)
- Fill rate was good (92%) — execution wasn't the problem, signal quality was

**Root cause:** At $0.90+ entry prices, the asymmetric payoff (risk $0.90 to make $0.10) requires near-perfect prediction accuracy. Our 0.2% distance threshold doesn't provide that. The strategy was right about direction more often than not, but wrong just often enough to be catastrophically negative EV.

**Implication:** Either need much higher distance thresholds (0.4%+) with much lower entry prices ($0.70-0.85), or a fundamentally different approach to contract selection and pricing.

## 11. V1 Strategy (T+300-540) — Early Results (Mar 9)

Shifted to T+300-540 window with 0.2% distance threshold. Prices are ~$0.70-0.85 (much cheaper than the $0.90+ from v0).

**10 trades over ~3.2 hours:**
- 8W/2L (80% WR) — observed, but small sample
- Net P&L: +$3.02 settled
- Avg entry: $0.77 | Avg win: $0.99 | Avg loss: $2.46
- Per-coin: XRP 3W/0L, SOL 4W/1L, ETH 1W/1L, BTC 0 trades
- Fill rate: 36% (28 signals → 10 fills). 94% of skips = "price too high, no edge"
- Zero liquidity cap events — books are deep (1k-35k contracts at our prices)

**Key tension:** Sizer uses confidence=0.88 from 219-round backtest. Observed WR is 80% from 10 trades. At 80% WR and $0.77 price, Kelly says 0 contracts (no edge after fees). We're profitable but possibly oversized. Need 50+ trades to resolve.

**Compared to v0 (finding #10):** Night and day. v0 entered at $0.92 avg and lost money at 34% round WR. v1 enters at $0.77 avg — much better risk/reward ratio. Even at 80% WR, individual losses are smaller relative to balance.

## 12. Spread by Time
| Coin | 0-120s | 120-300s | 300-600s | 600-900s |
|------|--------|----------|----------|----------|
| BTC  | $0.014 | $0.015 | $0.014 | $0.012 |
| ETH  | $0.031 | $0.022 | $0.022 | $0.020 |
| SOL  | $0.036 | $0.023 | $0.024 | $0.019 |
| XRP  | $0.045 | $0.024 | $0.025 | $0.024 |
