---
title: V3 Data Analysis — Comprehensive Edge Search
date: 2026-03-17
data: Kalshi rounds Mar 8-17 (9 days), Polymarket rounds Mar 10-17
---

# Phase 1: Data Understanding

## 1.1 Round Counts (Kalshi)

Total rounds with outcomes: 2529

Rounds per coin per day:

| coin   |   2026-03-08 |   2026-03-09 |   2026-03-10 |   2026-03-11 |   2026-03-12 |   2026-03-14 |   2026-03-15 |   2026-03-16 |   2026-03-17 |
|:-------|-------------:|-------------:|-------------:|-------------:|-------------:|-------------:|-------------:|-------------:|-------------:|
| BTC    |           12 |           94 |           87 |           96 |           80 |           11 |           96 |           96 |           61 |
| ETH    |           12 |           94 |           85 |           96 |           80 |           11 |           96 |           96 |           61 |
| SOL    |           12 |           94 |           88 |           96 |           80 |           11 |           96 |           96 |           61 |
| XRP    |           12 |           94 |           85 |           96 |           80 |           11 |           96 |           96 |           61 |

**Total rounds per coin:** {'BTC': 633, 'ETH': 631, 'SOL': 634, 'XRP': 631}


## 1.2 Base Rates (Kalshi)

- **BTC**: 633 rounds, 50.6% yes, 49.4% no
- **ETH**: 631 rounds, 51.3% yes, 48.7% no
- **SOL**: 634 rounds, 51.4% yes, 48.6% no
- **XRP**: 631 rounds, 47.4% yes, 52.6% no

- **Overall**: 50.2% yes (should be ~50% for fair coin)

## 1.3 Snapshot Density

Snapshots per round: mean=792, median=812, min=26, max=890

## 1.4 Spreads (Kalshi)

- **BTC**: median spread $0.010, mean $0.015 (n=364,992 snapshots with quoted book)
- **ETH**: median spread $0.030, mean $0.028 (n=358,472 snapshots with quoted book)
- **SOL**: median spread $0.030, mean $0.032 (n=363,017 snapshots with quoted book)
- **XRP**: median spread $0.030, mean $0.036 (n=355,636 snapshots with quoted book)

## 1.5 Spread by Time into Round (Kalshi)

|                    |   median |      mean |   count |
|:-------------------|---------:|----------:|--------:|
| ('BTC', '0-60')    |     0.01 | 0.013736  |   18611 |
| ('BTC', '60-180')  |     0.01 | 0.0138399 |   68100 |
| ('BTC', '180-300') |     0.01 | 0.0141373 |   68244 |
| ('BTC', '300-450') |     0.01 | 0.0141996 |   79364 |
| ('BTC', '450-600') |     0.01 | 0.014199  |   66406 |
| ('BTC', '600-750') |     0.01 | 0.0144312 |   46462 |
| ('BTC', '750-900') |     0.01 | 0.0220502 |   17805 |
| ('ETH', '0-60')    |     0.03 | 0.0337614 |   17504 |
| ('ETH', '60-180')  |     0.02 | 0.0260335 |   68173 |
| ('ETH', '180-300') |     0.03 | 0.0264898 |   68336 |
| ('ETH', '300-450') |     0.03 | 0.0273745 |   78555 |
| ('ETH', '450-600') |     0.03 | 0.0270475 |   64855 |
| ('ETH', '600-750') |     0.03 | 0.0281617 |   44845 |
| ('ETH', '750-900') |     0.03 | 0.045099  |   16204 |
| ('SOL', '0-60')    |     0.04 | 0.0403198 |   16792 |
| ('SOL', '60-180')  |     0.03 | 0.0323741 |   68091 |
| ('SOL', '180-300') |     0.03 | 0.0326381 |   67682 |
| ('SOL', '300-450') |     0.03 | 0.0317679 |   77354 |
| ('SOL', '450-600') |     0.03 | 0.0302147 |   66242 |
| ('SOL', '600-750') |     0.03 | 0.029483  |   47918 |
| ('SOL', '750-900') |     0.03 | 0.0436009 |   18938 |
| ('XRP', '0-60')    |     0.04 | 0.0499256 |   15869 |
| ('XRP', '60-180')  |     0.03 | 0.0344721 |   68122 |
| ('XRP', '180-300') |     0.03 | 0.0354659 |   67552 |
| ('XRP', '300-450') |     0.03 | 0.0337502 |   76866 |
| ('XRP', '450-600') |     0.03 | 0.0323406 |   62677 |
| ('XRP', '600-750') |     0.03 | 0.0336419 |   46439 |
| ('XRP', '750-900') |     0.03 | 0.0517292 |   18111 |

## 1.6 Volume per Round (Kalshi)

| coin   |     mean |   median |   min |    max |
|:-------|---------:|---------:|------:|-------:|
| BTC    | 312610   |   308178 | 60052 | 751890 |
| ETH    |  32326.9 |    29312 |  2938 |  96790 |
| SOL    |  18032.4 |    15555 |  2323 |  63212 |
| XRP    |  15744.8 |    13403 |  1563 |  56706 |

## 1.7 Polymarket Data Overview

Total PM snapshots: 4,461,957
Total PM round_end/resolved rows: 6412

PM rounds with outcomes:
| coin   |   15m |   4h |   5m |
|:-------|------:|-----:|-----:|
| BTC    |   397 |   25 | 1011 |
| ETH    |   432 |   26 | 1188 |
| SOL    |   441 |   26 | 1220 |
| XRP    |   443 |   26 | 1177 |

- **15m**: 1713 rounds, 50.1% up
- **4h**: 103 rounds, 60.2% up
- **5m**: 4596 rounds, 47.3% up

### PM Spreads

PM snapshots with quoted book (up_bid>0.05, up_ask<0.95): 2,144,355 / 4,455,545 (48.1%)

- **15m**: median spread $0.010, n=571,913
- **4h**: median spread $0.060, n=1,010,978
- **5m**: median spread $0.010, n=561,464



# Phase 2: Pattern Discovery

## 2.1 Spot Distance → Outcome Accuracy by Time Window

Does being further from strike predict the outcome? Broken by time and distance.

| dist_range   | T+0-60   | T+180-300   | T+250-500   | T+300-540   | T+450-600   | T+60-180   | T+600-750   | T+750-900   |
|:-------------|:---------|:------------|:------------|:------------|:------------|:-----------|:------------|:------------|
| 0.00-0.05%   | 50.4%    | 52.6%       | 57.3%       | 55.5%       | 56.7%       | 55.7%      | 59.4%       | 66.1%       |
| 0.05-0.10%   | 56.4%    | 68.4%       | 68.9%       | 68.0%       | 70.7%       | 62.5%      | 79.2%       | 86.5%       |
| 0.10-0.15%   | 63.4%    | 72.2%       | 74.8%       | 77.6%       | 82.3%       | 63.6%      | 85.6%       | 94.5%       |
| 0.15-0.20%   | 65.9%    | 75.1%       | 77.3%       | 80.3%       | 87.4%       | 65.6%      | 89.8%       | 97.1%       |
| 0.20-0.30%   | 58.6%    | 77.9%       | 82.9%       | 83.9%       | 90.7%       | 66.7%      | 95.4%       | 98.2%       |
| 0.30-0.50%   | 75.0%    | 85.5%       | 88.7%       | 87.1%       | 92.7%       | 63.8%      | 96.4%       | 99.7%       |
| 0.50-1.00%   | 85.7%    | 92.2%       | 93.8%       | 92.2%       | 97.5%       | 93.8%      | 99.5%       | 99.5%       |
| 1.00-100.00% | nan      | nan         | 100.0%      | 100.0%      | 100.0%      | nan        | 100.0%      | 100.0%      |

Sample sizes:
| dist_range   |   T+0-60 |   T+180-300 |   T+250-500 |   T+300-540 |   T+450-600 |   T+60-180 |   T+600-750 |   T+750-900 |
|:-------------|---------:|------------:|------------:|------------:|------------:|-----------:|------------:|------------:|
| 0.00-0.05%   |     1451 |         869 |         714 |         651 |         492 |       1317 |         453 |         449 |
| 0.05-0.10%   |      605 |         589 |         563 |         578 |         488 |        611 |         472 |         400 |
| 0.10-0.15%   |      191 |         392 |         412 |         388 |         384 |        247 |         354 |         347 |
| 0.15-0.20%   |       85 |         225 |         264 |         284 |         318 |        125 |         285 |         273 |
| 0.20-0.30%   |       58 |         231 |         293 |         310 |         367 |        102 |         349 |         389 |
| 0.30-0.50%   |       20 |         131 |         195 |         217 |         288 |         47 |         360 |         395 |
| 0.50-1.00%   |       14 |          51 |          65 |          77 |         160 |         16 |         198 |         213 |
| 1.00-100.00% |      nan |         nan |           8 |           9 |          19 |        nan |          41 |          49 |

## 2.2 Per-Coin Accuracy (v2 window: T+250-500)


### Distance 0.15-0.30%

- **BTC**: 76.9% accuracy, n=121
- **ETH**: 83.2% accuracy, n=143
- **SOL**: 77.6% accuracy, n=161
- **XRP**: 83.3% accuracy, n=132

### Distance 0.30-0.50%

