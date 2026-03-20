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

### When fees are charged
- **On every executed trade** (entry buy, early exit sell)
- **NOT on settlement/expiration** — if you hold to resolution, you pay fee once on entry only
- Implication: buy-and-hold-to-settlement pays 1 fee; buy-then-sell-early pays 2 fees

### Taker fee (IOC / immediate fills)
- **Formula**: `round_up(0.07 * C * P * (1 - P))` per order
- C = contracts, P = price in dollars, rounding up to nearest cent on total order

### Maker fee (resting limit orders that get filled)
- **Formula**: `round_up(0.0175 * C * P * (1 - P))` per order
- Maker coefficient is exactly 1/4 of taker (75% cheaper)
- Added July 2025 (replaced flat $0.0025/contract)

### Fee examples (per contract)
| Price | Taker fee | Maker fee |
|-------|-----------|-----------|
| $0.50 | $0.0175 | $0.0044 |
| $0.80 | $0.0112 | $0.0028 |
| $0.90 | $0.0063 | $0.0016 |
| $0.95 | $0.0033 | $0.0008 |
| $0.10 | $0.0063 | $0.0016 |

- Fees peak at P=0.50 and drop toward extremes — favorable for late-round trading
- S&P/Nasdaq markets use halved multipliers (0.035/0.00875) — does NOT apply to crypto

## Position Limit
- $25,000 per strike, per member

## Market Discovery
- Use `/markets?series_ticker=KXBTC15M&status=open` — one fast API call per series
- Tickers rotate every 15 min, must re-discover each cycle
- New markets appear ~11-25s after previous round closes

## Contract Terms
- Source doc: https://kalshi-public-docs.s3.amazonaws.com/contract_terms/CRYPTO15M.pdf
- If no data available at expiration, affected strikes resolve to No
