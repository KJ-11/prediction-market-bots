# V3 Deep Dive

======================================================================
PART 1: ML MODEL DEEP DIVE (Kalshi)
======================================================================

==================================================
Observation at T+300s
==================================================

Train: 1855 samples (2026-03-08 to 2026-03-15)
Test: 624 samples (2026-03-16 to 2026-03-17)
OOS AUC: 0.7472

### What does the model see at p>0.85?


YES bets (p>0.85): n=112, WR=85.7%
  abs_dist: mean=0.309%, median=0.236%
  yes_mid: mean=0.832, median=0.850
  spot_above: 100%
  mom_120s: mean=0.1304%
  intra_vol: mean=0.100922
  strike_crosses: mean=1.5
  Coins: {'ETH': 35, 'SOL': 30, 'BTC': 25, 'XRP': 22}
  Entry ask: median=$0.860, mean=$0.843

NO bets (p<0.15): n=103, WR=79.6%
  abs_dist: mean=0.265%, median=0.218%
  yes_mid: mean=0.202, median=0.195
  spot_above: 0%
  mom_120s: mean=-0.0775%
  intra_vol: mean=0.089043
  strike_crosses: mean=1.5
  Coins: {'BTC': 28, 'ETH': 26, 'XRP': 26, 'SOL': 23}
  Entry ask: median=$0.810, mean=$0.810

### Day-by-Day OOS Performance


Threshold: p>0.85
  2026-03-16: 135 trades (80Y/55N), WR=85%, PnL=$0.14
  2026-03-17: 80 trades (32Y/48N), WR=79%, PnL=$-3.12

Threshold: p>0.9
  2026-03-16: 79 trades (51Y/28N), WR=91%, PnL=$2.02
  2026-03-17: 44 trades (18Y/26N), WR=84%, PnL=$-0.58

### Model vs Simple Rules

    dist>0.15%          : n= 252, WR=80.6%, EV=$-0.0453, PnL=$-11.41
    dist>0.20%          : n= 175, WR=84.0%, EV=$-0.0381, PnL=$-6.66
    dist>0.30%          : n=  93, WR=86.0%, EV=$-0.0533, PnL=$-4.96
    yes_mid>0.85        : n=  95, WR=89.5%, EV=$-0.0285, PnL=$-2.71
    GBM p>0.85          : n= 215, WR=82.8%, EV=$-0.0139, PnL=$-2.98
  ✓ GBM p>0.90          : n= 123, WR=88.6%, EV=$0.0117, PnL=$1.44

### GBM + Price Cap (only enter when ask is cheap)

    p>0.8, ask≤$0.8: n=153, WR=68.6%, EV=$-0.0448, PnL=$-6.85, 76.5/day
    p>0.8, ask≤$0.85: n=196, WR=71.4%, EV=$-0.0407, PnL=$-7.97, 98.0/day
    p>0.8, ask≤$0.9: n=250, WR=74.0%, EV=$-0.0441, PnL=$-11.03, 125.0/day
    p>0.8, ask≤$0.95: n=285, WR=76.8%, EV=$-0.0346, PnL=$-9.87, 142.5/day
    p>0.85, ask≤$0.8: n=84, WR=72.6%, EV=$-0.0289, PnL=$-2.43, 42.0/day
    p>0.85, ask≤$0.85: n=118, WR=76.3%, EV=$-0.0171, PnL=$-2.02, 59.0/day
    p>0.85, ask≤$0.9: n=171, WR=78.9%, EV=$-0.0246, PnL=$-4.21, 85.5/day
    p>0.85, ask≤$0.95: n=205, WR=82.4%, EV=$-0.0104, PnL=$-2.13, 102.5/day
  ✓ p>0.9, ask≤$0.8: n=23, WR=78.3%, EV=$0.0396, PnL=$0.91, 11.5/day
  ✓ p>0.9, ask≤$0.85: n=41, WR=82.9%, EV=$0.0424, PnL=$1.74, 20.5/day
  ✓ p>0.9, ask≤$0.9: n=83, WR=84.3%, EV=$0.0042, PnL=$0.35, 41.5/day
  ✓ p>0.9, ask≤$0.95: n=115, WR=88.7%, EV=$0.0203, PnL=$2.34, 57.5/day

==================================================
Observation at T+450s
==================================================

Train: 1852 samples (2026-03-08 to 2026-03-15)
Test: 624 samples (2026-03-16 to 2026-03-17)
OOS AUC: 0.8323

### What does the model see at p>0.85?


YES bets (p>0.85): n=191, WR=88.5%
  abs_dist: mean=0.283%, median=0.214%
  yes_mid: mean=0.851, median=0.855
  spot_above: 100%
  mom_120s: mean=0.0577%
  intra_vol: mean=0.107637
  strike_crosses: mean=2.3
  Coins: {'ETH': 51, 'SOL': 50, 'BTC': 49, 'XRP': 41}
  Entry ask: median=$0.870, mean=$0.864

