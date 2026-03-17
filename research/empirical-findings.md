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

---

## V3 Analysis Findings (Mar 17, 2026 — 2,529 rounds over 9 days)

*Full analysis: `research/v3-data-analysis.md`. Scripts: `scripts/v3_analysis.py`, `scripts/v3_analysis_deep.py`.*

## 13. Across-the-Board EV at the Ask is Negative or Marginal

Tested 170 strategy parameter combinations (7 time windows × 5 distance thresholds × ~5 coin sets). **153 of 170 (90%) are negative EV when buying at the ask price.** The market is well-calibrated: accuracy increases with distance and time, but contract prices increase in lock-step.

Key examples (all coins, buying at ask):
- T+250-500, dist>0.15%: 83.5% acc, med $0.84, EV=$-0.016
- T+250-500, dist>0.20%: 86.4% acc, med $0.87, EV=$-0.016
- T+450-600, dist>0.15%: 91.4% acc, med $0.92, EV=$-0.016
- T+600-800, dist>0.30%: 97.7% acc, med $0.99, EV=$-0.023

The EV is remarkably consistent at about **-$0.02/trade** regardless of the window or threshold — the market always prices in the signal just enough.

## 14. ETH is the Only Coin with Positive EV at the Ask

ETH consistently outperforms other coins by 3-8% accuracy at equivalent distance thresholds:
- ETH T+200-450 d>0.10%: 80.4% acc, ask $0.77, **EV=$+0.014**
- ETH T+200-450 d>0.25%: 88.6% acc, ask $0.86, **EV=$+0.016**
- ETH T+250-500 d>0.20%: 88.2% acc, ask $0.86, **EV=$+0.012**

BTC at equivalent params: T+250-500 d>0.15%: 80.1% acc, ask $0.81, EV=$-0.059. ETH's advantage is real but the mechanism is unclear — possibly ETH market is less efficient due to lower volume (32K vs 313K contracts/round).

**None of these pass a 95% significance test** (best: ETH T+250-500 d>0.20%, 95% CI 81.9%-92.5%, BE=87%).

## 15. Entry Price Matters More Than Signal Quality

The difference between ask and bid entry transforms many strategies from negative to positive EV:
- ETH T+200-450 d>0.10%: ask EV=$+0.014, **bid EV=$+0.044**
- ETH T+250-500 d>0.20%: ask EV=$+0.012, **bid EV=$+0.032**
- BTC T+350-600 d>0.15%: ask EV=$-0.007, **bid EV=$+0.008**

The spread is $0.01 for BTC and $0.03 for ETH/SOL/XRP. Moving from ask to bid saves $0.01-$0.03 per contract — often the difference between profitable and not.

## 16. Market Calibration Sweet Spot at $0.60-$0.80

Updated calibration analysis (T+250-500, 2,487 rounds):
| Implied Prob | Actual WR | Miscalibration | n |
|:--|:--|:--|:--|
| 6.6% | 3.2% | -3.4% | 62 |
| 15.6% | 17.3% | +1.7% | 197 |
| 25.1% | 24.5% | -0.6% | 282 |
| 35.1% | 31.6% | -3.5% | 345 |
| 45.2% | 44.3% | -1.0% | 357 |
| 55.2% | 54.7% | -0.5% | 369 |
| 65.0% | 66.9% | +1.9% | 329 |
| **74.9%** | **80.5%** | **+5.6%** | **272** |
| 84.9% | 87.4% | +2.4% | 190 |
| 93.6% | 95.2% | +1.6% | 84 |

The $0.70-$0.80 range is the biggest exploitable miscalibration. Contracts are systematically underpriced by ~5.6%.

## 17. Other Signals Add No Independent Information

- **Momentum**: 60.6% overall accuracy, scales with magnitude, but collinear with distance
- **Book imbalance**: 70.7% accuracy, scales with confidence, but IS the market price signal
- **Book + distance combined**: Marginally worse than distance alone (filters out correct signals where book is slow)
- **Volume**: No meaningful accuracy difference across volume quartiles
- **Sequential rounds**: 47% repeat previous outcome (slight mean-reversion, not exploitable)
- **Cross-coin correlation**: 58-75% (BTC-ETH highest), confirms shared market moves, not independently useful

## 18. Volatility Regime Matters but is Hard to Exploit

Low-vol rounds: 92.4% signal accuracy (at d>0.15%), but entry prices are lower (med $0.81)
High-vol rounds: 76.4% accuracy, but entry prices are higher (med $0.89)

The market partially prices in volatility — but not perfectly. Low-vol + ETH + d>0.15% showed 89.5% accuracy (n=19, too small). Needs more data.

## 19. Time-of-Day Effects Exist but Are Noisy

Signal accuracy varies from 61% (15:00 UTC) to 97% (18:00 UTC) at T+250-500 d>0.15%. Sample sizes per hour are 18-65 — too small to be actionable. The yes_rate also varies by hour (42% at 02:00 to 64% at 13:00), but this likely reflects crypto market session dynamics rather than anything exploitable.

## 20. Polymarket Data Has Multiple Collector Bugs (VERIFIED 2026-03-17)

**Root cause identified**: Not a token mapping issue (clobTokenIds[0] = "Up" is correct). Three distinct bugs:

1. **`best_bid_ask` misattribution**: WS `best_bid_ask` events have no `asset_id` field → collector routes ALL to UP token bucket → `up_bid`/`up_ask` randomly contain DOWN token values. 88.3% of "real" book episodes last ≤3 snapshots (DOWN token flicker).
2. **UP-only trade tracking**: `last_trade_price` only records UP token trades. DOWN token trades silently dropped → stale prices, 7.5% unknown outcomes (15m), 11% (5m).
3. **`up_midpoint` useless**: 51.6% of snapshots = 0.50 (from 0.01/0.99 book). Even non-0.50 values contaminated by Bug 1.

**What IS reliable**: `last_trade_price` (consistent UP token view), spot prices (all 3 sources), outcomes for resolved rounds (92.5% of 15m).

**Impact on prior PM analysis**: Any finding using `up_bid`, `up_ask`, `up_midpoint`, or `spread` is invalidated. PM miscalibration finding (5-10% in 0.30-0.80 range) used `last_trade_price` which is reliable, but has selection bias from 7.5% unknown outcomes.

Key stats:
- PM 15m: 1,713 rounds, 7.5% unknown outcomes, 50.1% up rate
- PM 5m: 4,596 rounds, 11.0% unknown outcomes, 47.3% up rate
- Cross-platform BTC 15m: **96.2% agreement** (340 matched rounds), 3.8% disagree (CF Benchmarks vs Chainlink resolution)

See `research/data-verification.md` for full verification report.

## 21. bot-v2 Live Trading Assessment

bot-v2 (T+250-500, dist>0.15%, BTC/ETH/XRP, Kelly 0.30) is running since Mar 10:
- The all-coin aggregate EV is estimated at **$-0.016/trade** based on 822 backtest signals
- ETH-only would be positive ($+0.005), but BTC ($-0.059) and XRP drag it negative
- Recommendation: **stop live trading or switch to ETH-only**