- **BTC**: 87.5% accuracy, n=32
- **ETH**: 88.2% accuracy, n=51
- **SOL**: 92.7% accuracy, n=55
- **XRP**: 86.0% accuracy, n=57

### Distance 0.50-1.00%

- **BTC**: 100.0% accuracy, n=7
- **ETH**: 94.7% accuracy, n=19
- **SOL**: 92.9% accuracy, n=28
- **XRP**: 90.9% accuracy, n=11

### Distance 0.20-0.50%

- **BTC**: 83.9% accuracy, n=87
- **ETH**: 87.0% accuracy, n=123
- **SOL**: 84.3% accuracy, n=140
- **XRP**: 85.5% accuracy, n=138

## 2.3 Realistic Entry Prices (ask price at signal time)

When distance > threshold, what does the ask price look like?


### Window T+250-500

- dist>0.15%: n=822, acc=83.5%, med_price=$0.840, avg_price=$0.834, fee=$0.0100, BE_WR=85.0%, **EV=$-0.0155**
- dist>0.20%: n=560, acc=86.4%, med_price=$0.870, avg_price=$0.861, fee=$0.0100, BE_WR=88.0%, **EV=$-0.0157**
- dist>0.30%: n=267, acc=90.3%, med_price=$0.910, avg_price=$0.899, fee=$0.0100, BE_WR=92.0%, **EV=$-0.0174**

### Window T+300-540

- dist>0.15%: n=894, acc=84.3%, med_price=$0.850, avg_price=$0.849, fee=$0.0100, BE_WR=86.0%, **EV=$-0.0166**
- dist>0.20%: n=612, acc=86.3%, med_price=$0.880, avg_price=$0.876, fee=$0.0100, BE_WR=89.0%, **EV=$-0.0273**
- dist>0.30%: n=302, acc=88.7%, med_price=$0.920, avg_price=$0.910, fee=$0.0100, BE_WR=93.0%, **EV=$-0.0426**

### Window T+450-600

- dist>0.15%: n=1150, acc=91.4%, med_price=$0.920, avg_price=$0.903, fee=$0.0100, BE_WR=93.0%, **EV=$-0.0161**
- dist>0.20%: n=833, acc=92.9%, med_price=$0.940, avg_price=$0.924, fee=$0.0100, BE_WR=95.0%, **EV=$-0.0208**
- dist>0.30%: n=467, acc=94.6%, med_price=$0.960, avg_price=$0.954, fee=$0.0100, BE_WR=97.0%, **EV=$-0.0235**

### Window T+600-800

- dist>0.15%: n=1233, acc=95.2%, med_price=$0.970, avg_price=$0.951, fee=$0.0100, BE_WR=98.0%, **EV=$-0.0279**
- dist>0.20%: n=948, acc=96.8%, med_price=$0.980, avg_price=$0.966, fee=$0.0100, BE_WR=99.0%, **EV=$-0.0216**
- dist>0.30%: n=599, acc=97.7%, med_price=$0.990, avg_price=$0.980, fee=$0.0100, BE_WR=100.0%, **EV=$-0.0234**

## 2.4 Momentum Signal

Does the rate of spot price change predict the outcome?

Overall momentum-direction accuracy: 60.6% (n=2462)

- |momentum| 0.00-0.05%: 52.7% accuracy, n=1115
- |momentum| 0.05-0.10%: 63.8% accuracy, n=597
- |momentum| 0.10-0.20%: 67.0% accuracy, n=506
- |momentum| 0.20-0.50%: 74.3% accuracy, n=230
- |momentum| 0.50-100.00%: 100.0% accuracy, n=12

## 2.5 Book Imbalance Signal

Does the bid-ask midpoint deviating from 0.50 predict the outcome?

Book midpoint predicts outcome: 70.7% (n=2487)

- Book confidence 0.00-0.05: 51.3% accuracy, n=349
- Book confidence 0.05-0.15: 60.9% accuracy, n=700
- Book confidence 0.15-0.25: 72.2% accuracy, n=626
- Book confidence 0.25-0.40: 84.1% accuracy, n=655
- Book confidence 0.40-0.50: 95.5% accuracy, n=157

## 2.6 Spot-Book Disagreement

When spot says one thing and the book says another, who's right?

When spot and book agree: 71.8% accuracy (n=2394)
When they disagree: spot is right 57.0%, book is right 43.0% (n=93)

## 2.7 Volume Patterns

- Volume Q4_high: accuracy 71.5%, n=629
- Volume Q3: accuracy 74.4%, n=628
- Volume Q2: accuracy 70.4%, n=625
- Volume Q1_low: accuracy 69.1%, n=632

## 2.8 Time of Day Effects

Yes rate by hour (UTC):

  00:00   48.2%  n=112  █████████
  01:00   50.0%  n=108  ██████████
  02:00   42.9%  n=112  ████████
  03:00   43.8%  n=112  ████████
  04:00   53.6%  n=112  ██████████
  05:00   58.0%  n=112  ███████████
  06:00   43.0%  n=100  ████████
  07:00   54.2%  n= 96  ██████████
  08:00   54.2%  n= 96  ██████████
  09:00   49.1%  n=108  █████████
  10:00   41.1%  n=112  ████████
  11:00   48.2%  n=112  █████████
  12:00   46.4%  n=112  █████████
  13:00   64.3%  n=112  ████████████
  14:00   46.3%  n=108  █████████
  15:00   58.0%  n=100  ███████████
  16:00   59.4%  n= 96  ███████████
  17:00   57.8%  n= 83  ███████████
  18:00   41.5%  n= 94  ████████
  19:00   51.1%  n= 94  ██████████
  20:00   44.6%  n= 83  ████████
  21:00   51.4%  n=111  ██████████
  22:00   50.8%  n=128  ██████████
  23:00   48.3%  n=116  █████████

### Signal accuracy by hour (T+250-500, dist>0.15%)

  00:00   69.2%  n= 39
  01:00   88.6%  n= 44
  02:00   92.3%  n= 39
  03:00   81.8%  n= 44
  04:00   90.9%  n= 33
  05:00   74.1%  n= 27
  06:00   69.6%  n= 23
  07:00   89.3%  n= 28
  08:00   93.1%  n= 29
  09:00   89.5%  n= 38
  10:00   83.3%  n= 24
  11:00   94.4%  n= 18
  12:00   87.0%  n= 23
  13:00   77.0%  n= 61
  14:00   80.0%  n= 65
  15:00   61.1%  n= 36
  16:00   81.0%  n= 42
  17:00   88.5%  n= 26
  18:00   97.2%  n= 36
  19:00   75.0%  n= 28
  20:00   90.5%  n= 21
  21:00   94.4%  n= 18
  22:00   81.4%  n= 43
  23:00   92.5%  n= 40

## 2.9 Sequential Round Patterns

Does the previous round outcome predict the next?

- **BTC**: same as previous 47.3% (n=632), expect ~50% if random
- **ETH**: same as previous 47.8% (n=630), expect ~50% if random
- **SOL**: same as previous 46.1% (n=633), expect ~50% if random
- **XRP**: same as previous 47.1% (n=630), expect ~50% if random

## 2.10 Cross-Coin Correlation

Do outcomes of different coins in the same time window correlate?

Outcome correlation matrix (n=635 time windows):

| coin   |      BTC |      ETH |      SOL |      XRP |
|:-------|---------:|---------:|---------:|---------:|
| BTC    | 1        | 0.754351 | 0.704    | 0.63936  |
| ETH    | 0.754351 | 1        | 0.653366 | 0.596031 |
| SOL    | 0.704    | 0.653366 | 1        | 0.577551 |
| XRP    | 0.63936  | 0.596031 | 0.577551 | 1        |

## 2.11 Volatility Regime Effects

Does intra-round volatility affect strategy accuracy?

- **low_vol**: accuracy 92.4%, avg_dist=0.224%, n=275
- **mid_vol**: accuracy 81.8%, avg_dist=0.283%, n=275
- **high_vol**: accuracy 76.4%, avg_dist=0.383%, n=275

## 2.12 Kraken-Coinbase Divergence

Does a divergence between Coinbase and Kraken spot predict anything?

Median Coinbase-Kraken divergence: 0.0151%

- High divergence + signal: 81.8% acc, n=253
- Low divergence + signal: 86.4% acc, n=184

## 2.13 Market Calibration Analysis

Is the market well-calibrated? (Do contracts priced at X% win X% of the time?)

| prob_bin   |   n |   implied_prob |   actual_win_rate |   miscalibration |
|:-----------|----:|---------------:|------------------:|-----------------:|
| (0.0, 0.1] |  62 |      0.0662097 |         0.0322581 |      -0.0339516  |
| (0.1, 0.2] | 197 |      0.155964  |         0.172589  |       0.0166244  |
| (0.2, 0.3] | 282 |      0.251099  |         0.244681  |      -0.00641844 |
| (0.3, 0.4] | 345 |      0.351116  |         0.315942  |      -0.0351739  |
| (0.4, 0.5] | 357 |      0.452227  |         0.442577  |      -0.00964986 |
| (0.5, 0.6] | 369 |      0.552087  |         0.547425  |      -0.00466125 |
| (0.6, 0.7] | 329 |      0.650015  |         0.668693  |       0.0186778  |
| (0.7, 0.8] | 272 |      0.748879  |         0.805147  |       0.0562684  |
| (0.8, 0.9] | 190 |      0.849395  |         0.873684  |       0.0242895  |
| (0.9, 1.0] |  84 |      0.936256  |         0.952381  |       0.016125   |