NO bets (p<0.15): n=137, WR=86.9%
  abs_dist: mean=0.319%, median=0.264%
  yes_mid: mean=0.138, median=0.115
  spot_above: 1%
  mom_120s: mean=-0.1046%
  intra_vol: mean=0.109676
  strike_crosses: mean=2.3
  Coins: {'XRP': 40, 'BTC': 38, 'SOL': 31, 'ETH': 28}
  Entry ask: median=$0.900, mean=$0.874

### Day-by-Day OOS Performance


Threshold: p>0.85
  2026-03-16: 205 trades (126Y/79N), WR=91%, PnL=$7.25
  2026-03-17: 123 trades (65Y/58N), WR=82%, PnL=$-8.25

Threshold: p>0.9
  2026-03-16: 160 trades (110Y/50N), WR=94%, PnL=$5.52
  2026-03-17: 99 trades (57Y/42N), WR=86%, PnL=$-4.75

### Model vs Simple Rules

    dist>0.15%          : n= 297, WR=87.9%, EV=$-0.0275, PnL=$-8.17
    dist>0.20%          : n= 232, WR=90.9%, EV=$-0.0164, PnL=$-3.80
    dist>0.30%          : n= 128, WR=94.5%, EV=$-0.0144, PnL=$-1.84
    yes_mid>0.85        : n= 205, WR=91.2%, EV=$-0.0294, PnL=$-6.03
    GBM p>0.85          : n= 328, WR=88.1%, EV=$-0.0000, PnL=$-0.01
  ✓ GBM p>0.90          : n= 259, WR=90.7%, EV=$0.0030, PnL=$0.77

### GBM + Price Cap (only enter when ask is cheap)

    p>0.8, ask≤$0.8: n=119, WR=71.4%, EV=$-0.0298, PnL=$-3.55, 59.5/day
  ✓ p>0.8, ask≤$0.85: n=181, WR=77.9%, EV=$0.0014, PnL=$0.26, 90.5/day
    p>0.8, ask≤$0.9: n=251, WR=78.5%, EV=$-0.0245, PnL=$-6.14, 125.5/day
    p>0.8, ask≤$0.95: n=318, WR=82.1%, EV=$-0.0167, PnL=$-5.31, 159.0/day
  ✓ p>0.85, ask≤$0.8: n=78, WR=78.2%, EV=$0.0256, PnL=$2.00, 39.0/day
  ✓ p>0.85, ask≤$0.85: n=130, WR=83.1%, EV=$0.0401, PnL=$5.21, 65.0/day
    p>0.85, ask≤$0.9: n=194, WR=82.0%, EV=$-0.0045, PnL=$-0.87, 97.0/day
    p>0.85, ask≤$0.95: n=260, WR=85.4%, EV=$-0.0003, PnL=$-0.09, 130.0/day
  ✓ p>0.9, ask≤$0.8: n=37, WR=89.2%, EV=$0.1108, PnL=$4.10, 18.5/day
  ✓ p>0.9, ask≤$0.85: n=77, WR=89.6%, EV=$0.0823, PnL=$6.34, 38.5/day
  ✓ p>0.9, ask≤$0.9: n=130, WR=85.4%, EV=$0.0082, PnL=$1.06, 65.0/day
  ✓ p>0.9, ask≤$0.95: n=193, WR=88.6%, EV=$0.0087, PnL=$1.68, 96.5/day



======================================================================
PART 2: PM PRICE-GAP TRADING DEEP DIVE
======================================================================

### 2a. PM Book Quality — Are these prices real?


At T-300s:
  Total round-snapshots: 1574
  Real UP token quoted: 0 (0%)
  Real DOWN token quoted: 546 (35%)
  Both quoted: 0 (0%)

At T-180s:
  Total round-snapshots: 1578
  Real UP token quoted: 0 (0%)
  Real DOWN token quoted: 413 (26%)
  Both quoted: 0 (0%)

### 2b. Price Gap Trading with Real PM Book Prices

Focus: when Kalshi and PM disagree, trade the PM side that Kalshi predicts.
Only count trades where PM has a real quoted book.


--- T-450s (n=1499 matched) ---

When Kalshi < PM (buy PM DOWN = buy 'up' token in data):
    gap<-5%: n=343, WR(DOWN)=29.2%, ask=$0.290, bid=$0.280, spread=$0.010, vol=$139, EV≈$-0.0043
      BTC: n=44, WR=36%
      ETH: n=78, WR=22%
      SOL: n=114, WR=28%
      XRP: n=107, WR=33%
    gap<-10%: n=312, WR(DOWN)=26.3%, ask=$0.280, bid=$0.265, spread=$0.015, vol=$135, EV≈$-0.0228
      BTC: n=40, WR=32%
      ETH: n=72, WR=19%
      SOL: n=104, WR=26%
      XRP: n=96, WR=29%
    gap<-15%: n=278, WR(DOWN)=22.7%, ask=$0.250, bid=$0.230, spread=$0.019, vol=$136, EV≈$-0.0279
      BTC: n=34, WR=26%
      ETH: n=65, WR=18%
      SOL: n=92, WR=22%
      XRP: n=87, WR=25%
    gap<-20%: n=256, WR(DOWN)=23.0%, ask=$0.250, bid=$0.235, spread=$0.014, vol=$135, EV≈$-0.0240
      BTC: n=31, WR=26%
      ETH: n=62, WR=18%
      SOL: n=86, WR=23%
      XRP: n=77, WR=26%

