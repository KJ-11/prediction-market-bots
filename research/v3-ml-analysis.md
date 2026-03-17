# V3 ML & Cross-Platform Analysis


### ML at T+180s

Samples: 2456, Features: 23, Base rate: 50.6% yes

Logistic Regression AUC: 0.7376 ± 0.0137

LR coefficients (top 10):
  yes_mid               : +0.8793
  mom_120s              : +0.2323
  mom_30s               : +0.2194
  spot_range            : -0.1851
  book_momentum         : -0.1658
  mom_60s               : -0.1542
  accel                 : -0.1486
  intra_vol             : +0.1064
  abs_dist              : -0.0817
  volume                : +0.0506

GBM AUC: 0.7604 ± 0.0091

GBM Feature Importance (top 10):
  yes_mid               : 0.2098 ████████████████████
  pct_dist              : 0.1193 ███████████
  mom_120s              : 0.0769 ███████
  mom_30s               : 0.0636 ██████
  hour                  : 0.0627 ██████
  mom_60s               : 0.0578 █████
  max_dist_seen         : 0.0544 █████
  accel                 : 0.0533 █████
  book_momentum         : 0.0502 █████
  volume                : 0.0480 ████

Brier Score — GBM: 0.1127, Market: 0.2066
  ✓ GBM beats market by 0.0939

### Out-of-Sample Trading Simulation

Out-of-sample AUC: 0.6922 (train=1719, test=737)
    p>0.55: 662 trades (334Y/328N), WR=65.7%, EV=$-0.0267, PnL=$-17.69, 220.7/day
    p>0.60: 591 trades (304Y/287N), WR=66.8%, EV=$-0.0295, PnL=$-17.45, 197.0/day
    p>0.65: 516 trades (270Y/246N), WR=68.6%, EV=$-0.0278, PnL=$-14.32, 172.0/day
    p>0.70: 421 trades (216Y/205N), WR=70.1%, EV=$-0.0332, PnL=$-13.99, 140.3/day
    p>0.75: 346 trades (182Y/164N), WR=72.5%, EV=$-0.0297, PnL=$-10.27, 115.3/day
    p>0.80: 270 trades (146Y/124N), WR=75.6%, EV=$-0.0218, PnL=$-5.89, 90.0/day
    p>0.85: 200 trades (105Y/95N), WR=74.0%, EV=$-0.0551, PnL=$-11.02, 66.7/day
    p>0.90: 103 trades (51Y/52N), WR=81.6%, EV=$-0.0125, PnL=$-1.29, 34.3/day

### Per-coin OOS AUC:
  BTC: AUC=0.6550 (n=156)
  ETH: AUC=0.7067 (n=230)
  SOL: AUC=0.6785 (n=195)
  XRP: AUC=0.7250 (n=156)

### ML at T+300s

Samples: 2479, Features: 23, Base rate: 50.5% yes

Logistic Regression AUC: 0.7974 ± 0.0142

LR coefficients (top 10):
  yes_mid               : +1.2525
  pct_dist              : +0.2099
  abs_dist              : +0.1723
  max_dist_seen         : -0.1494
  mom_60s               : -0.1398
  book_momentum         : +0.1278
  spot_range            : -0.1171
  spot_above            : -0.1034
  intra_vol             : +0.0982
  volume                : +0.0859

GBM AUC: 0.8124 ± 0.0071

GBM Feature Importance (top 10):
  yes_mid               : 0.4226 ██████████████████████████████████████████
  mom_120s              : 0.0508 █████
  book_momentum         : 0.0500 ████
  pct_dist              : 0.0490 ████
  intra_vol             : 0.0475 ████
  max_dist_seen         : 0.0469 ████
  accel                 : 0.0443 ████
  kc_divergence         : 0.0436 ████
  volume                : 0.0388 ███
  hour                  : 0.0375 ███

Brier Score — GBM: 0.0995, Market: 0.1822
  ✓ GBM beats market by 0.0827

### Out-of-Sample Trading Simulation

Out-of-sample AUC: 0.7557 (train=1735, test=744)
    p>0.55: 678 trades (325Y/353N), WR=69.9%, EV=$-0.0230, PnL=$-15.62, 226.0/day
    p>0.60: 619 trades (300Y/319N), WR=71.2%, EV=$-0.0261, PnL=$-16.13, 206.3/day
    p>0.65: 551 trades (271Y/280N), WR=74.0%, EV=$-0.0188, PnL=$-10.38, 183.7/day
    p>0.70: 505 trades (247Y/258N), WR=74.7%, EV=$-0.0256, PnL=$-12.91, 168.3/day
    p>0.75: 430 trades (212Y/218N), WR=77.4%, EV=$-0.0186, PnL=$-7.99, 143.3/day
    p>0.80: 360 trades (186Y/174N), WR=78.9%, EV=$-0.0226, PnL=$-8.12, 120.0/day
    p>0.85: 278 trades (147Y/131N), WR=81.3%, EV=$-0.0179, PnL=$-4.98, 92.7/day
  ✓ p>0.90: 150 trades (85Y/65N), WR=88.0%, EV=$0.0047, PnL=$0.71, 50.0/day