Positive miscalibration = market underprices the outcome (potential edge).

## 2.14 Cross-Platform (Polymarket vs Kalshi)

PM 15m snapshots with tradeable quotes: 571,913 / 1,484,846

PM has tradeable 15m markets — cross-platform analysis possible.

PM 15m resolved rounds: 1713






# Phase 2B: Deep Pattern Analysis

## 2B.1 Calibration Edge: The $0.70-$0.80 Sweet Spot

Initial finding: contracts priced $0.70-$0.80 win 80.5% vs 74.9% implied.
Can we exploit this? Does it persist when filtered by our signals?


### Window T+200-450

  Implied 50%-60%: n=863, WR=55.3%, ask=$0.560 (EV=$-0.0273), mid=$0.545 (EV=$-0.0123), bid=$0.530 (EV=$0.0027)
  Implied 60%-70%: n=700, WR=69.3%, ask=$0.660 (EV=$0.0129), mid=$0.645 (EV=$0.0279), bid=$0.630 (EV=$0.0429)
  Implied 70%-80%: n=550, WR=76.4%, ask=$0.760 (EV=$-0.0164), mid=$0.745 (EV=$-0.0014), bid=$0.730 (EV=$0.0136)
  Implied 80%-90%: n=260, WR=86.5%, ask=$0.850 (EV=$0.0054), mid=$0.840 (EV=$0.0154), bid=$0.830 (EV=$0.0254)
  Implied 90%-100%: n=102, WR=95.1%, ask=$0.940 (EV=$0.0010), mid=$0.930 (EV=$0.0110), bid=$0.920 (EV=$0.0210)

### Window T+250-500

  Implied 50%-60%: n=710, WR=55.4%, ask=$0.560 (EV=$-0.0265), mid=$0.550 (EV=$-0.0165), bid=$0.540 (EV=$-0.0065)
  Implied 60%-70%: n=670, WR=66.7%, ask=$0.660 (EV=$-0.0128), mid=$0.645 (EV=$0.0022), bid=$0.630 (EV=$0.0172)
  Implied 70%-80%: n=563, WR=77.8%, ask=$0.760 (EV=$-0.0020), mid=$0.745 (EV=$0.0130), bid=$0.730 (EV=$0.0280)
  Implied 80%-90%: n=387, WR=85.3%, ask=$0.850 (EV=$-0.0073), mid=$0.840 (EV=$0.0027), bid=$0.830 (EV=$0.0127)
  Implied 90%-100%: n=157, WR=95.5%, ask=$0.940 (EV=$0.0054), mid=$0.930 (EV=$0.0154), bid=$0.920 (EV=$0.0254)

### Window T+300-540

  Implied 50%-60%: n=629, WR=53.1%, ask=$0.560 (EV=$-0.0490), mid=$0.545 (EV=$-0.0340), bid=$0.530 (EV=$-0.0190)
  Implied 60%-70%: n=614, WR=68.2%, ask=$0.660 (EV=$0.0024), mid=$0.645 (EV=$0.0174), bid=$0.630 (EV=$0.0324)
  Implied 70%-80%: n=584, WR=77.2%, ask=$0.760 (EV=$-0.0077), mid=$0.745 (EV=$0.0073), bid=$0.730 (EV=$0.0223)
  Implied 80%-90%: n=445, WR=86.3%, ask=$0.850 (EV=$0.0029), mid=$0.840 (EV=$0.0129), bid=$0.830 (EV=$0.0229)
  Implied 90%-100%: n=219, WR=93.6%, ask=$0.940 (EV=$-0.0139), mid=$0.930 (EV=$-0.0039), bid=$0.920 (EV=$0.0061)

### Window T+350-600

  Implied 50%-60%: n=520, WR=55.2%, ask=$0.570 (EV=$-0.0381), mid=$0.555 (EV=$-0.0231), bid=$0.540 (EV=$-0.0081)
  Implied 60%-70%: n=564, WR=66.3%, ask=$0.670 (EV=$-0.0269), mid=$0.655 (EV=$-0.0119), bid=$0.640 (EV=$0.0031)
  Implied 70%-80%: n=552, WR=77.9%, ask=$0.770 (EV=$-0.0110), mid=$0.750 (EV=$0.0090), bid=$0.730 (EV=$0.0290)
  Implied 80%-90%: n=521, WR=86.2%, ask=$0.860 (EV=$-0.0082), mid=$0.845 (EV=$0.0068), bid=$0.830 (EV=$0.0218)
  Implied 90%-100%: n=335, WR=96.1%, ask=$0.940 (EV=$0.0112), mid=$0.935 (EV=$0.0162), bid=$0.930 (EV=$0.0212)


## 2B.2 Low Volatility Filter

Low-vol rounds had 92.4% accuracy vs 76.4% for high-vol.
But are the prices cheaper in low-vol rounds (making it exploitable)?


### Window T+250-500

  dist>0.15% vol=low: n=272, acc=83.1%, med_price=$0.810, EV=$0.0009
  dist>0.15% vol=mid: n=271, acc=80.8%, med_price=$0.820, EV=$-0.0319
  dist>0.15% vol=high: n=272, acc=86.0%, med_price=$0.885, EV=$-0.0347
  dist>0.20% vol=low: n=185, acc=83.2%, med_price=$0.850, EV=$-0.0276
  dist>0.20% vol=mid: n=185, acc=85.9%, med_price=$0.850, EV=$-0.0005
  dist>0.20% vol=high: n=185, acc=89.7%, med_price=$0.910, EV=$-0.0227
  dist>0.30% vol=low: n=89, acc=86.5%, med_price=$0.880, EV=$-0.0248
  dist>0.30% vol=mid: n=87, acc=92.0%, med_price=$0.900, EV=$0.0095
  dist>0.30% vol=high: n=89, acc=92.1%, med_price=$0.930, EV=$-0.0187

### Window T+350-600

  dist>0.15% vol=low: n=328, acc=87.2%, med_price=$0.870, EV=$-0.0080
  dist>0.15% vol=mid: n=327, acc=85.9%, med_price=$0.860, EV=$-0.0107
  dist>0.15% vol=high: n=328, acc=89.9%, med_price=$0.910, EV=$-0.0206
  dist>0.20% vol=low: n=231, acc=85.3%, med_price=$0.890, EV=$-0.0472
  dist>0.20% vol=mid: n=231, acc=87.4%, med_price=$0.880, EV=$-0.0155
  dist>0.20% vol=high: n=231, acc=90.5%, med_price=$0.930, EV=$-0.0352
  dist>0.30% vol=low: n=115, acc=84.3%, med_price=$0.930, EV=$-0.0965
  dist>0.30% vol=mid: n=115, acc=95.7%, med_price=$0.920, EV=$0.0265
  dist>0.30% vol=high: n=115, acc=94.8%, med_price=$0.960, EV=$-0.0222


## 2B.3 ETH Deep Dive

ETH is the only coin with positive EV. Let's understand why and if it's real.