When Kalshi > PM (buy PM UP = buy 'down' token in data):

--- T-300s (n=1428 matched) ---

When Kalshi < PM (buy PM DOWN = buy 'up' token in data):
  ✓ gap<-5%: n=252, WR(DOWN)=27.8%, ask=$0.210, bid=$0.200, spread=$0.010, vol=$133, EV≈$0.0636
      BTC: n=24, WR=38%
      ETH: n=56, WR=25%
      SOL: n=81, WR=28%
      XRP: n=91, WR=26%
  ✓ gap<-10%: n=226, WR(DOWN)=27.0%, ask=$0.205, bid=$0.195, spread=$0.010, vol=$133, EV≈$0.0608
      BTC: n=21, WR=29%
      ETH: n=51, WR=25%
      SOL: n=73, WR=27%
      XRP: n=81, WR=27%
  ✓ gap<-15%: n=209, WR(DOWN)=26.3%, ask=$0.200, bid=$0.190, spread=$0.010, vol=$135, EV≈$0.0592
      BTC: n=20, WR=25%
      ETH: n=47, WR=23%
      SOL: n=67, WR=28%
      XRP: n=75, WR=27%
  ✓ gap<-20%: n=191, WR(DOWN)=24.1%, ask=$0.190, bid=$0.180, spread=$0.010, vol=$134, EV≈$0.0470
      BTC: n=18, WR=22%
      ETH: n=41, WR=24%
      SOL: n=63, WR=24%
      XRP: n=69, WR=25%

When Kalshi > PM (buy PM UP = buy 'down' token in data):

--- T-180s (n=1186 matched) ---

When Kalshi < PM (buy PM DOWN = buy 'up' token in data):
  ✓ gap<-5%: n=174, WR(DOWN)=31.6%, ask=$0.218, bid=$0.205, spread=$0.013, vol=$197, EV≈$0.0937
      BTC: n=21, WR=19%
      ETH: n=39, WR=28%
      SOL: n=60, WR=28%
      XRP: n=54, WR=43%
  ✓ gap<-10%: n=157, WR(DOWN)=31.2%, ask=$0.209, bid=$0.190, spread=$0.019, vol=$195, EV≈$0.0989
      BTC: n=19, WR=21%
      ETH: n=36, WR=25%
      SOL: n=54, WR=28%
      XRP: n=48, WR=44%
  ✓ gap<-15%: n=142, WR(DOWN)=29.6%, ask=$0.200, bid=$0.185, spread=$0.015, vol=$195, EV≈$0.0918
      BTC: n=19, WR=21%
      ETH: n=32, WR=22%
      SOL: n=49, WR=24%
      XRP: n=42, WR=45%
  ✓ gap<-20%: n=130, WR(DOWN)=24.6%, ask=$0.150, bid=$0.140, spread=$0.010, vol=$188, EV≈$0.0937
      BTC: n=18, WR=17%
      ETH: n=30, WR=20%
      SOL: n=44, WR=18%
      XRP: n=38, WR=39%

When Kalshi > PM (buy PM UP = buy 'down' token in data):

### 2c. Day-by-Day PM Strategy (T-300s, gap<-10%)

  2026-03-10: n=4, WR(DOWN)=25%, ask=$0.135, EV≈$0.1123
  2026-03-11: n=51, WR(DOWN)=31%, ask=$0.200, EV≈$0.1097
  2026-03-12: n=55, WR(DOWN)=22%, ask=$0.180, EV≈$0.0346
  2026-03-14: n=1, WR(DOWN)=0%, ask=$0.150, EV≈$-0.1530
  2026-03-15: n=37, WR(DOWN)=22%, ask=$0.220, EV≈$-0.0082
  2026-03-16: n=45, WR(DOWN)=24%, ask=$0.200, EV≈$0.0404
  2026-03-17: n=41, WR(DOWN)=32%, ask=$0.320, EV≈$-0.0093

### 2d. PM 5m — Same price-gap analysis with Kalshi

PM 5m has way more rounds. Can Kalshi 15m mid predict PM 5m outcomes?


Matched Kalshi-vs-PM-5m rounds: 1301
Agreement (both >0.5): 59.0%
  ✓ 5m gap<-5%: n=264, WR(DOWN)=49.2%, ask=$0.420, EV≈$0.0640
  ✓ 5m gap<-10%: n=225, WR(DOWN)=45.8%, ask=$0.390, EV≈$0.0600
  ✓ 5m gap<-15%: n=201, WR(DOWN)=44.3%, ask=$0.330, EV≈$0.1062
