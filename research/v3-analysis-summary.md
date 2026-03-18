# V3 Analysis Summary (Mar 17, 2026)

*Consolidated from 5 analysis files. Data: 2,529 Kalshi rounds (Mar 8-17, 9 days), 6,412 PM rounds (Mar 10-17).*

## Dataset Overview

| Source | Rounds | Notes |
|:--|:--|:--|
| Kalshi (BTC/ETH/SOL/XRP) | 2,529 | ~631 per coin, ~96/day, 50.2% yes rate |
| PM 15m | 1,713 | 7.5% unknown outcomes, 50.1% up rate |
| PM 5m | 4,596 | 11.0% unknown outcomes, 47.3% up rate |
| PM 4h | 103 | Too few for analysis |

Kalshi spreads: BTC $0.01, ETH/SOL/XRP $0.02-$0.04. BTC volume 313K/round, ETH 32K, SOL/XRP 15-18K.

## Key Findings

### 1. Market is Well-Calibrated at the Ask — No Easy Edge

153/170 strategy parameter combinations (7 time windows x 5 distance thresholds x coin sets) are **negative EV at the ask**. EV is remarkably consistent at about **-$0.02/trade** regardless of window or threshold — the market prices in the signal just enough.

| Strategy (all coins) | Acc | Med Ask | EV/trade |
|:--|:--|:--|:--|
| T+250-500, d>0.15% | 83.5% | $0.84 | -$0.016 |
| T+250-500, d>0.20% | 86.4% | $0.87 | -$0.016 |
| T+450-600, d>0.15% | 91.4% | $0.92 | -$0.016 |
| T+600-800, d>0.30% | 97.7% | $0.99 | -$0.023 |

### 2. ETH is the Only Coin with Positive EV at the Ask

ETH outperforms by 3-8% accuracy at equivalent thresholds, possibly due to lower volume (32K vs 313K for BTC).

| ETH Strategy | Acc | Ask | EV/trade | /day |
|:--|:--|:--|:--|:--|
| T+200-450 d>0.10% | 80.4% | $0.77 | +$0.014 | 32 |
| T+250-500 d>0.20% | 88.2% | $0.86 | +$0.012 | 16 |
| T+200-450 d>0.25% | 88.6% | $0.86 | +$0.016 | 8.8 |

**None pass 95% significance** (best CI: 81.9%-92.5%, BE=87%).

### 3. Entry Price Matters More Than Signal Quality

Bid vs ask entry transforms strategies from negative to positive:

| Strategy | Ask EV | Bid EV | Spread Saved |
|:--|:--|:--|:--|
| ETH T+200-450 d>0.10% | +$0.014 | +$0.044 | $0.03 |
| ETH T+250-500 d>0.20% | +$0.012 | +$0.032 | $0.02 |
| BTC T+350-600 d>0.15% | -$0.007 | +$0.008 | $0.01 |

### 4. $0.60-$0.80 Calibration Sweet Spot

Kalshi contracts at $0.70-$0.80 win 80.5% vs 75% implied — **+5.6% miscalibration** (n=272). Price-capped entry (T+350-600, ask≤$0.80): 212 signals, 81.1% acc, EV=$0.021/trade, 26.5/day. Needs validation.

### 5. Other Signals Add No Independent Information

| Signal | Accuracy | Verdict |
|:--|:--|:--|
| Momentum (60s) | 60.6% | Collinear with distance |
| Book imbalance | 70.7% | IS the market price signal |
| Book + distance | Marginally worse | Filters out correct signals |
| Volume quartiles | No difference | Not useful |
| Sequential rounds | 47% repeat | Slight mean-reversion, not exploitable |
| Cross-coin | 58-75% | Not independently useful |

### 6. Volatility Regimes Matter but Hard to Exploit

Low-vol: 92.4% accuracy (d>0.15%) but med ask $0.81. High-vol: 76.4% accuracy, med ask $0.89. Market partially prices in volatility. Low-vol + ETH + d>0.15%: 89.5% (n=19, too small).