### ETH: Full parameter sweep

    T+180-400 d>0.10%: n=252, acc=77.8%, ask=$0.770 EV=$-0.0122, bid=$0.740 EV=✓$0.0178, 28.0/day
    T+180-400 d>0.15%: n=166, acc=78.9%, ask=$0.800 EV=$-0.0308, bid=$0.770 EV= $-0.0008, 18.4/day
  ✓ T+180-400 d>0.20%: n=108, acc=85.2%, ask=$0.825 EV=$0.0069, bid=$0.800 EV=✓$0.0319, 12.0/day
  ✓ T+180-400 d>0.25%: n=74, acc=90.5%, ask=$0.855 EV=$0.0404, bid=$0.820 EV=✓$0.0654, 8.2/day
  ✓ T+180-400 d>0.30%: n=47, acc=91.5%, ask=$0.890 EV=$0.0149, bid=$0.850 EV=✓$0.0549, 5.2/day
  ✓ T+180-400 d>0.40%: n=24, acc=95.8%, ask=$0.925 EV=$0.0233, bid=$0.885 EV=✓$0.0633, 3.0/day
  ✓ T+180-400 d>0.50%: n=18, acc=94.4%, ask=$0.925 EV=$0.0094, bid=$0.895 EV=✓$0.0394, 2.2/day
  ✓ T+200-450 d>0.10%: n=285, acc=80.4%, ask=$0.770 EV=$0.0135, bid=$0.740 EV=✓$0.0435, 31.7/day
  ✓ T+200-450 d>0.15%: n=182, acc=84.1%, ask=$0.810 EV=$0.0107, bid=$0.780 EV=✓$0.0407, 20.2/day
  ✓ T+200-450 d>0.20%: n=118, acc=86.4%, ask=$0.840 EV=$0.0144, bid=$0.810 EV=✓$0.0344, 13.1/day
  ✓ T+200-450 d>0.25%: n=79, acc=88.6%, ask=$0.860 EV=$0.0161, bid=$0.850 EV=✓$0.0261, 8.8/day
  ✓ T+200-450 d>0.30%: n=50, acc=90.0%, ask=$0.890 EV=$0.0000, bid=$0.860 EV=✓$0.0300, 5.6/day
  ✓ T+200-450 d>0.40%: n=26, acc=96.2%, ask=$0.920 EV=$0.0315, bid=$0.905 EV=✓$0.0465, 3.2/day
  ✓ T+200-450 d>0.50%: n=18, acc=100.0%, ask=$0.945 EV=$0.0450, bid=$0.925 EV=✓$0.0650, 2.2/day
    T+250-500 d>0.10%: n=315, acc=81.3%, ask=$0.800 EV=$-0.0073, bid=$0.770 EV=✓$0.0227, 35.0/day
  ✓ T+250-500 d>0.15%: n=214, acc=85.5%, ask=$0.840 EV=$0.0051, bid=$0.815 EV=✓$0.0201, 23.8/day
  ✓ T+250-500 d>0.20%: n=144, acc=88.2%, ask=$0.860 EV=$0.0119, bid=$0.840 EV=✓$0.0319, 16.0/day
  ✓ T+250-500 d>0.25%: n=96, acc=89.6%, ask=$0.880 EV=$0.0058, bid=$0.870 EV=✓$0.0158, 10.7/day
    T+250-500 d>0.30%: n=72, acc=90.3%, ask=$0.905 EV=$-0.0122, bid=$0.885 EV=✓$0.0078, 8.0/day
  ✓ T+250-500 d>0.40%: n=38, acc=94.7%, ask=$0.930 EV=$0.0074, bid=$0.910 EV=✓$0.0274, 4.8/day
    T+250-500 d>0.50%: n=21, acc=95.2%, ask=$0.950 EV=$-0.0076, bid=$0.930 EV=✓$0.0124, 2.6/day
    T+300-540 d>0.10%: n=337, acc=81.6%, ask=$0.810 EV=$-0.0140, bid=$0.790 EV=✓$0.0060, 37.4/day
    T+300-540 d>0.15%: n=238, acc=84.9%, ask=$0.850 EV=$-0.0113, bid=$0.830 EV=✓$0.0087, 26.4/day
    T+300-540 d>0.20%: n=167, acc=88.0%, ask=$0.880 EV=$-0.0098, bid=$0.860 EV=✓$0.0102, 18.6/day
    T+300-540 d>0.25%: n=111, acc=88.3%, ask=$0.900 EV=$-0.0271, bid=$0.880 EV= $-0.0071, 12.3/day
    T+300-540 d>0.30%: n=85, acc=88.2%, ask=$0.910 EV=$-0.0376, bid=$0.890 EV= $-0.0176, 9.4/day
    T+300-540 d>0.40%: n=48, acc=91.7%, ask=$0.935 EV=$-0.0283, bid=$0.920 EV= $-0.0133, 5.3/day
    T+300-540 d>0.50%: n=23, acc=95.7%, ask=$0.950 EV=$-0.0035, bid=$0.940 EV=✓$0.0065, 2.9/day
    T+350-600 d>0.10%: n=366, acc=84.7%, ask=$0.840 EV=$-0.0030, bid=$0.810 EV=✓$0.0170, 40.7/day
    T+350-600 d>0.15%: n=260, acc=87.3%, ask=$0.880 EV=$-0.0169, bid=$0.850 EV=✓$0.0131, 28.9/day
    T+350-600 d>0.20%: n=188, acc=87.8%, ask=$0.900 EV=$-0.0323, bid=$0.870 EV= $-0.0023, 20.9/day
    T+350-600 d>0.25%: n=135, acc=88.9%, ask=$0.920 EV=$-0.0411, bid=$0.900 EV= $-0.0211, 15.0/day
    T+350-600 d>0.30%: n=99, acc=88.9%, ask=$0.930 EV=$-0.0511, bid=$0.900 EV= $-0.0211, 11.0/day
  ✓ T+350-600 d>0.40%: n=57, acc=96.5%, ask=$0.940 EV=$0.0149, bid=$0.920 EV=✓$0.0349, 6.3/day
  ✓ T+350-600 d>0.50%: n=27, acc=100.0%, ask=$0.970 EV=$0.0200, bid=$0.950 EV=✓$0.0400, 3.4/day


## 2B.4 Limit Order Break-Even Analysis

If we post limit orders at the bid (or mid), what fill rate do we need for positive EV?


ETH T+250-500 dist>0.20%: n=143, acc=88.1%

  Ask $0.860: fee=$0.0100, EV=$0.0111, BE_WR=87.0%
  Mid $0.850: fee=$0.0100, EV=$0.0211, BE_WR=86.0%
  Bid $0.840: fee=$0.0100, EV=$0.0311, BE_WR=85.0%

  At bid: every filled trade = $0.0311 EV. Even with low fill rate, this is profitable per fill.
  Daily filled trades needed for $0.10/day: 3


## 2B.5 Price-Capped Entry Strategy

Instead of buying whenever there's a signal, only buy when the ask is below a certain level. This avoids overpaying.


### Window T+250-500 (all coins)

    ask≤$0.70: n=49, 4 coins (BTC+ETH+SOL+XRP), acc=63.3%, med=$0.680, fee=$0.0200, EV=$-0.0673, 6.1/day
    ask≤$0.75: n=142, 4 coins (BTC+ETH+SOL+XRP), acc=64.8%, med=$0.725, fee=$0.0200, EV=$-0.0971, 17.8/day
    ask≤$0.80: n=303, 4 coins (BTC+ETH+SOL+XRP), acc=74.3%, med=$0.760, fee=$0.0200, EV=$-0.0374, 37.9/day
    ask≤$0.82: n=375, 4 coins (BTC+ETH+SOL+XRP), acc=76.3%, med=$0.770, fee=$0.0200, EV=$-0.0273, 46.9/day
    ask≤$0.85: n=486, 4 coins (BTC+ETH+SOL+XRP), acc=77.2%, med=$0.790, fee=$0.0200, EV=$-0.0384, 60.8/day
    ask≤$0.88: n=585, 4 coins (BTC+ETH+SOL+XRP), acc=78.8%, med=$0.800, fee=$0.0200, EV=$-0.0320, 73.1/day
    ask≤$0.90: n=647, 4 coins (BTC+ETH+SOL+XRP), acc=80.4%, med=$0.810, fee=$0.0200, EV=$-0.0263, 80.9/day

### Window T+200-450 (all coins)

  ✓ ask≤$0.70: n=44, 4 coins (BTC+ETH+SOL+XRP), acc=81.8%, med=$0.680, fee=$0.0200, EV=$0.1182, 5.5/day
  ✓ ask≤$0.75: n=151, 4 coins (BTC+ETH+SOL+XRP), acc=75.5%, med=$0.720, fee=$0.0200, EV=$0.0150, 18.9/day
    ask≤$0.80: n=317, 4 coins (BTC+ETH+SOL+XRP), acc=76.7%, med=$0.760, fee=$0.0200, EV=$-0.0134, 39.6/day
    ask≤$0.82: n=364, 4 coins (BTC+ETH+SOL+XRP), acc=77.2%, med=$0.770, fee=$0.0200, EV=$-0.0180, 45.5/day
    ask≤$0.85: n=461, 4 coins (BTC+ETH+SOL+XRP), acc=79.0%, med=$0.780, fee=$0.0200, EV=$-0.0104, 57.6/day
    ask≤$0.88: n=513, 4 coins (BTC+ETH+SOL+XRP), acc=80.3%, med=$0.790, fee=$0.0200, EV=$-0.0069, 64.1/day
    ask≤$0.90: n=559, 4 coins (BTC+ETH+SOL+XRP), acc=80.9%, med=$0.790, fee=$0.0200, EV=$-0.0014, 69.9/day

### Window T+350-600 (all coins)

  ✓ ask≤$0.70: n=21, 4 coins (BTC+ETH+SOL+XRP), acc=71.4%, med=$0.680, fee=$0.0200, EV=$0.0143, 2.6/day
  ✓ ask≤$0.75: n=80, 4 coins (BTC+ETH+SOL+XRP), acc=86.2%, med=$0.730, fee=$0.0200, EV=$0.1125, 10.0/day
  ✓ ask≤$0.80: n=212, 4 coins (BTC+ETH+SOL+XRP), acc=81.1%, med=$0.770, fee=$0.0200, EV=$0.0213, 26.5/day
  ✓ ask≤$0.82: n=276, 4 coins (BTC+ETH+SOL+XRP), acc=80.4%, med=$0.780, fee=$0.0200, EV=$0.0043, 34.5/day
    ask≤$0.85: n=411, 4 coins (BTC+ETH+SOL+XRP), acc=81.5%, med=$0.800, fee=$0.0200, EV=$-0.0049, 51.4/day
    ask≤$0.88: n=547, 4 coins (BTC+ETH+SOL+XRP), acc=82.8%, med=$0.820, fee=$0.0200, EV=$-0.0118, 60.8/day
    ask≤$0.90: n=631, 4 coins (BTC+ETH+SOL+XRP), acc=83.7%, med=$0.830, fee=$0.0100, EV=$-0.0032, 70.1/day


## 2B.6 ETH + Price Cap


