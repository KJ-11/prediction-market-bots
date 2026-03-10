# Insights

Extracted from research and data analysis, organized by topic. See `empirical-findings.md` for detailed quantitative results.

## Calibration & Accuracy

### Brier Score
- Standard metric for binary prediction calibration: `BS = (1/N) * sum((forecast - outcome)^2)`
- Lower is better. 0 = perfect, 0.25 = random (coin flip)
- Useful for evaluating whether our confidence parameter (0.88) matches reality
- Can compute per-coin, per-window, per-distance-bucket to find where calibration breaks down

## Market Microstructure

### Kalshi Repricing Speed
- Market makers reprice within 14-21s median after spot crosses strike (not minutes as initially assumed)
- Mean is higher (55-62s) due to occasional slow outliers
- XRP market makers are fastest: ~1s median lag — likely have direct CF Benchmarks feed
- Implication: earlier entry window (T+250) captures mispricing before market corrects

### Polymarket vs Kalshi
- Kalshi resolves on CF Benchmarks (60s avg of RTI), Polymarket resolves on Chainlink (Binance price)
- Different resolution sources = potential for cross-platform divergence
- PM markets have 4 durations (5m/15m/1h/4h), Kalshi only 15m
- PM uses CLOB with up/down tokens, Kalshi uses yes/no on single order book

## Fee Structure Impact
- At price extremes ($0.90+), Kalshi fees drop to $0.003-0.006/contract — makes high-confidence trades very capital efficient
- Break-even WR at $0.76 avg price: ~76.8% (v2). At $0.80: ~81.6% (v1). Cheaper entry = more margin for error.
- Polymarket fees: TBD (need to analyze once we have trading data)