### Per-coin OOS AUC:
  BTC: AUC=0.7318 (n=156)
  ETH: AUC=0.8051 (n=232)
  SOL: AUC=0.7237 (n=200)
  XRP: AUC=0.7481 (n=156)

### ML at T+450s

Samples: 2476, Features: 23, Base rate: 50.3% yes

Logistic Regression AUC: 0.8713 ± 0.0069

LR coefficients (top 10):
  yes_mid               : +1.3145
  pct_dist              : +0.7146
  abs_dist              : +0.1896
  max_dist_seen         : -0.1502
  yes_spread            : -0.1057
  coin_BTC              : -0.0864
  volume                : +0.0858
  mom_60s               : +0.0772
  accel                 : -0.0701
  book_momentum         : -0.0583

GBM AUC: 0.8702 ± 0.0052

GBM Feature Importance (top 10):
  pct_dist              : 0.3587 ███████████████████████████████████
  yes_mid               : 0.2304 ███████████████████████
  abs_dist              : 0.0460 ████
  mom_120s              : 0.0371 ███
  intra_vol             : 0.0367 ███
  volume                : 0.0355 ███
  kc_divergence         : 0.0316 ███
  accel                 : 0.0308 ███
  max_dist_seen         : 0.0297 ██
  hour                  : 0.0284 ██

Brier Score — GBM: 0.0736, Market: 0.1458
  ✓ GBM beats market by 0.0722

### Out-of-Sample Trading Simulation

Out-of-sample AUC: 0.8465 (train=1733, test=743)
    p>0.55: 711 trades (342Y/369N), WR=77.9%, EV=$-0.0111, PnL=$-7.88, 237.0/day
    p>0.60: 659 trades (314Y/345N), WR=79.1%, EV=$-0.0142, PnL=$-9.39, 219.7/day
    p>0.65: 617 trades (297Y/320N), WR=80.9%, EV=$-0.0100, PnL=$-6.18, 205.7/day
    p>0.70: 565 trades (275Y/290N), WR=82.7%, EV=$-0.0090, PnL=$-5.11, 188.3/day
    p>0.75: 512 trades (261Y/251N), WR=85.0%, EV=$-0.0035, PnL=$-1.77, 170.7/day
    p>0.80: 444 trades (243Y/201N), WR=86.0%, EV=$-0.0121, PnL=$-5.37, 148.0/day
  ✓ p>0.85: 371 trades (222Y/149N), WR=89.8%, EV=$0.0076, PnL=$2.82, 123.7/day
  ✓ p>0.90: 290 trades (189Y/101N), WR=92.4%, EV=$0.0140, PnL=$4.05, 96.7/day

### Per-coin OOS AUC:
  BTC: AUC=0.8152 (n=156)
  ETH: AUC=0.8786 (n=231)
  SOL: AUC=0.8294 (n=200)
  XRP: AUC=0.8487 (n=156)

======================================================================
KALSHI LEADS PM — Can Kalshi price predict PM outcomes?
======================================================================

T-600s: n=1474, agree=49.3%, disagree=748
  Kalshi predicts PM outcome: 48.7%
  PM predicts own outcome: 74.9%
  DISAGREEMENTS (n=748):
    Kalshi right about PM: 24.2%
    PM right about itself: 75.8%
    Kalshi=YES, PM=DOWN (380 cases): PM actually UP 26%, entry≈$0.990
    Kalshi=NO, PM=UP (368 cases): PM actually DOWN 22%, entry≈$0.290

T-450s: n=1292, agree=45.8%, disagree=700
  Kalshi predicts PM outcome: 47.0%
  PM predicts own outcome: 78.6%
  DISAGREEMENTS (n=700):
    Kalshi right about PM: 20.9%
    PM right about itself: 79.1%
    Kalshi=YES, PM=DOWN (352 cases): PM actually UP 25%, entry≈$0.990
    Kalshi=NO, PM=UP (348 cases): PM actually DOWN 16%, entry≈$0.250

T-300s: n=1011, agree=47.1%, disagree=535
  Kalshi predicts PM outcome: 48.1%
  PM predicts own outcome: 80.8%
  DISAGREEMENTS (n=535):
    Kalshi right about PM: 19.1%
    PM right about itself: 80.9%
    Kalshi=YES, PM=DOWN (273 cases): PM actually UP 23%, entry≈$0.990
    Kalshi=NO, PM=UP (262 cases): PM actually DOWN 15%, entry≈$0.195