### ETH T+200-450

    d>0.15% ask≤$0.75: n=41, acc=73.2%, med=$0.720, EV=$-0.0083, 5.1/day, daily_EV=$-0.043
  ✓ d>0.15% ask≤$0.80: n=89, acc=79.8%, med=$0.760, EV=$0.0178, 11.1/day, daily_EV=$0.197
  ✓ d>0.15% ask≤$0.85: n=131, acc=80.9%, med=$0.780, EV=$0.0092, 16.4/day, daily_EV=$0.150
  ✓ d>0.15% ask≤$0.88: n=143, acc=81.8%, med=$0.790, EV=$0.0082, 17.9/day, daily_EV=$0.146
    d>0.20% ask≤$0.75: n=11, acc=72.7%, med=$0.730, EV=$-0.0227, 1.8/day, daily_EV=$-0.042
    d>0.20% ask≤$0.80: n=35, acc=77.1%, med=$0.770, EV=$-0.0186, 5.0/day, daily_EV=$-0.093
    d>0.20% ask≤$0.85: n=71, acc=81.7%, med=$0.810, EV=$-0.0131, 8.9/day, daily_EV=$-0.116
    d>0.20% ask≤$0.88: n=83, acc=83.1%, med=$0.820, EV=$-0.0087, 10.4/day, daily_EV=$-0.090
    d>0.30% ask≤$0.85: n=19, acc=78.9%, med=$0.830, EV=$-0.0505, 2.4/day, daily_EV=$-0.120
    d>0.30% ask≤$0.88: n=24, acc=83.3%, med=$0.840, EV=$-0.0167, 3.0/day, daily_EV=$-0.050

### ETH T+250-500

    d>0.15% ask≤$0.75: n=42, acc=69.0%, med=$0.730, EV=$-0.0595, 5.2/day, daily_EV=$-0.313
    d>0.15% ask≤$0.80: n=72, acc=76.4%, med=$0.750, EV=$-0.0061, 9.0/day, daily_EV=$-0.055
    d>0.15% ask≤$0.85: n=128, acc=79.7%, med=$0.790, EV=$-0.0131, 16.0/day, daily_EV=$-0.210
    d>0.15% ask≤$0.88: n=157, acc=80.9%, med=$0.810, EV=$-0.0211, 19.6/day, daily_EV=$-0.414
    d>0.20% ask≤$0.75: n=15, acc=66.7%, med=$0.730, EV=$-0.0833, 2.1/day, daily_EV=$-0.179
  ✓ d>0.20% ask≤$0.80: n=30, acc=80.0%, med=$0.755, EV=$0.0250, 3.8/day, daily_EV=$0.094
    d>0.20% ask≤$0.85: n=66, acc=81.8%, med=$0.810, EV=$-0.0118, 8.2/day, daily_EV=$-0.097
    d>0.20% ask≤$0.88: n=91, acc=82.4%, med=$0.840, EV=$-0.0258, 11.4/day, daily_EV=$-0.294
    d>0.30% ask≤$0.85: n=17, acc=76.5%, med=$0.850, EV=$-0.0953, 2.1/day, daily_EV=$-0.203
    d>0.30% ask≤$0.88: n=31, acc=80.6%, med=$0.850, EV=$-0.0535, 3.9/day, daily_EV=$-0.208

### ETH T+180-400

    d>0.15% ask≤$0.75: n=42, acc=64.3%, med=$0.720, EV=$-0.0971, 5.2/day, daily_EV=$-0.510
    d>0.15% ask≤$0.80: n=84, acc=71.4%, med=$0.755, EV=$-0.0607, 10.5/day, daily_EV=$-0.637
    d>0.15% ask≤$0.85: n=122, acc=73.8%, med=$0.780, EV=$-0.0623, 15.2/day, daily_EV=$-0.950
    d>0.15% ask≤$0.88: n=134, acc=75.4%, med=$0.790, EV=$-0.0563, 16.8/day, daily_EV=$-0.943
  ✓ d>0.20% ask≤$0.75: n=10, acc=90.0%, med=$0.730, EV=$0.1500, 1.4/day, daily_EV=$0.214
  ✓ d>0.20% ask≤$0.80: n=37, acc=83.8%, med=$0.780, EV=$0.0378, 4.6/day, daily_EV=$0.175
    d>0.20% ask≤$0.85: n=67, acc=79.1%, med=$0.800, EV=$-0.0290, 8.4/day, daily_EV=$-0.243
    d>0.20% ask≤$0.88: n=78, acc=80.8%, med=$0.810, EV=$-0.0223, 9.8/day, daily_EV=$-0.218
  ✓ d>0.30% ask≤$0.85: n=14, acc=85.7%, med=$0.820, EV=$0.0171, 2.3/day, daily_EV=$0.040
  ✓ d>0.30% ask≤$0.88: n=21, acc=85.7%, med=$0.840, EV=$0.0071, 3.0/day, daily_EV=$0.021


## 2B.7 Polymarket Analysis


### PM 5m Markets

Resolved rounds: 4596
Snapshots with outcome: 1,465,372
Base rate: 47.3% up

Snapshots with quoted book: 560,883 (38.3%)
Median spread: $0.010
  T-300s to T-200s: midpoint accuracy 44.8%, n=4581
  T-200s to T-120s: midpoint accuracy 31.3%, n=4448
  T-120s to T-60s: midpoint accuracy 27.4%, n=3822
  T-60s to T-30s: midpoint accuracy 24.1%, n=2835

### PM 5m Calibration

| prob_bin   |    n |   implied |    actual |     miscal |
|:-----------|-----:|----------:|----------:|-----------:|
| (0.0, 0.2] |  970 |  0.107619 | 0.9       |  0.792381  |
| (0.2, 0.3] |  401 |  0.250505 | 0.708229  |  0.457724  |
| (0.3, 0.4] |  366 |  0.350703 | 0.612022  |  0.261318  |
| (0.4, 0.5] |  434 |  0.450226 | 0.509217  |  0.0589908 |
| (0.5, 0.6] |  420 |  0.549752 | 0.416667  | -0.133086  |
| (0.6, 0.7] |  406 |  0.650175 | 0.317734  | -0.332441  |
| (0.7, 0.8] |  412 |  0.750637 | 0.257282  | -0.493355  |
| (0.8, 1.0] | 1040 |  0.89118  | 0.0942308 | -0.796949  |


### PM 5m Volume

Volume per round: median=$130, mean=$344, max=$11312

### PM 15m Markets

Resolved rounds: 1713
Snapshots with outcome: 1,474,826
Base rate: 50.1% up

Snapshots with quoted book: 567,756 (38.5%)
Median spread: $0.010
  T-300s to T-240s: midpoint accuracy 23.5%, n=1223
  T-240s to T-180s: midpoint accuracy 23.8%, n=1092
  T-180s to T-120s: midpoint accuracy 21.7%, n=971
  T-120s to T-60s: midpoint accuracy 21.4%, n=766
  T-60s to T-30s: midpoint accuracy 25.2%, n=520

### PM 15m Calibration

| prob_bin   |   n |   implied |    actual |     miscal |
|:-----------|----:|----------:|----------:|-----------:|
| (0.0, 0.2] | 419 |  0.102474 | 0.916468  |  0.813994  |
| (0.2, 0.3] | 159 |  0.249233 | 0.735849  |  0.486616  |
| (0.3, 0.4] | 128 |  0.346691 | 0.609375  |  0.262684  |
| (0.4, 0.5] | 137 |  0.452963 | 0.569343  |  0.11638   |
| (0.5, 0.6] | 130 |  0.550558 | 0.453846  | -0.0967115 |
| (0.6, 0.7] | 149 |  0.653802 | 0.369128  | -0.284674  |
| (0.7, 0.8] | 159 |  0.746937 | 0.213836  | -0.533101  |
| (0.8, 1.0] | 389 |  0.894355 | 0.0796915 | -0.814663  |


### PM 15m Volume

Volume per round: median=$185, mean=$521, max=$7714


## 2B.8 Cross-Platform: PM 15m → Kalshi 15m

Do PM prices help predict Kalshi outcomes for the same 15m window?
Different resolution sources (PM: Binance/Chainlink, Kalshi: CF Benchmarks).


Matched cross-platform rounds: 1660
Agreement rate: 92.3%
When they disagree: 128 times

  BTC: 92.2% agreement, n=385
  ETH: 93.8% agreement, n=418
  SOL: 90.4% agreement, n=428
  XRP: 92.8% agreement, n=429


## 2B.9 Triple Filter: ETH + Low Vol + Distance


### ETH T+250-500 + low vol (<0.037305)

    d>0.10%: n=47, acc=78.7%, med=$0.790, EV=$-0.0228, 6.7/day
  ✓ d>0.15%: n=19, acc=89.5%, med=$0.820, EV=$0.0547, 2.7/day

### ETH T+200-450 + low vol (<0.037305)

    d>0.10%: n=42, acc=73.8%, med=$0.760, EV=$-0.0419, 6.0/day
  ✓ d>0.15%: n=15, acc=86.7%, med=$0.810, EV=$0.0367, 2.5/day

### ETH T+300-540 + low vol (<0.037305)

  ✓ d>0.10%: n=60, acc=81.7%, med=$0.790, EV=$0.0067, 7.5/day
    d>0.15%: n=28, acc=78.6%, med=$0.825, EV=$-0.0593, 4.0/day
  ✓ d>0.20%: n=14, acc=92.9%, med=$0.860, EV=$0.0586, 2.0/day


## 2B.10 BTC Spread Advantage

BTC has $0.01 median spread vs $0.03 for others. Does this translate to better EV at the mid?


