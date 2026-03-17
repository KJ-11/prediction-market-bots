# First-Principles Strategy Analysis

*Written 2026-03-17 after data verification. See `research/data-verification.md` for data quality assessment.*

## What We Actually Know (Verified)

### The Markets
- **Kalshi**: 15m binary options on BTC/ETH/SOL/XRP. "Will price be ≥ strike at end?" Single order book, resolves on CF Benchmarks (60s TWAP, multiple exchanges). Fee = 0.07 × p × (1-p). ~96 rounds/day, 24/7.
- **Polymarket**: 5m/15m/1h/4h binary options on same coins. "Will price be up or down?" Two-token CLOB, resolves on Chainlink (Binance price). Fee = 0.25 × p × (p×(1-p))² (tiny at extremes). Thin resting books but $68K-$124K volume per 15m round.

### The Data (Verified Quality)
| Source | Reliable? | Notes |
|:--|:--|:--|
| Kalshi outcomes | YES | 0% unknown, API-confirmed |
| Kalshi book (yes_bid/yes_ask) | YES | Median 90 unique price levels/round |
| Kalshi spot (Coinbase) | YES | 99.94% availability |
| PM `last_trade_price` | YES | 96-98% availability, consistent UP-token view |
| PM spot (Coinbase/Kraken/RTDS) | YES | Independent sources |
| PM `up_bid`/`up_ask` | **NO** | Contaminated by DOWN token events |
| PM outcomes | PARTIAL | 7-11% unknown (missing DOWN token trades) |

### The Core Result (2,529 Kalshi rounds)
**The market is well-calibrated at the ask.** 153/170 strategy combinations are negative EV when buying at the ask. The market prices in the signal just enough — about -$0.02/trade regardless of parameters.

**ETH is the only marginal exception** — ~$0.01-0.02 positive EV at the ask in specific windows, but not statistically significant.

---

## Theoretical Edge Categories

### 1. Information Edge: "We see data others don't, or process it faster"

**What we see:**
- Coinbase + Kraken spot prices (WS, ~100ms latency)
- PM trade flow + RTDS (Binance/Chainlink) prices
- Kalshi order book depth

**What market makers see:**
- CF Benchmarks constituent feeds (Coinbase, Kraken, Bitstamp, Gemini, LMAX)
- Direct exchange feeds with co-located servers
- Internal flow data from their own positions

**Assessment: NO EDGE.** We're slower and see less data than the MMs. Our Coinbase spot arrives ~100ms after it happens; their constituent feeds arrive in single-digit ms. The fact that 3.4% of outcomes diverge from our Coinbase spot (CF Benchmarks resolution differs) proves the MMs are pricing information we literally cannot see.

The spot-distance signal works not because we're fast, but because the market takes 14-21 seconds to fully reprice after a move. That's a behavioral/structural gap, not an information advantage.

### 2. Execution Edge: "We can enter/exit at better prices"

**Current execution:**
- IOC at ask: immediate fill but worst price
- Limit at bid: best price but uncertain fill rate
- Fill rate data from v1: 36% (94% of skips = "price too high")

**Spread matters enormously:**
- BTC spread: $0.01 → $0.01 saved per contract
- ETH/SOL/XRP spread: $0.02-$0.03 → $0.02-$0.03 saved per contract
- Finding 15: many strategies flip from negative to positive EV at bid vs ask

**Assessment: POSSIBLE SMALL EDGE.** If we can reliably fill at bid or midpoint instead of ask, that transforms -$0.02 EV into flat or slightly positive. But limit order fill rate is the key unknown. At 36% fill rate, we need the edge per fill to compensate for missed opportunities.

**Key question**: Can we reliably get fills between bid and ask? Are there execution strategies (e.g., post limit, cancel if not filled within Ns, reprice) that improve fill rate without giving up too much on price?

### 3. Structural Edge: "Fee structure, resolution mechanics, or market design create systematic mispricings"

**Kalshi fee structure:**
- Fee = 0.07 × p × (1-p)
- At p=0.90: $0.0063 (0.63%)
- At p=0.50: $0.0175 (1.75%)
- At p=0.95: $0.0033 (0.33%)
- Fees are lowest at extremes — but that's where the market is most efficient

**Kalshi single order book:**
- yes_bid + no_ask = $1.00 always → no cross-leg arb possible
- BUT this also means the market can only express one view at a time
- In a two-token market (PM), divergent views can coexist briefly

**PM fee structure:**
- Fee = 0.25 × p × (p×(1-p))²
- At p=0.90: $0.002 (virtually free)
- At p=0.95: $0.0006
- 20% maker rebate
- **PM fees at extremes are 3-10× cheaper than Kalshi**

**Resolution source divergence:**
- Kalshi = CF Benchmarks (60s TWAP of Coinbase/Kraken/Bitstamp/Gemini/LMAX)
- PM = Chainlink (Binance price)
- 3.8% disagree on BTC 15m outcomes
- This means a position that's "right" on one platform is "wrong" on the other ~4% of the time

