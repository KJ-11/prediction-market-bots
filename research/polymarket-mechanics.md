# Polymarket Crypto Up/Down Markets

## Overview
- **Coins**: BTC, ETH, SOL, XRP
- **Durations**: 5m, 15m, 1h, 4h
- **Question**: "Will [coin] price be up or down at the end of this window?"
- Binary market — two tokens (Up and Down), each resolves to $1.00 or $0.00
- New round opens on fixed intervals aligned to clock boundaries

## Resolution Mechanics
- **Source**: Chainlink oracle reading Binance price
- **NOT** CF Benchmarks (that's Kalshi) — this is a key difference
- **RTDS feed** (`wss://ws-live-data.polymarket.com`) provides real-time Binance prices — this is the resolution proxy
- Resolves "Up" if price at close >= price at open
- Settlement is on-chain (Polygon)

### Key Difference from Kalshi
| | Kalshi | Polymarket |
|---|---|---|
| Resolution source | CF Benchmarks RTI (60s avg) | Chainlink oracle (Binance) |
| Resolution method | Avg of 60 RTI prices in last 60s | Single price snapshot |
| Settlement speed | ~60s after close | On-chain, variable |
| Chain | None (centralized) | Polygon |

## Market Structure

### Two Tokens per Market
Each market has two CLOB tokens:
- **Up token** (`clobTokenIds[0]`) — pays $1 if price goes up
- **Down token** (`clobTokenIds[1]`) — pays $1 if price goes down
- Each token has its own order book (bids and asks)
- Complement relationship: `up_price + down_price ≈ 1.00` (minus spread)

### Discovery
Markets use **predictable slugs** — no search API needed:
```
{coin}-updown-{duration}-{unix_close_timestamp}
```
Example: `btc-updown-5m-1773150900`

The close timestamp aligns to clock boundaries:
- 5m: every 300s boundary
- 15m: every 900s boundary
- 1h: every 3600s boundary
- 4h: every 14400s boundary

Slug → Gamma API: `GET /events/slug/{slug}` returns event with nested market containing condition_id, token IDs, end_date, prices.

### Key Market Fields
- `conditionId` — unique market identifier
- `clobTokenIds` — JSON string: `["up_token_id", "down_token_id"]`
- `endDate` — ISO 8601 close time
- `outcomePrices` — current up/down prices
- `volume` — total volume traded
- `active` / `closed` — market status
- `slug` — predictable URL slug

## APIs

### Gamma API (`gamma-api.polymarket.com`)
Market discovery and metadata. No auth required.
- `GET /events/slug/{slug}` — fetch by predictable slug
- `GET /markets/{condition_id}` — fetch by condition ID
- `GET /events?active=true` — search active events

### CLOB API (`clob.polymarket.com`)
Order books and prices. No auth for reads; signed orders for trading.
- `GET /book?token_id={id}` — full order book (bids/asks with sizes)
- `GET /midpoint?token_id={id}` — midpoint price
- `GET /spread?token_id={id}` — bid-ask spread
- `GET /price?token_id={id}&side=BUY` — best price
- `GET /last-trade-price?token_id={id}` — last execution
- `GET /tick-size/{token_id}` — minimum price increment

### Trading (not yet implemented)
- CLOB API requires **API key + signing** for order placement
- Orders are signed with an Ethereum wallet (Polygon chain)
- Requires USDC on Polygon for settlement
- See Polymarket CLOB API docs for order format

## WebSocket Feeds

### Market WS (`wss://ws-subscriptions-clob.polymarket.com/ws/market`)
Subscribe with token IDs (asset_ids). Receives 7 event types:

| Event | Description | Auth |
|-------|-------------|------|
| `book` | Full order book snapshot (bids + asks with sizes) | No |
| `best_bid_ask` | Top-of-book update (best bid, ask, spread) | No |
| `last_trade_price` | Trade execution (price, size, side) | No |
| `price_change` | Order book delta (order placed/cancelled) | No |
| `tick_size_change` | Minimum price increment changed | `custom_feature_enabled` |
| `new_market` | New market created | `custom_feature_enabled` |
| `market_resolved` | Market resolution with outcome | `custom_feature_enabled` |

Subscription message:
```json
{
  "assets_ids": ["up_token_id", "down_token_id"],
  "type": "market",
  "custom_feature_enabled": true
}
```

**Critical**: `custom_feature_enabled: true` is required for `market_resolved` events (authoritative outcome detection).

### RTDS WS (`wss://ws-live-data.polymarket.com`)
Real-time crypto prices from Binance (and optionally Chainlink). No auth.

Subscription:
```json
{
  "action": "subscribe",
  "subscriptions": [{"topic": "crypto_prices", "type": "update"}]
}
```

Message format:
```json
{
  "topic": "crypto_prices",
  "type": "update",
  "timestamp": 1753314064237,
  "payload": {"symbol": "btcusdt", "timestamp": ..., "value": 71236.01}
}
```

Symbols: `btcusdt`, `ethusdt`, `solusdt`, `xrpusdt`

## Fee Structure

### When fees are charged
- **Taker fee on every executed trade** (entry and exit)
- **Maker fee: 0%** — makers pay nothing and receive rebates via the Maker Rebates Program
- **NOT on settlement/resolution** — winning shares pay $1 USDC with no deduction
- **2% of net profits at withdrawal** (Global only) — functions as a deferred profit fee
- **Gas**: Polygon transaction fees for on-chain settlement (minimal, ~$0.01)

### Taker fee formula (crypto markets)
```
fee = C * p * feeRate * (p * (1 - p))^exponent
```
- `C` = shares, `p` = price (0 to 1)
- **Crypto**: feeRate=0.25, exponent=2 (quadratic — steeper than Kalshi's linear P*(1-P))
- **Sports**: feeRate=0.0175, exponent=1 (much cheaper)

### Fee examples — crypto markets (per contract)
| Price | Taker fee | % of notional |
|-------|-----------|---------------|
| $0.50 | $0.0078 | 1.56% |
| $0.60 | $0.0086 | 1.44% |
| $0.80 | $0.0064 | 0.80% |
| $0.90 | $0.0020 | 0.22% |
| $0.20 | $0.0064 | 3.20% |

Peak fee at p=0.50 is ~1.56% of notional. Drops toward extremes due to the (p*(1-p))^2 term.

### Maker rebates
- 20% of taker fees paid back daily to liquidity providers (crypto markets)
- 25% rebate for sports markets

### Crypto fee rollout timeline
- Jan 19, 2026 — 15-min crypto markets (first to get fees)
- Feb 12, 2026 — 5-min crypto markets
- Mar 6, 2026 — 1H, 4H, daily, weekly crypto markets
- Only applies to markets deployed after activation dates

### API
- Fetch fee rate per token: `GET https://clob.polymarket.com/fee-rate?token_id={token_id}`
- SDK auto-fetches; REST requires manual `feeRateBps` in signed orders

## Volume & Liquidity (TBD)
Data collection started Mar 10, 2026. Need 48+ hours of data to characterize:
- Spread distribution by coin and duration
- Volume per round
- Book depth at various price levels
- Comparison to Kalshi liquidity

## What We're Collecting
16 Docker collectors (4 coins x 4 durations), each writing 1 snapshot/sec:
- **Coinbase spot** — primary crypto price (same as Kalshi collector)
- **Kraken spot** — cross-validation (CF Benchmarks constituent)
- **PM Market WS** — up/down bid/ask, trades, market_resolved events
- **PM RTDS** — Binance price (PM resolution proxy via Chainlink)

Output: `data/rounds/polymarket/{COIN}-{duration}-YYYY-MM-DD.csv` with 23 columns including all price sources, book data, and round outcomes.