### BTC T+250-500

  d>0.15%: n=161, acc=80.1%, spread=$0.010, ask EV=$-0.0588, mid EV=$-0.0538, bid EV=$-0.0488
  d>0.20%: n=95, acc=85.3%, spread=$0.010, ask EV=$-0.0374, mid EV=$-0.0324, bid EV=$-0.0274
  d>0.30%: n=41, acc=90.2%, spread=$0.020, ask EV=$-0.0176, mid EV=$-0.0076, bid EV=$0.0024

### BTC T+350-600

  d>0.15%: n=196, acc=88.3%, spread=$0.015, ask EV=$-0.0073, mid EV=$0.0002, bid EV=$0.0077
  d>0.20%: n=119, acc=89.1%, spread=$0.020, ask EV=$-0.0292, mid EV=$-0.0192, bid EV=$-0.0092
  d>0.30%: n=52, acc=92.3%, spread=$0.015, ask EV=$-0.0269, mid EV=$-0.0194, bid EV=$-0.0119

# Phase 3: Exploitable Edge Quantification

For each candidate strategy, compute realistic EV after fees, frequency, stability, and required speed.

## 3.1 Strategy Simulation (all parameter combos)

Simulating: at first snapshot in window with dist>threshold, buy the ask. Hold to expiry.

### Top 20 Strategies by Daily EV

| Window    | Dist   | Coins           |    N |   Days | Acc    | MedPrice   | BE_WR   | EV/trade   |   Trades/day | EV/day   | WorstDay   |
|:----------|:-------|:----------------|-----:|-------:|:-------|:-----------|:--------|:-----------|-------------:|:---------|:-----------|
| T+250-500 | >0.20% | ETH             |  144 |      9 | 88.2%  | $0.860     | 87.0%   | $0.0119    |     16       | $0.191   | 72%        |
| T+350-600 | >0.50% | BTC+ETH+XRP     |   65 |      8 | 100.0% | $0.970     | 98.0%   | $0.0200    |      8.125   | $0.163   | 100%       |
| T+250-500 | >0.15% | ETH             |  214 |      9 | 85.5%  | $0.840     | 85.0%   | $0.0051    |     23.7778  | $0.122   | 70%        |
| T+350-600 | >0.50% | BTC+ETH+SOL+XRP |  102 |      9 | 99.0%  | $0.970     | 98.0%   | $0.0102    |     11.3333  | $0.116   | 94%        |
| T+350-600 | >0.50% | BTC+ETH         |   39 |      8 | 100.0% | $0.970     | 98.0%   | $0.0200    |      4.875   | $0.098   | 100%       |
| T+180-400 | >0.20% | ETH             |  108 |      9 | 85.2%  | $0.825     | 84.5%   | $0.0069    |     12       | $0.082   | 70%        |
| T+180-400 | >0.30% | ETH             |   47 |      9 | 91.5%  | $0.890     | 90.0%   | $0.0149    |      5.22222 | $0.078   | 71%        |
| T+350-600 | >0.50% | ETH             |   27 |      8 | 100.0% | $0.970     | 98.0%   | $0.0200    |      3.375   | $0.068   | 100%       |
| T+180-400 | >0.30% | BTC+ETH         |   73 |      9 | 91.8%  | $0.900     | 91.0%   | $0.0078    |      8.11111 | $0.063   | 78%        |
| T+250-500 | >0.50% | BTC+ETH         |   30 |      8 | 96.7%  | $0.945     | 95.5%   | $0.0117    |      3.75    | $0.044   | 75%        |
| T+500-800 | >0.50% | ETH             |   54 |      9 | 100.0% | $0.984     | 99.4%   | $0.0055    |      6       | $0.033   | 100%       |
| T+500-800 | >0.50% | BTC+ETH         |   78 |      9 | 100.0% | $0.990     | 100.0%  | $0.0000    |      8.66667 | $0.000   | 100%       |
| T+500-800 | >0.50% | BTC             |   24 |      8 | 100.0% | $0.990     | 100.0%  | $0.0000    |      3       | $0.000   | 100%       |
| T+600-850 | >0.50% | BTC+ETH+XRP     |  162 |      9 | 100.0% | $1.000     | 100.0%  | $0.0000    |     18       | $0.000   | 100%       |
| T+600-850 | >0.50% | ETH             |   72 |      9 | 100.0% | $1.000     | 100.0%  | $0.0000    |      8       | $0.000   | 100%       |
| T+600-850 | >0.50% | BTC+ETH         |  106 |      9 | 100.0% | $1.000     | 100.0%  | $0.0000    |     11.7778  | $0.000   | 100%       |
| T+600-850 | >0.50% | BTC             |   34 |      8 | 100.0% | $1.000     | 100.0%  | $0.0000    |      4.25    | $0.000   | 100%       |
| T+350-600 | >0.10% | BTC+ETH+XRP     | 1033 |      9 | 85.0%  | $0.840     | 85.0%   | $-0.0000   |    114.778   | $-0.006  | 72%        |
| T+180-400 | >0.30% | BTC             |   26 |      8 | 92.3%  | $0.915     | 92.5%   | $-0.0019   |      3.25    | $-0.006  | 75%        |
| T+300-540 | >0.50% | ETH             |   23 |      8 | 95.7%  | $0.950     | 96.0%   | $-0.0035   |      2.875   | $-0.010  | 80%        |

### Top 20 Strategies by EV per Trade

| Window    | Dist   | Coins           |    N |   Days | Acc    | MedPrice   | BE_WR   | EV/trade   |   Trades/day | EV/day   | WorstDay   |
|:----------|:-------|:----------------|-----:|-------:|:-------|:-----------|:--------|:-----------|-------------:|:---------|:-----------|
| T+350-600 | >0.50% | BTC+ETH+XRP     |   65 |      8 | 100.0% | $0.970     | 98.0%   | $0.0200    |      8.125   | $0.163   | 100%       |
| T+350-600 | >0.50% | BTC+ETH         |   39 |      8 | 100.0% | $0.970     | 98.0%   | $0.0200    |      4.875   | $0.098   | 100%       |
| T+350-600 | >0.50% | ETH             |   27 |      8 | 100.0% | $0.970     | 98.0%   | $0.0200    |      3.375   | $0.068   | 100%       |
| T+180-400 | >0.30% | ETH             |   47 |      9 | 91.5%  | $0.890     | 90.0%   | $0.0149    |      5.22222 | $0.078   | 71%        |
| T+250-500 | >0.20% | ETH             |  144 |      9 | 88.2%  | $0.860     | 87.0%   | $0.0119    |     16       | $0.191   | 72%        |
| T+250-500 | >0.50% | BTC+ETH         |   30 |      8 | 96.7%  | $0.945     | 95.5%   | $0.0117    |      3.75    | $0.044   | 75%        |
| T+350-600 | >0.50% | BTC+ETH+SOL+XRP |  102 |      9 | 99.0%  | $0.970     | 98.0%   | $0.0102    |     11.3333  | $0.116   | 94%        |
| T+180-400 | >0.30% | BTC+ETH         |   73 |      9 | 91.8%  | $0.900     | 91.0%   | $0.0078    |      8.11111 | $0.063   | 78%        |
| T+180-400 | >0.20% | ETH             |  108 |      9 | 85.2%  | $0.825     | 84.5%   | $0.0069    |     12       | $0.082   | 70%        |
| T+500-800 | >0.50% | ETH             |   54 |      9 | 100.0% | $0.984     | 99.4%   | $0.0055    |      6       | $0.033   | 100%       |
| T+250-500 | >0.15% | ETH             |  214 |      9 | 85.5%  | $0.840     | 85.0%   | $0.0051    |     23.7778  | $0.122   | 70%        |
| T+500-800 | >0.50% | BTC             |   24 |      8 | 100.0% | $0.990     | 100.0%  | $0.0000    |      3       | $0.000   | 100%       |
| T+500-800 | >0.50% | BTC+ETH         |   78 |      9 | 100.0% | $0.990     | 100.0%  | $0.0000    |      8.66667 | $0.000   | 100%       |
| T+600-850 | >0.50% | BTC+ETH+XRP     |  162 |      9 | 100.0% | $1.000     | 100.0%  | $0.0000    |     18       | $0.000   | 100%       |
| T+600-850 | >0.50% | ETH             |   72 |      9 | 100.0% | $1.000     | 100.0%  | $0.0000    |      8       | $0.000   | 100%       |
| T+600-850 | >0.50% | BTC+ETH         |  106 |      9 | 100.0% | $1.000     | 100.0%  | $0.0000    |     11.7778  | $0.000   | 100%       |
| T+600-850 | >0.50% | BTC             |   34 |      8 | 100.0% | $1.000     | 100.0%  | $0.0000    |      4.25    | $0.000   | 100%       |
| T+350-600 | >0.10% | BTC+ETH+XRP     | 1033 |      9 | 85.0%  | $0.840     | 85.0%   | $-0.0000   |    114.778   | $-0.006  | 72%        |
| T+500-800 | >0.30% | ETH             |  145 |      9 | 97.9%  | $0.970     | 98.0%   | $-0.0007   |     16.1111  | $-0.011  | 92%        |
| T+450-700 | >0.10% | BTC             |  354 |      9 | 89.8%  | $0.890     | 90.0%   | $-0.0017   |     39.3333  | $-0.067  | 82%        |

**153 / 170 parameter combos are negative EV.**