## ML Model Results (GBM)

| Observation Time | OOS AUC | Best OOS Strategy | n | WR | EV |
|:--|:--|:--|:--|:--|:--|
| T+180s | 0.6922 | p>0.90 | 103 | 81.6% | -$0.013 |
| T+300s | 0.7557 | p>0.90 | 150 | 88.0% | +$0.005 |
| T+450s | 0.8465 | p>0.90 | 290 | 92.4% | +$0.014 |

Top features: `yes_mid` (market price) dominates at all timepoints. `pct_dist`, `mom_120s`, `intra_vol` are secondary. **ML adds marginal value over simple rules** — the feature space is thin.

GBM p>0.9 + price cap (ask≤$0.85) at T+450s: n=77, WR=89.6%, EV=$0.082/trade — best combination found, but small OOS sample (2 days).

## Polymarket Analysis

### PM Data Quality (Verified)
- `best_bid_ask` events misattributed between tokens (no asset_id field) — `up_bid`/`up_ask`/`up_midpoint` contaminated
- `last_trade_price` only tracks UP token trades — 7-11% unknown outcomes
- Reliable: `last_trade_price`, spot prices, resolved outcomes

### PM Miscalibration
PM contracts at 0.30-0.80 are systematically underpriced by 5-10% (using `last_trade_price`, which is reliable but has selection bias from unknown outcomes).

### PM Price-Gap Trading (Kalshi vs PM)
When Kalshi and PM disagree, trading PM at T-300s with gap<-5%: n=252, WR=27.8%, EV=$0.064. Positive EV at T-300s and T-180s when Kalshi predicts DOWN but PM is priced UP.

### Cross-Platform Results
- PM mid-round price predicts Kalshi outcome 84.2% (n=1,506)
- When they disagree: Kalshi is right 82.3% of the time
- Cross-platform BTC 15m: 96.2% agreement (340 matched rounds)
- Kalshi does NOT lead PM — 46-48% cross-prediction accuracy

### PM Feasibility Issues
- PM book is sparse: at T-300s, only 35% of rounds have a real DOWN token quote, 0% have real UP token quote
- No "Kalshi > PM" trades found with positive EV — only "Kalshi < PM" direction works

## Creative Strategy Results

### Late-Round "Free Money" (buying favored at $0.90+)
All negative EV. Even at mid≥$0.98 with 120-180s left (100% WR, n=233), EV=$0.00. 24 losses analyzed: median distance 0.109% from strike (close calls). SOL/BTC most loss-prone.

### Contrarian Underdog Betting
Mostly negative EV. One exception: underdog≤$0.10 at 30-60s remaining: 5.8% WR (implied 4%), EV=$0.008 (n=447). Tiny edge, huge variance.

### Spread Capture / Market Making
Not viable. BTC spread ($0.01) < 2x fee ($0.02). ETH spread ($0.03) < 2x fee ($0.02) but adverse selection risk is high.

## Strategy Directions (Ranked)

1. **ETH-only with limit order execution** — highest priority. Current IOC-at-ask is barely positive; limit-at-midpoint could 2-3x EV. Needs live fill rate data.
2. **PM trading at extremes** — PM fees at 0.90+ are 3-10x cheaper than Kalshi. Needs fixed collector data first.
3. **Multi-source signal combination** — Coinbase + Kraken + Binance consensus, RTDS-adjusted distance. Explorable with existing data.
4. **PM miscalibration exploitation** — 5-10% underpricing in 0.30-0.80 range. Needs verification with fixed data.
5. **5m PM as leading indicator for 15m Kalshi** — 3 PM rounds per Kalshi round, resolved outcomes as signals. Data exists.

## What NOT to Pursue

- Cross-coin consensus (57-67%, neg EV)
- Momentum/direction signals (collinear with distance)
- Late-round T>600 strategies (correctly priced at extremes)
- Spread farming (spread < fee)
- Complex ML on Kalshi alone (thin features, no info edge)
- Cross-platform arbitrage (3.8% diverge, unpredictable)
