# Prediction Market Trading Bots

## Current Focus
Kalshi short-duration crypto binary markets. Polymarket and cross-platform arb are deferred.

## Project Structure
- `shared/` — Shared infrastructure (clients, execution, risk, alerts, ws, utils)
- `bots/kalshi_crypto/` — Crypto bot: discovery, strategy, sizing, main loop
- `scripts/` — Data collection, backtesting, analysis (not production code)
- `tests/` — Test suite
- `data/trades/` — CSV trade logs (one per day)
- `data/rounds/` — Collected round snapshots for analysis
- `research/` — Knowledge base: sources index, extracted insights, deep dives

## Conventions
- Python 3.9+, async-first (httpx, websockets)
- `from __future__ import annotations` in every file
- Decimal for all prices — never use float for money
- Kalshi: ONE order book per market (no_price = 1 - yes_price)
- All API clients accept raw credentials in `__init__`, no global singletons
- WS handlers push PriceUpdate objects onto asyncio.Queue
- Every bot uses `shared/runner.py` BotRunner for lifecycle management
- Kill switch checked on every loop iteration
- Paper trading is the default — set PAPER_TRADING=false for live
- Risk limits are percentage-based so they scale with balance
- Kill switch: touch `KILL` file in project root to emergency stop

## Commands
- `source venv/bin/activate` — Activate virtualenv
- `pytest` — Run all tests
- `python scripts/smoke_test_kalshi.py` — Verify Kalshi connectivity + alerts
- `ruff check .` — Lint

## Deployment & Operations
- GCP VM: `<GCP_ZONE>`, `35.245.140.169`, project `<GCP_PROJECT>`
- 5 Docker services: bot + 4 collectors (btc, eth, sol, xrp)
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
- `research/empirical-findings.md` — Our own data analysis results
- `research/strategy-evaluation.md` — What we've tested: ruled out, validated, deferred
- `research/open-questions.md` — TODOs and things to investigate
- New topic files can be created for deep dives (e.g., `research/kelly-sizing.md`)

## Strategy Principles
- Spot distance from strike is the signal — not direction, not momentum, not contract divergence
- T+600-800 is the window — earlier is too noisy, later the contract has caught up
- 0.2% distance threshold — below this, prediction accuracy drops to near-random
- Market reprices faster than expected — median 14-21s lag, not minutes
- XRP is untradeable — Coinbase spot has zero correlation with CF Benchmarks for XRP
- Cross-coin consensus doesn't work — 57-67% accuracy, negative EV after fees
- ETH is the best coin — most consistent edge across all windows and thresholds
- Fees are manageable at extremes — $0.003-0.006 at $0.90+
- WebSocket over REST — by the time REST round-trips, opportunity is gone
- Simple mechanical rules over complex models