**Assessment: TWO STRUCTURAL EDGES WORTH EXPLORING.**

**Edge 3a: PM fee advantage at extremes.** When a contract is at 0.90+ on Kalshi, buying the same view on PM costs 3-10× less in fees. If PM has comparable accuracy, the EV per contract is higher on PM.

**Edge 3b: PM miscalibration.** Our prior analysis (suspect but directionally plausible) found PM systematically underprices contracts in the 0.30-0.80 range by 5-10%. With PM fees being tiny, even a small miscalibration is exploitable. This needs re-verification with fixed outcome data.

### 4. Cross-Platform Edge: "One platform's price contains information the other hasn't incorporated"

**Prior finding**: Kalshi does NOT lead PM (46-48% cross-platform prediction). They disagree ~50% intra-round, converging to 96.2% agreement at settlement.

**New perspective**: The question isn't "does Kalshi predict PM?" — it's "when both platforms give a signal and they AGREE, is that more reliable than either alone?"

**Available signals:**
- Kalshi yes_ask at time T → implied P(up)
- PM last_trade_price at time T → implied P(up)
- Coinbase spot vs strike at time T → raw distance signal
- Kraken spot at time T → alternative distance signal
- RTDS (Binance) at time T → PM-resolution-source distance signal

**Assessment: WORTH EXPLORING.** Not as "Kalshi leads PM" but as signal combination. When Kalshi's book says 70% and PM's last trade says 65%, the consensus might be more accurate than either alone. Or the *divergence* might identify rounds where one platform is slow to update.

### 5. Behavioral Edge: "Market participants systematically over/under-react"

**What we've found:**
- Market reprices in 14-21 seconds median after a spot move. This is the repricing lag.
- Contracts at $0.70-$0.80 win at 80.5% instead of 75% (Finding 16). This is a calibration gap.
- ETH is consistently 3-8% more accurate than BTC at equivalent distance. Market may be less efficient on ETH.

**Why these exist:**
- The 14-21s repricing lag: MMs can't update instantly. They need to verify the spot move is real (not a Coinbase-only spike), compute CF Benchmarks-weighted fair value, and manage inventory risk.
- The $0.70-$0.80 miscalibration: At these prices, the MM is exposed to $0.20-$0.30 of downside. They widen the spread slightly to compensate, creating systematic underpricing.
- ETH efficiency gap: Lower volume (32K vs 313K contracts/round for BTC). Fewer MMs → slower repricing → bigger gap.

**Assessment: THE MOST PROMISING EDGE.** This is what our current strategy exploits. The problem is it's barely positive EV at the ask ($0.01-$0.02 for ETH, negative for BTC/XRP). We need better execution to capture it.

---

## Strategy Directions (Ranked by Promise)

### Direction 1: ETH-Only with Execution Optimization (HIGH PRIORITY)

**Thesis**: ETH has the biggest behavioral gap. If we can improve execution from IOC-at-ask to limit-at-midpoint with reasonable fill rate, we transform marginal EV into meaningful profit.

**Mechanics:**
- Signal: spot distance > 0.15% from strike, T+200-500
- Entry: limit order at midpoint (yes_bid + $0.01 for BTC, yes_bid + $0.01-$0.02 for ETH)
- If not filled within 5s, reprice toward ask
- Exit: hold to settlement

**Expected improvement:**
- Current: ETH T+250-500 d>0.20%, ask EV=$0.012
- At midpoint: EV ≈ $0.032 (Finding 15)
- At bid: EV ≈ $0.044

**Data needed**: Fill rate at various price points. Need live testing data — backtest can't tell us this.

**Risk**: Low. Still paper-tradeable. Worst case = poor fill rate, we fall back to IOC at ask.

### Direction 2: PM Trading at Extremes (HIGH PRIORITY — needs data fix first)

**Thesis**: PM fees at 0.90+ are 3-10× cheaper than Kalshi. If PM last_trade_price at 0.90+ is as predictive as Kalshi yes_ask at 0.90+, we can capture the same edge with much lower friction.

**Before pursuing:**
1. Fix PM collector to track DOWN token trades (infer UP price from `1 - down_price`)
2. Fix PM outcome determination (reduce 7-11% unknown rate)
3. Re-run calibration analysis with fixed data
4. Verify PM last_trade_price at 0.90+ predicts outcome as well as Kalshi ask

**Mechanics (if data validates):**
- Signal: PM last_trade_price ≥ 0.90 (or ≤ 0.10)
- Entry: buy UP (or DOWN) token on PM CLOB
- Fee: $0.002 at 0.90 vs $0.006 on Kalshi — saves $0.004/contract
- Exit: settlement