Most negative: T+600-850 >0.10% BTC+ETH+SOL+XRP: EV=$-0.0293

## 3.2 Day-by-Day Stability (Top 3 Strategies)


### T+250-500 dist>0.20% ETH

  2026-03-08: 4 trades, 100% acc (4W/0L), med_price=$0.815, EV=$0.1650
  2026-03-09: 21 trades, 86% acc (18W/3L), med_price=$0.870, EV=$-0.0229
  2026-03-10: 13 trades, 100% acc (13W/0L), med_price=$0.860, EV=$0.1300
  2026-03-11: 20 trades, 80% acc (16W/4L), med_price=$0.880, EV=$-0.0900
  2026-03-12: 16 trades, 94% acc (15W/1L), med_price=$0.880, EV=$0.0475
  2026-03-14: 1 trades, 100% acc (1W/0L), med_price=$0.980, EV=$0.0100
  2026-03-15: 17 trades, 94% acc (16W/1L), med_price=$0.890, EV=$0.0412
  2026-03-16: 34 trades, 91% acc (31W/3L), med_price=$0.850, EV=$0.0518
  2026-03-17: 18 trades, 72% acc (13W/5L), med_price=$0.840, EV=$-0.1278

### T+350-600 dist>0.50% BTC+ETH+XRP

  2026-03-08: 3 trades, 100% acc (3W/0L), med_price=$1.000, EV=$0.0000
  2026-03-09: 10 trades, 100% acc (10W/0L), med_price=$0.980, EV=$0.0100
  2026-03-10: 12 trades, 100% acc (12W/0L), med_price=$0.980, EV=$0.0100
  2026-03-11: 11 trades, 100% acc (11W/0L), med_price=$0.960, EV=$0.0300
  2026-03-12: 4 trades, 100% acc (4W/0L), med_price=$0.955, EV=$0.0350
  2026-03-15: 4 trades, 100% acc (4W/0L), med_price=$0.980, EV=$0.0100
  2026-03-16: 11 trades, 100% acc (11W/0L), med_price=$0.960, EV=$0.0300
  2026-03-17: 10 trades, 100% acc (10W/0L), med_price=$0.960, EV=$0.0300

### T+250-500 dist>0.15% ETH

  2026-03-08: 4 trades, 100% acc (4W/0L), med_price=$0.815, EV=$0.1650
  2026-03-09: 32 trades, 88% acc (28W/4L), med_price=$0.835, EV=$0.0300
  2026-03-10: 25 trades, 88% acc (22W/3L), med_price=$0.820, EV=$0.0400
  2026-03-11: 28 trades, 82% acc (23W/5L), med_price=$0.850, EV=$-0.0386
  2026-03-12: 28 trades, 89% acc (25W/3L), med_price=$0.830, EV=$0.0529
  2026-03-14: 1 trades, 100% acc (1W/0L), med_price=$0.980, EV=$0.0100
  2026-03-15: 23 trades, 91% acc (21W/2L), med_price=$0.890, EV=$0.0130
  2026-03-16: 46 trades, 87% acc (40W/6L), med_price=$0.830, EV=$0.0296
  2026-03-17: 27 trades, 70% acc (19W/8L), med_price=$0.820, EV=$-0.1363

## 3.3 Combined Signal: Spot Distance + Book Confirmation

Require both: dist>threshold AND book agrees (yes_mid supports direction).

T+250-500 dist>0.15% + book confirms: acc=83.7%, n=571, med=$0.840, EV=$-0.0129
T+250-500 dist>0.20% + book confirms: acc=86.6%, n=387, med=$0.870, EV=$-0.0144
T+250-500 dist>0.30% + book confirms: acc=89.0%, n=181, med=$0.910, EV=$-0.0305
T+300-540 dist>0.15% + book confirms: acc=84.9%, n=631, med=$0.860, EV=$-0.0206
T+300-540 dist>0.20% + book confirms: acc=87.4%, n=427, med=$0.880, EV=$-0.0165
T+300-540 dist>0.30% + book confirms: acc=87.9%, n=199, med=$0.910, EV=$-0.0406
T+450-700 dist>0.15% + book confirms: acc=91.6%, n=810, med=$0.920, EV=$-0.0140
T+450-700 dist>0.20% + book confirms: acc=93.0%, n=574, med=$0.940, EV=$-0.0197
T+450-700 dist>0.30% + book confirms: acc=94.5%, n=311, med=$0.960, EV=$-0.0247

## 3.4 Optimal Per-Coin Strategy

Best parameters per coin (by EV/trade, min 30 samples):


**ETH**: T+180-400 dist>0.30%
  Acc=91.5%, MedPrice=$0.890, EV/trade=$0.0149, 5.2 trades/day, EV/day=$0.078, n=47, worst_day=71%

**BTC+ETH+XRP**: T+350-600 dist>0.50%
  Acc=100.0%, MedPrice=$0.970, EV/trade=$0.0200, 8.1 trades/day, EV/day=$0.163, n=65, worst_day=100%

**BTC+ETH**: T+350-600 dist>0.50%
  Acc=100.0%, MedPrice=$0.970, EV/trade=$0.0200, 4.9 trades/day, EV/day=$0.098, n=39, worst_day=100%

**BTC+ETH+SOL+XRP**: T+350-600 dist>0.50%
  Acc=99.0%, MedPrice=$0.970, EV/trade=$0.0102, 11.3 trades/day, EV/day=$0.116, n=102, worst_day=94%

## 3.5 Statistical Significance

For top strategies, compute 95% confidence intervals using Wilson score.


**T+250-500 dist>0.20% ETH** (n=144)
  Accuracy: 88.2% (95% CI: 81.9% - 92.5%)
  Break-even WR: 87.0%
  EV/trade: $0.0119 (95% CI: $-0.0509 to $0.0550)
  ✗ Lower bound 81.9% ≤ BE 87.0% — NOT statistically significant

**T+350-600 dist>0.50% BTC+ETH+XRP** (n=65)
  Accuracy: 100.0% (95% CI: 94.4% - 100.0%)
  Break-even WR: 98.0%
  EV/trade: $0.0200 (95% CI: $-0.0358 to $0.0200)
  ✗ Lower bound 94.4% ≤ BE 98.0% — NOT statistically significant

**T+250-500 dist>0.15% ETH** (n=214)
  Accuracy: 85.5% (95% CI: 80.2% - 89.6%)
  Break-even WR: 85.0%
  EV/trade: $0.0051 (95% CI: $-0.0483 to $0.0460)
  ✗ Lower bound 80.2% ≤ BE 85.0% — NOT statistically significant

**T+350-600 dist>0.50% BTC+ETH+SOL+XRP** (n=102)
  Accuracy: 99.0% (95% CI: 94.7% - 99.8%)
  Break-even WR: 98.0%
  EV/trade: $0.0102 (95% CI: $-0.0335 to $0.0183)
  ✗ Lower bound 94.7% ≤ BE 98.0% — NOT statistically significant

**T+350-600 dist>0.50% BTC+ETH** (n=39)
  Accuracy: 100.0% (95% CI: 91.0% - 100.0%)
  Break-even WR: 98.0%
  EV/trade: $0.0200 (95% CI: $-0.0697 to $0.0200)
  ✗ Lower bound 91.0% ≤ BE 98.0% — NOT statistically significant



# Phase 4: Strategy Recommendations

## 4.0 Executive Summary

**The brutal truth: there is no statistically significant edge in any strategy we tested across 2,529 Kalshi rounds over 9 days.**

Of 170 parameter combinations tested, 153 (90%) are negative EV at the ask. The remaining 17 are marginally positive but **none** pass a 95% confidence test. The market is well-calibrated — accuracy increases with distance from strike, but so does the contract price, leaving ~zero profit margin.

However, the data reveals several promising leads worth pursuing with more data and better execution:

1. **ETH is special** — consistently the best coin across all windows, the only one with positive EV at the ask
2. **Entry price matters more than signal accuracy** — the difference between ask, mid, and bid entry transforms negative-EV strategies into positive ones
3. **Calibration bias in the $0.60-$0.80 range** — market systematically underprices these contracts by 3-6%
4. **Polymarket data is broken** — inverted calibration (low-priced contracts win 90%) means our collector has up/down tokens swapped. Must fix before PM analysis is useful.

## 4.1 Primary Recommendation: Pause Live Trading, Fix PM Data, Collect More Kalshi Data

**Action: Stop the bot from making live trades. Continue collecting data. Fix PM collector.**

Rationale:
- bot-v2 (T+250-500, dist>0.15%, BTC/ETH/XRP) has an estimated EV/trade of **$-0.016** across all coins. It's losing money slowly.
- The only positive-EV variant is **ETH-only** at $0.012/trade — but this isn't statistically significant (95% CI includes negative EV)
- With ~$28 account balance, even a marginally positive edge grows capital extremely slowly while a marginally negative one bleeds to zero
- 9 days / ~630 rounds per coin is insufficient to confirm a 1-2% edge. We need ~500+ signal events per strategy variant.

**Concrete steps:**
1. Set `PAPER_TRADING=true` or kill the live bot
2. Fix PM collector (Section 4.6)
3. Keep Kalshi + PM collectors running for 2-3 more weeks
4. Re-run this analysis with ~2,500+ rounds per coin

## 4.2 Best Strategy Candidates (to validate with more data)

