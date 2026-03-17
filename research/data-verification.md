# Data Verification Report

*Generated 2026-03-17. Verification scripts: `scripts/verify_data.py`, `scripts/verify_data_phase2.py`.*

## Summary

| Platform | Verdict | Key Issues |
|:--|:--|:--|
| **Kalshi** | **GOOD** — data is reliable | Minor: 85/2529 outcomes differ from Coinbase spot (CF Benchmarks divergence, expected) |
| **Polymarket** | **SIGNIFICANT ISSUES** — book data unreliable, outcomes 7-11% unknown | `best_bid_ask` bug, UP-only trade tracking, high unknown rate |
| **Cross-platform** | **GOOD** — 96.2% agreement where both resolve | 3.8% disagree = resolution source difference |

---

## Kalshi Data (2,529 rounds, 9 days, 4 coins)

### Timing: PASS
- `seconds_remaining + seconds_elapsed` sums to ~900s in **every single snapshot** (0 timing issues)
- Avg 798 snapshots per round (~1/second as configured)

### Outcomes: PASS
- 50.2% yes / 49.8% no — balanced, no collection bias
- **Zero "unknown" outcomes** — the API retry loop (6 attempts × 5s) always gets the result
- 85/2529 (3.4%) rounds where our Coinbase spot would predict a different outcome than the Kalshi API returned
  - All are close calls (spot within 0.07% of strike)
  - Expected: Kalshi resolves on CF Benchmarks (60s TWAP across multiple exchanges), not Coinbase spot
  - Example: `KXBTC15M-26MAR101545-45` — spot $69,993.84, strike $70,020.65 (spot says "no"), but CF Benchmarks says "yes" (likely Kraken/Bitstamp were higher)

### Book Quality: PASS
- Missing spot: 0.06%, Missing book: 0.07% — negligible
- BTC has median 90 unique (yes_bid, yes_ask) pairs per round — active repricing
- 2,062 rounds show >30s of unchanged book — but this is normal when the market is one-sided (book sits at 0.99/1.00 or 0.00/0.01)

### Data Gaps: MINOR
- Mar 8, Mar 14: only ~12 rounds/day/coin (collection started/restarted mid-day)
- Mar 9, 11, 15, 16: 94-96 rounds/day (near-complete)
- Mar 10, 12, 17: 61-87 rounds (some gaps)
- **Impact**: Reduces sample size but doesn't bias the data

### Conclusion: Kalshi data is trustworthy for analysis
The collector correctly captures WS updates, writes accurate snapshots, and reliably fetches API outcomes. The 3.4% Coinbase/CF Benchmarks divergence is inherent to the resolution mechanism, not a data bug.

---

## Polymarket Data (6,412 rounds across 5m/15m/4h, 8 days, 4 coins)

### BUG 1: `best_bid_ask` events misattributed to UP token — CONFIRMED