T-180s: n=625, agree=49.4%, disagree=316
  Kalshi predicts PM outcome: 50.6%
  PM predicts own outcome: 84.2%
  DISAGREEMENTS (n=316):
    Kalshi right about PM: 16.8%
    PM right about itself: 83.2%
    Kalshi=YES, PM=DOWN (150 cases): PM actually UP 21%, entry≈$0.990
    Kalshi=NO, PM=UP (166 cases): PM actually DOWN 13%, entry≈$0.149

T-120s: n=407, agree=48.2%, disagree=211
  Kalshi predicts PM outcome: 52.3%
  PM predicts own outcome: 85.5%
  DISAGREEMENTS (n=211):
    Kalshi right about PM: 18.0%
    PM right about itself: 82.0%
    Kalshi=YES, PM=DOWN (106 cases): PM actually UP 23%, entry≈$0.990
    Kalshi=NO, PM=UP (105 cases): PM actually DOWN 13%, entry≈$0.139

T-60s: n=166, agree=50.0%, disagree=83
  Kalshi predicts PM outcome: 50.6%
  PM predicts own outcome: 83.7%
  DISAGREEMENTS (n=83):
    Kalshi right about PM: 16.9%
    PM right about itself: 83.1%
    Kalshi=YES, PM=DOWN (47 cases): PM actually UP 21%, entry≈$0.990
    Kalshi=NO, PM=UP (36 cases): PM actually DOWN 11%, entry≈$0.120


### Price Gap → PM Outcome

    T-450s, gap>5% (567): PM UP 35%, entry=$0.990, EV≈$-0.6606
    T-450s, gap<-5% (609): PM DOWN 29%, entry=$0.300, EV≈$-0.0154
    T-450s, gap>10% (519): PM UP 32%, entry=$0.990, EV≈$-0.6861
    T-450s, gap<-10% (555): PM DOWN 26%, entry=$0.290, EV≈$-0.0309
    T-450s, gap>15% (482): PM UP 31%, entry=$0.990, EV≈$-0.7007
    T-450s, gap<-15% (497): PM DOWN 24%, entry=$0.260, EV≈$-0.0278
    T-450s, gap>20% (439): PM UP 29%, entry=$0.990, EV≈$-0.7228
    T-450s, gap<-20% (444): PM DOWN 23%, entry=$0.250, EV≈$-0.0230
    T-450s, gap>30% (360): PM UP 25%, entry=$0.990, EV≈$-0.7570
    T-450s, gap<-30% (342): PM DOWN 19%, entry=$0.230, EV≈$-0.0416
    T-300s, gap>5% (445): PM UP 35%, entry=$0.990, EV≈$-0.6570
  ✓ T-300s, gap<-5% (459): PM DOWN 29%, entry=$0.250, EV≈$0.0391
    T-300s, gap>10% (413): PM UP 34%, entry=$0.990, EV≈$-0.6732
  ✓ T-300s, gap<-10% (409): PM DOWN 27%, entry=$0.230, EV≈$0.0392
    T-300s, gap>15% (369): PM UP 30%, entry=$0.990, EV≈$-0.7090
  ✓ T-300s, gap<-15% (376): PM DOWN 26%, entry=$0.220, EV≈$0.0336
    T-300s, gap>20% (334): PM UP 29%, entry=$0.990, EV≈$-0.7224
  ✓ T-300s, gap<-20% (335): PM DOWN 22%, entry=$0.200, EV≈$0.0169
    T-300s, gap>30% (285): PM UP 25%, entry=$0.990, EV≈$-0.7572
    T-300s, gap<-30% (286): PM DOWN 18%, entry=$0.190, EV≈$-0.0120
    T-180s, gap>5% (272): PM UP 39%, entry=$0.990, EV≈$-0.6164
  ✓ T-180s, gap<-5% (284): PM DOWN 31%, entry=$0.210, EV≈$0.0957
    T-180s, gap>10% (239): PM UP 36%, entry=$0.990, EV≈$-0.6500
  ✓ T-180s, gap<-10% (257): PM DOWN 28%, entry=$0.190, EV≈$0.0902
    T-180s, gap>15% (219): PM UP 34%, entry=$0.990, EV≈$-0.6719
  ✓ T-180s, gap<-15% (238): PM DOWN 27%, entry=$0.175, EV≈$0.0946
    T-180s, gap>20% (203): PM UP 32%, entry=$0.990, EV≈$-0.6945
  ✓ T-180s, gap<-20% (221): PM DOWN 23%, entry=$0.169, EV≈$0.0584
    T-180s, gap>30% (169): PM UP 27%, entry=$0.990, EV≈$-0.7376
  ✓ T-180s, gap<-30% (184): PM DOWN 16%, entry=$0.149, EV≈$0.0056
