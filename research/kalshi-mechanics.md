# Kalshi 15-Min Crypto Binary Markets

## Overview
- **Series**: KXBTC15M, KXETH15M, KXSOL15M, KXXRP15M
- **Question**: "Will [coin] price be up in the next 15 minutes?"
- Binary bet — resolves Yes or No, pays $1.00 or $0.00
- New round opens immediately when previous closes — continuous 15-min cycles

## Resolution Mechanics
- **Source**: CF Benchmarks Real-Time Index (BRTI for BTC, corresponding RTI for others)
- **NOT** Coinbase, Binance, or Google prices — these can diverge from CF Benchmarks
- **Method**: Simple average of 60 RTI prices collected in the last 60 seconds before the timestamp
- **Resolves Yes** if avg price at close >= avg price at open ("at least" = greater or equal)
- Settles within 60 seconds of close

## Key Market Fields
- `floor_strike` — the reference price to beat (set at market open)
- `yes_sub_title` — human-readable "Price to beat: $X"
- `close_time` — when trading stops
- `open_time` — when this round started
- `status: "active"` — currently tradable (not `"open"`)

## Volume & Liquidity
| Coin | Series | ~24h Volume | Relative Liquidity |
|------|--------|-------------|-------------------|
| BTC  | KXBTC15M | 45,000+ | Highest by far |
| ETH  | KXETH15M | ~3,500 | Moderate |
| SOL  | KXSOL15M | ~1,500 | Lower |
| XRP  | KXXRP15M | ~1,100 | Lowest |

Open interest on a single BTC round can exceed 50k contracts.

**Order book depth at $0.90+ (observed Mar 7, 2026):**
| Coin | YES side depth | NO side depth | Top-of-book (WS) |
|------|---------------|--------------|-------------------|
| BTC  | 193,618       | 113,795      | 488-696           |
| ETH  | 186,999       | 3,617        | 369-1,300         |
| SOL  | 80,791        | 2,160        | 1,380-2,460       |

Liquidity is NOT a constraint until thousands of contracts per trade. Top-of-book (WS `yes_ask_size_fp`) is much smaller than full depth — at scale, walk the book across price levels.

## Fee Structure
- **Formula**: `$0.07 * P * (1 - P)` per contract, where P = contract price in dollars
- Both maker and taker fees apply (maker fees added April 2025)
- Fee examples:
  - At $0.50 (max): ~$0.0175/contract
  - At $0.20 or $0.80: ~$0.0112/contract
  - At $0.10 or $0.90: ~$0.0063/contract
  - At $0.05 or $0.95: ~$0.0033/contract
- Fees are lowest at price extremes — relevant for late-round trading

## Position Limit
- $25,000 per strike, per member

## Market Discovery
- Use `/markets?series_ticker=KXBTC15M&status=open` — one fast API call per series
- Tickers rotate every 15 min, must re-discover each cycle
- New markets appear ~11-25s after previous round closes

## Contract Terms
- Source doc: https://kalshi-public-docs.s3.amazonaws.com/contract_terms/CRYPTO15M.pdf
- If no data available at expiration, affected strikes resolve to No