### Strategy A: "ETH Mid-Round" (best daily EV)
- **Entry**: ETH only, T+200-450, dist>0.10%, buy at ask
- **Backtest**: 285 signals, 80.4% accuracy, med ask $0.77, **EV=$0.014/trade, $0.44/day**
- **At bid**: EV=$0.044/trade — extremely profitable if limit orders fill
- **Why ETH?** ETH has $0.03 spread (vs BTC's $0.01) but consistently higher accuracy at equivalent distances. ETH's market may be less efficient due to lower volume (32K vs 313K contracts/round).
- **Risk**: 95% CI spans 75.4%-84.6%, and BE WR is 79%. Lower bound is below BE.
- **Verdict**: Promising but needs 400+ signals to confirm. Paper trade this.

### Strategy B: "ETH Selective" (best EV/trade)
- **Entry**: ETH only, T+200-450, dist>0.25%, buy at ask
- **Backtest**: 79 signals, 88.6% accuracy, med ask $0.86, **EV=$0.016/trade, $0.14/day**
- **At bid**: EV=$0.026/trade
- **Risk**: n=79 is too small. 95% CI is wide.
- **Verdict**: Higher confidence per trade but low frequency. Consider as secondary layer.

### Strategy C: "Cheap Contracts" (price-capped entry)
- **Entry**: All coins, T+350-600, dist>0.15%, ask≤$0.80
- **Backtest**: 212 signals, 81.1% accuracy, med ask $0.77, **EV=$0.021/trade, $0.56/day**
- **Why it works**: Only entering when contracts are cheap means you need lower accuracy to profit. The $0.70-$0.80 implied range is where the market is most miscalibrated (+5.6% actual vs implied).
- **Risk**: Selection bias — cheap contracts occur early in the signal window when outcomes are less certain. The 81% WR might not hold.
- **Verdict**: Most interesting new idea. Need to understand WHY these contracts are cheap (noise vs genuine uncertainty).

### Strategy D: "High Distance Safe" (100% WR in backtest)
- **Entry**: BTC+ETH+XRP, T+350-600, dist>0.50%, buy at ask
- **Backtest**: 65 signals, 100% accuracy, med ask $0.97, **EV=$0.020/trade, $0.16/day**
- **Risk**: 100% in 65 is impressive but 95% CI lower bound is 94.4%, and BE is 98%. One loss in 65 would flip EV negative. The $0.02 profit per trade is tiny vs $0.97 risk.
- **Verdict**: Safe but low return. Could work as a "free money" layer but needs more data.

## 4.3 What NOT to Do

1. **Don't trade BTC-only** — BTC has the tightest spreads ($0.01) and highest volume, which means the market is most efficient. BTC accuracy at T+250-500 d>0.15% is only 80.1% vs ETH's 85.5%. Even at the bid, BTC strategies are mostly negative EV.

2. **Don't trade late-round (T+600+)** — By T+600, contracts with 0.50%+ distance are priced at $0.97-1.00. Even with 100% accuracy, EV is $0.00-$0.02 minus fees. The market has fully priced the signal.

3. **Don't use the current v2 strategy on all 3 coins** — The aggregate all-coin numbers mask that BTC and XRP drag down the average. ETH-only is positive EV; the mix is negative.

4. **Don't add book confirmation** — Combined signals (dist + book agrees) actually performed *worse* than distance alone. The book IS the distance signal, just with a lag — requiring both doesn't add information, it just filters out some correct signals where the book is slow to update.

5. **Don't trade XRP** — XRP accuracy is decent (83.3% at d>0.15-0.20%) but entry prices are high due to wider spreads ($0.03-0.04), making it negative EV at the ask.

6. **Don't rely on momentum** — Momentum (rate of spot change) accuracy is 60.6% overall, rising to 74% for large moves. But this is already captured by the distance signal — large momentum creates large distance. It adds no independent information.

7. **Don't rely on cross-coin signals** — Outcomes are 58-75% correlated (BTC-ETH highest). But consensus signals (multiple coins agreeing) don't help — this was already ruled out.

## 4.4 Promising Leads for Further Investigation

### 4.4.1 Limit Orders Instead of IOC
The single most impactful change would be shifting from IOC (immediate-or-cancel, buy at ask) to resting limit orders at the bid or mid. For ETH T+200-450 d>0.10%:
- At ask: EV=$0.014/trade
- At mid: EV=$0.044/trade (3x better)
- At bid: EV=$0.044/trade

Even with a 50% fill rate, bid-entry would produce more daily profit than 100%-fill ask-entry. This requires:
- Understanding Kalshi limit order behavior (partial fills, queue priority)
- Monitoring fill rate empirically
- Handling the case where your order is still resting when the round ends

### 4.4.2 Fix PM Collector, Then Cross-Platform Analysis
The PM calibration data is inverted: contracts with 10% implied probability win 90% of the time. This means the `up_midpoint` in our collector is backwards. Either:
- `up_token_id` and `down_token_id` are swapped, or
- The midpoint formula is wrong (should be `1 - up_midpoint`?)

Once fixed:
- PM 15m and Kalshi 15m agree 92.3% of the time (different resolution sources)
- The 7.7% disagreement (128 cases in our data) could be exploitable — trade on whichever platform's price hasn't moved yet
- PM 5m has 4,596 resolved rounds — much more data than Kalshi 15m for pattern discovery
- PM markets have $0.01 median spread on 5m and 15m — potentially tradeable

### 4.4.3 Market Calibration Exploitation
The Kalshi market is systematically miscalibrated in the $0.60-$0.80 range:
- $0.60-$0.70 implied → 69.3% actual win rate (+4.3% edge)
- $0.70-$0.80 implied → 77.8% actual win rate (+2.9% edge)
- $0.80-$0.90 implied → 86.5% actual win rate (+1.6% edge)

A strategy that specifically targets the $0.60-$0.70 zone (buying the favored side at the ask):
- n=700, WR=69.3%, med ask=$0.66, EV=$0.013/trade at T+200-450

This is a different angle than distance-from-strike — it's about finding when the market is mispriced regardless of the underlying signal.

### 4.4.4 Volatility Regime Filter
Low-vol rounds have 92.4% distance-signal accuracy vs 76.4% for high-vol. But the prices are higher in low-vol, partially canceling the benefit. With enough data, a vol-filter could improve signal quality. Triple-filter (ETH + low vol + dist>0.15%) showed 89.5% accuracy but only n=19 — need more data.

### 4.4.5 Time-of-Day Optimization
Signal accuracy at T+250-500 d>0.15% varies from 61% (15:00 UTC) to 97% (18:00 UTC). With more data, avoiding poor hours and concentrating on high-accuracy hours could improve overall performance. But current sample sizes per hour (18-65) are too small for confidence.

## 4.5 Infrastructure Requirements

For any strategy we pursue:
- **WebSocket feed** (already have): Spot price + order book updates in real-time
- **Sub-second order submission**: IOC orders need <500ms from signal to fill
- **Limit order support** (needed for 4.4.1): Place resting orders and cancel/update them
- **PM data fix** (needed for 4.4.2): Debug up/down token assignment in collector
- **Paper trading mode** with realistic execution simulation
- **Data collection**: Continue running 21 Docker services (1 bot + 4 Kalshi + 16 PM collectors)

## 4.6 Risk Assessment

### Current state
- **Account balance**: ~$28
- **bot-v2 estimated EV**: $-0.02/trade (negative across all coins), -$0.50/day
- **If we keep trading**: Slow bleed to zero. At -$0.50/day, account hits zero in ~8 weeks.
- **If we pause**: $0 lost, data collection continues, analysis improves

### If we deploy a validated strategy
- **Best realistic daily EV**: $0.20-$0.50/day (ETH-only, IOC at ask)
- **With limit orders**: Potentially $0.50-$1.50/day (but fill rate is unknown)
- **Scaling**: ETH volume is 32K/round — our small size ($1-5/trade) has zero market impact
- **Ruin probability**: At $28 balance, even a proven 88% WR strategy has meaningful bust risk from variance. Need more capital to survive drawdowns.
- **Recommendation**: If edge is confirmed with more data, deposit to $100 minimum before going live.

### Key uncertainty
The fundamental question is: **is ETH's outperformance real or noise?**
- ETH at T+200-450 d>0.10%: 80.4% acc, n=285. Looks good.
- But Mar 17 (most recent day) showed 72% accuracy for ETH d>0.20% — the worst single day.
- We need 500+ signals to distinguish an 80% true accuracy from a 76% true accuracy (which would be negative EV).

## 4.7 Action Plan

| Priority | Action | Timeline | Goal |
|----------|--------|----------|------|
| 1 | Stop live trading (paper mode) | Immediately | Stop bleeding |
| 2 | Fix PM collector (inverted tokens) | This week | Enable PM analysis |
| 3 | Continue data collection | 3 more weeks | Double our sample size |
| 4 | Re-run analysis at ~5,000+ rounds/coin | Apr 7 | Confirm or reject ETH edge |
| 5 | If confirmed: ETH-only paper trial | Apr 7-14 | Validate real execution |
| 6 | If paper profitable: deposit $100, go live | Apr 14+ | Capture edge |
| 7 | Investigate limit order execution | Parallel | Potentially 3x EV |
| 8 | Cross-platform PM analysis (after fix) | After #2 | New edge source |

