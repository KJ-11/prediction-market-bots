# Prediction Market Trading Bots

## Current Focus
Kalshi short-duration crypto binary markets (live trading). Polymarket data collection (16 collectors across 4 coins x 4 durations). Cross-platform arb and PM trading strategies are deferred until we have sufficient PM data.

## Project Structure
- `shared/` — Shared infrastructure (clients, execution, risk, alerts, ws, utils)
- `bots/kalshi_crypto/` — Crypto bot: discovery, strategy, sizing, main loop
- `scripts/` — Data collection (collect_rounds.py for Kalshi, collect_polymarket.py for PM), backtesting, analysis
- `tests/` — Test suite
- `data/trades/` — CSV trade logs (one per day)
- `data/rounds/` — Collected round snapshots for analysis
- `data/alerts/` — Daily alert logs (synced from VM via rsync)
- `data/lifecycle.json` — Bot version lifecycle metadata (deposits, start times, balances)
- `research/` — Knowledge base: sources index, extracted insights, deep dives

## Data Sources & Feeds
- **Coinbase WS** (`shared/ws/spot.py: SpotWSFeed`) — primary spot price for BTC/ETH/SOL/XRP
- **Kraken WS** (`shared/ws/spot.py: KrakenWSFeed`) — secondary spot price for cross-validation (Kalshi resolves on CF Benchmarks which samples multiple exchanges)
- **Kalshi WS** (`shared/ws/kalshi.py: KalshiWSManager`) — real-time order book updates (yes/no bid/ask/size/volume)
- **PM Market WS** (`shared/ws/polymarket.py: PolymarketMarketWSFeed`) — PM order book, trades, and market_resolved events
- **PM RTDS** (`shared/ws/polymarket.py: PolymarketRTDSFeed`) — Polymarket's Binance/Chainlink price feed (PM resolution source)
- All WS feeds push typed dataclass updates onto `asyncio.Queue` for consumption by bots/collectors

## Key Data Files
- `data/rounds/KXBTC15M-YYYY-MM-DD.csv` — Kalshi round snapshots (one per coin per day)
- `data/rounds/polymarket/BTC-5m-YYYY-MM-DD.csv` — PM round snapshots (one per coin per duration per day)
- `data/trades/kalshi-crypto-multi-YYYY-MM-DD.csv` — bot trade logs
- `data/alerts/YYYY-MM-DD.log` — human-readable alert timeline
- `data/circuit_breaker.json` — persisted circuit breaker state (ATH, consecutive losses, stopped_for_day)
- `data/lifecycle.json` — bot version lifecycle metadata

## Conventions
- Python 3.9+, async-first (httpx, websockets)
- `from __future__ import annotations` in every file
- Decimal for all prices — never use float for money
- Kalshi: ONE order book per market (no_price = 1 - yes_price)
- Polymarket: two tokens per market (up/down), resolves on Chainlink oracle (Binance price)
- All API clients accept raw credentials in `__init__`, no global singletons
- WS handlers push typed dataclass updates onto asyncio.Queue
- Every bot uses `shared/runner.py` BotRunner for lifecycle management
- Kill switch checked on every loop iteration
- Circuit breaker state persists to `data/circuit_breaker.json` (survives Docker restarts)
- Paper trading is the default — set PAPER_TRADING=false for live
- Risk limits are percentage-based so they scale with balance
- Kill switch: touch `KILL` file in project root to emergency stop

## Commands
- `source venv/bin/activate` — Activate virtualenv
- `pytest` — Run all tests
- `python scripts/smoke_test_kalshi.py` — Verify Kalshi connectivity + alerts
- `python scripts/performance.py` — Performance report (queries Kalshi API, parses alert logs)
- `python scripts/performance.py --sync` — Same, but rsync alert logs from VM first
- `ruff check .` — Lint

## Versioning & Performance Tracking
- Bot versions are git-tagged: `bot-v1`, `bot-v2`, etc. Tag on meaningful strategy/code changes.
- `data/lifecycle.json` — Tracks bot lifecycle: version, start time, starting balance, deposits, withdrawals
- `scripts/performance.py` — Performance report using Kalshi API (fills + settlements) as source of truth, alert logs for signal analytics
- Each version in lifecycle.json has a `start_time_utc` — only fills after this time are counted for that version's P&L
- When deploying a new bot version: update lifecycle.json with new version entry, tag git (`git tag -a bot-vN -m "description"`)

## Deployment & Operations
- GCP VM: `<GCP_ZONE>`, `<VM_IP>`, e2-small (2GB RAM)
- GCP project: `<GCP_PROJECT>` (named "Bots"), account: `kshjhun@gmail.com`
  - NOT `profitlabs` — that is a different project with a different account (`<CONTACT_EMAIL>`)
- 21 Docker services: bot + 4 Kalshi collectors + 16 PM collectors (4 coins x 4 durations)
- See `OPS.md` for full operations guide (health checks, logs, data, deploy, emergency controls)
- SSH: `gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT>`

## Risk Rules
- Never place orders without kill switch check
- Always check RiskLimits before placing orders
- Paper trade every strategy before going live
- Position sizing must be derived from validated data — never assume win rates or edge sizes without sufficient sample (96+ rounds minimum). Use fractional Kelly at most.

## Research Workflow
When the user shares a research article, paper, link, or interesting finding:
1. **Read/fetch** the content and understand it
2. **Add to `research/sources.md`** — title, URL, date, tags, one-line summary
3. **Extract key insights into `research/insights.md`** — file under the right topic heading, reference the source
4. **Flag anything actionable** — if an insight contradicts current strategy, suggests a new strategy, or changes risk parameters, call it out explicitly so we can discuss whether to act on it
5. **Update research files if needed** — if findings are directly relevant to our current strategies, integrate into the appropriate research file

The `research/` directory is our knowledge base:
- `research/sources.md` — Index of all sources with tags and summaries
- `research/insights.md` — Extracted insights organized by topic
- `research/kalshi-mechanics.md` — How Kalshi crypto markets work (resolution, fees, limits, discovery)
- `research/polymarket-mechanics.md` — How PM crypto markets work (resolution, tokens, WS events)
- `research/polymarket-roadmap.md` — What to do with PM data: analysis plan, strategy ideas, infrastructure
- `research/empirical-findings.md` — Our own data analysis results
- `research/strategy-evaluation.md` — What we've tested: ruled out, validated, deferred
- `research/open-questions.md` — TODOs and things to investigate

## Strategy Principles
- Spot distance from strike is the signal — not direction, not momentum, not contract divergence
- T+250-500 is the window (v2) — earlier entry captures cheaper prices with comparable accuracy
- 0.15% distance threshold (v2) — lower WR but cheaper avg price = higher EV/contract
- SOL dropped (v2) — worst coin by WR (79%), BTC/ETH/XRP only
- Kelly 30% (v2) — captures more of validated edge
- EV per contract matters more than WR alone — $0.065 EV at 83% WR beats $0.049 EV at 87% WR
- Market reprices faster than expected — median 14-21s lag, not minutes
- Cross-coin consensus doesn't work — 57-67% accuracy, negative EV after fees
- ETH is the best coin — most consistent edge across all windows and thresholds
- Fees are manageable at extremes — $0.003-0.006 at $0.90+
- WebSocket over REST — by the time REST round-trips, opportunity is gone
- Simple mechanical rules over complex models