**Risk**: Need PM trading infrastructure (currently only collect, don't trade). Need to verify PM book can absorb our order sizes.

### Direction 3: Multi-Source Signal Combination (MEDIUM PRIORITY)

**Thesis**: Combining Kalshi book, PM last_trade, and multi-source spot (Coinbase + Kraken + Binance) might produce a more accurate signal than any single source.

**Specific ideas:**
- **RTDS-adjusted distance**: Instead of Coinbase spot vs Kalshi strike, use RTDS (Binance) vs PM-implied strike. This aligns with PM's resolution source.
- **Spot consensus**: When Coinbase, Kraken, and Binance all agree (price is up/down from round start), signal quality should be higher. When they diverge, skip.
- **Book-trade divergence**: If Kalshi book says 0.70 but PM last_trade says 0.55, one is stale. Can we systematically identify which one?

**Data needed**: This can be explored with existing data (all spot feeds are captured). But PM book data is unreliable (Bug 1).

### Direction 4: PM Miscalibration Exploitation (MEDIUM — needs verification)

**Thesis**: PM contracts in the 0.30-0.80 range are systematically underpriced by 5-10% (prior finding, needs re-verification).

**Why it might be real:**
- PM books are thin (resting at 0.01/0.99)
- Market makers aren't actively quoting tight spreads
- Traders must actively cross the spread to express views
- With PM's tiny fees at mid-range prices (~$0.008 at 0.50), even small miscalibration is exploitable

**Why it might be an artifact:**
- 7.5% of 15m rounds have unknown outcomes — if these are systematically "close" rounds, excluding them biases calibration
- last_trade_price only tracks UP token — if DOWN token activity is heavier in certain regimes, our view is incomplete

**Before pursuing:** Fix collector bugs, re-run with complete data.

### Direction 5: 5m PM Rounds as Leading Indicator for 15m Kalshi (LOW-MEDIUM)

**Thesis**: Three PM 5m rounds complete within each Kalshi 15m round. If the first 5m round resolves "up" on PM, that's a leading signal for the 15m Kalshi outcome.

**Why it might work:**
- 5m resolution is a hard data point (not just a price)
- If BTC is up after 5 minutes, it's more likely to be up after 15 minutes
- This signal is uncorrelated with spot distance (it's about realized outcome, not current price)

**Why it might not:**
- 5m→15m persistence may be weak (mean reversion over 15m)
- Resolution source differs (Chainlink vs CF Benchmarks)
- 5m rounds resolve 5 minutes into the 15m round — the Kalshi market has already repriced by then

**Data needed**: Match 5m PM outcomes to corresponding 15m Kalshi rounds. All data exists.

### Direction 6: Volatility Regime Filter (LOW PRIORITY)

**Thesis**: Low-volatility rounds have higher signal accuracy (92.4% vs 76.4%) but the market only partially prices this in.

**Problem**: Small samples (n=19 for low-vol ETH). Need more data to validate. And defining "low vol" in real-time is tricky — by the time you measure it, the opportunity may be gone.

---

## Data Improvements Needed (Priority Order)

### 1. Fix PM Collector (Critical)
- Track DOWN token trades: `last_trade_price = 1 - down_price` when DOWN trades
- Fix `best_bid_ask` attribution: either parse asset_id from the event or track per-token
- Outcome determination: use DOWN token trades for fallback

### 2. Add PM CLOB API Midpoint (Important)
- PM CLOB API `/midpoint` endpoint returns the real midpoint
- Currently we compute `(0.01+0.99)/2 = 0.50` — useless
- API midpoint would give us the actual market-implied probability

### 3. Collect Book Depth (Nice to Have)
- Currently only top-of-book
- Full book depth would allow: order impact analysis, depth-weighted midpoint, volume-at-price

### 4. Log Down Token Book Separately (Nice to Have)
- Track `down_best_bid`, `down_best_ask` from WS `book` events (which do include asset_id)
- Don't rely on complement calculation

---

## What I Would NOT Pursue

1. **Cross-coin consensus**: 57-67% accuracy, negative EV. Finding 8 stands.
2. **Momentum/direction signals**: Collinear with distance, adds no independent information. Finding 17 stands.
3. **Late-round (T>600) strategies**: Market is already correctly priced at extremes. Finding 5 stands.
4. **Spread farming/market making**: Spread < fee. Finding 7 stands.
5. **Complex ML models on Kalshi data alone**: GBM added marginal improvement but the feature space is thin. The information edge doesn't exist; what exists is behavioral/structural.
6. **Cross-platform arbitrage**: 96.2% same outcome → only 3.8% of rounds diverge, and we can't predict which ones ex ante.

---

## Recommended Next Steps

1. **Immediate**: Fix PM collector bugs (1-2 hours of code changes)
2. **This week**: Re-collect 2-3 days of PM data with fixed collector
3. **Then**: Re-run PM calibration analysis with fixed data → validate or invalidate Direction 4
4. **Parallel**: Implement ETH-only limit order strategy (Direction 1) on Kalshi — test fill rates
5. **If PM miscalibration is real**: Build PM trading client, paper trade Direction 2
