# Empirical Findings

*Validated with 109+ rounds per coin across 24h collection (Mar 6-7, 2026). Full analysis: `data/analysis/ANALYSIS-2026-03-06.md`*

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

## 10. Spread by Time
| Coin | 0-120s | 120-300s | 300-600s | 600-900s |
|------|--------|----------|----------|----------|
| BTC  | $0.014 | $0.015 | $0.014 | $0.012 |
| ETH  | $0.031 | $0.022 | $0.022 | $0.020 |
| SOL  | $0.036 | $0.023 | $0.024 | $0.019 |
| XRP  | $0.045 | $0.024 | $0.025 | $0.024 |