**Root cause**: `polymarket.py:277` creates `PolymarketBookUpdate` with `asset_id=""` for `best_bid_ask` events (the event format doesn't include asset_id). In `collect_polymarket.py:433`:

```python
if bu.asset_id == up_token_id or not bu.asset_id:
    best_up_bid = bu.best_bid  # ALL best_bid_ask go here
```

Empty string is falsy → all `best_bid_ask` events route to the UP token bucket.

**Evidence from data**:
- 88.3% of "real" book episodes (where up_bid ≠ 0.01) last ≤3 snapshots, then revert to 0.01/0.99
- These are DOWN token `best_bid_ask` events being written to `up_bid`/`up_ask`
- Only 0.7% of real book episodes are sustained (>30 snapshots)
- Book quality increases from 15% real at round start → 83% at round end (near settlement, UP token book genuinely tightens)

**Impact**: `up_bid`, `up_ask`, `up_midpoint`, and `spread` columns are **unreliable**. They randomly contain DOWN token prices. Any analysis using these fields is suspect.

**What's NOT affected**: `last_trade_price` (tracked separately), `spot_price`, `rtds_price`, outcomes via `market_resolved` events.

### BUG 2: Only UP token trades recorded — CONFIRMED

**Root cause**: `collect_polymarket.py:455`:
```python
if tu.asset_id == up_token_id:
    last_trade_price = tu.price
```

DOWN token trades are silently dropped.

**Impact**:
- `last_trade_price` is always the UP token's perspective (P(up)). This is *consistent* but *incomplete*.
- When only DOWN token trades occur, `last_trade_price` goes stale
- Outcome determination fails when last trade is ambiguous (0.10-0.90): 93% of unknown outcomes
- **7.5% of 15m rounds and 11.0% of 5m rounds have "unknown" outcome** because of this

**Mitigation**: Could infer UP implied price from DOWN trades as `1 - down_price`.

### BUG 3: `up_midpoint` is useless — CONFIRMED

- 51.6% of 15m snapshots have midpoint ≈ 0.50 (from 0.01/0.99 book)
- Even the "real" midpoints are contaminated by Bug 1 (DOWN token values in UP columns)
- **This field should never be used for analysis**

### BUG 4: `down_bid`/`down_ask` mostly derived from complement

- 68% of snapshots: `down_bid = 1 - up_ask`, `down_ask = 1 - up_bid` (exact complement)
- 32% show independent values (when a DOWN `book` event arrives before being overwritten)
- On Polymarket, UP and DOWN tokens have independent order books that need not sum to 1.00
- **These fields add no independent information** in our current collection

### Outcome Quality: MODERATE

| Duration | Total | Unknown | Unknown % |
|:--|:--|:--|:--|
| 5m | 4,596 | 504 | 11.0% |
| 15m | 1,713 | 128 | 7.5% |
| 4h | 103 | 8 | 7.8% |

For unknown outcomes:
- 93-96% have last_trade_price in the ambiguous range (0.10-0.90)
- Coinbase spot direction for unknowns: ~53% up (near random — these are genuinely close calls)
- Most unknowns are rounds where `market_resolved` WS event was not received and the price-based fallback couldn't determine the outcome

### What PM data CAN be used for

Despite the bugs, some data is reliable:
1. **`last_trade_price`** — consistent UP token view, 96-98% availability, avg 190 unique price changes per 15m round
2. **`spot_price`** (Coinbase), **`kraken_price`**, **`rtds_price`** (Binance) — all independently sourced
3. **`outcome`** for resolved rounds (92.5% of 15m) — 97.5% agree with Coinbase spot direction
4. **Volume** — from discovery API, not affected by WS bugs
5. **Round timing** — `end_date`, `seconds_remaining` are correct

### What PM data CANNOT be used for

1. **`up_bid`/`up_ask`** — contaminated by DOWN token events
2. **`up_midpoint`** — derived from contaminated bid/ask
3. **`down_bid`/`down_ask`** — mostly complement of contaminated up_bid/up_ask
4. **`spread`** — derived from contaminated bid/ask
5. **Outcome for "unknown" rounds** — 7-11% of all rounds

---

## Cross-Platform Verification

### Time Alignment: PASS
- PM round end minutes: 0, 15, 30, 45 — correctly aligned with Kalshi
- 385 of 633 Kalshi BTC rounds matched to PM (61%) — gap is because PM collection started later
- Timestamp-based matching (±120s) works correctly

### Outcome Agreement: GOOD
- 340 matched BTC 15m rounds where both resolved
- **327/340 (96.2%) agree, 13/340 (3.8%) disagree**
- 3.8% disagreement = CF Benchmarks (Kalshi) vs Chainlink/Binance (PM) resolution source difference
- This is an inherent characteristic, not a data bug

### Implication for Cross-Platform Strategies
The 3.8% disagreement rate means cross-platform arbitrage on identical outcomes is possible but the edge is small. Both platforms get the same answer >96% of the time.

---

## Impact on Prior Analysis

### V3 Analysis Findings That Are VALIDATED:
- Finding 13 (Kalshi negative EV at ask): Based on Kalshi data → **valid**
- Finding 14 (ETH-only positive EV): Based on Kalshi data → **valid**
- Finding 15 (Entry price matters): Based on Kalshi data → **valid**
- Finding 16 (Market calibration at $0.60-$0.80): Based on Kalshi data → **valid**
- Finding 17 (Other signals no value): Based on Kalshi data → **valid**
- Finding 20 (PM inversion bug): **Confirmed as a real bug** (best_bid_ask misattribution), not just token mapping

### V3 Analysis Findings That Are INVALIDATED or SUSPECT:
- **PM miscalibration of 5-10%** (from v3-data-analysis.md): Used `last_trade_price` which IS reliable, BUT 7.5% of rounds have unknown outcomes that were excluded. If unknowns are biased (they skew toward "close" rounds), the calibration analysis has selection bias.
- **PM $0.05 EV/trade at 0.30-0.80**: Needs re-verification with fixed data. The signal (`last_trade_price`) is ok but the outcome data is incomplete.
- **"PM books are 0.01/0.99"**: True for the UP token — but we're also accidentally seeing DOWN token values flicker through. The real UP book IS thin (0.01/0.99), confirmed.
- **GBM model features using PM book data**: Any feature using `up_bid`, `up_ask`, `up_midpoint`, or PM book data is contaminated.

### What Needs to Be Redone:
1. PM calibration analysis using only resolved rounds (exclude unknowns)
2. Any cross-platform analysis using PM book data
3. PM outcome determination — fix collector to track DOWN token trades
