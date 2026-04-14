# Prediction Market Trading Bots

## Current Focus
Building prediction market trading bots across Kalshi and Polymarket. Crypto (15-min binary markets) is the first vertical, but the shared infrastructure and bot template pattern (`shared/` + `bots/<vertical>/`) are designed to expand into elections, sports, economic indicators, weather, and any other Kalshi/PM market type. Strategy is being reworked from the ground up — no assumed edge.

## Project Structure
- `shared/` — Reusable infrastructure (clients, execution, risk, alerts, ws, utils)
- `bots/kalshi_crypto/` — Reference per-round bot (discovery → round execution); template for round-based verticals
- `bots/kalshi_whale/` — Event-driven bot (continuous WS → signal → monitor); template for event-based verticals
- `scripts/` — Data collectors (`collect_rounds.py`, `collect_polymarket.py`), performance reporting, smoke tests
- `tests/` — Test suite
- `data/` — Trade logs, round snapshots, alert logs, circuit breaker state, lifecycle metadata
- `research/` — Market mechanics reference (`kalshi-mechanics.md`, `polymarket-mechanics.md`)

## Building a New Bot
Two templates depending on market structure. Shared pieces: `discovery.py`, `strategy.py`, `sizing.py`, `main.py`.

**Per-round (see `bots/kalshi_crypto/`)** — markets open and close on a fixed cadence (e.g. 15-min crypto).
1. **`discovery.py`** — Find active markets for this round
2. **`strategy.py`** — Define `RoundContext`, `TradeSignal`, `BaseStrategy` ABC
3. **`strategies/`** — Concrete strategy implementations (e.g., `spot_distance.py`)
4. **`sizing.py`** — Fractional Kelly with Kalshi fee model
5. **`main.py`** — Entry point: setup engines/risk/strategies, main discovery loop
6. **`round.py`** — Round execution: subscribe to markets, run strategies, execute signals, settle

**Event-driven (see `bots/kalshi_whale/`)** — continuously listen for signals across many markets (e.g. whale trades).
1. **`discovery.py`** — Maintain a watchlist of active markets
2. **`strategy.py`** — Signal types + config (no per-round abstraction)
3. **`signal.py`** — Long-lived WS loop; emits `WhaleSignal` onto a queue when criteria met
4. **`sizing.py`** — Phase-based half_port sizing (balance-tier allocation)
5. **`main.py`** — Entry point: spawns 4 concurrent loops (discovery / detector / signal consumer / monitor)
6. **`monitor.py`** — Position monitor: stop-loss via WS price feed, settlement via REST poll

`shared/` provides: API clients, execution engines (live + paper), risk management (kill switch, circuit breaker, risk limits), WebSocket feeds, alerting (Telegram + file), trade logging, bot lifecycle runner.

## Data Sources & Feeds
- **Coinbase WS** (`shared/ws/spot.py: SpotWSFeed`) — primary spot price for BTC/ETH/SOL/XRP
- **Kraken WS** (`shared/ws/spot.py: KrakenWSFeed`) — secondary spot price for cross-validation
- **Kalshi WS** (`shared/ws/kalshi.py: KalshiWSManager`) — real-time order book updates
- **PM Market WS** (`shared/ws/polymarket.py: PolymarketMarketWSFeed`) — PM order book, trades, market_resolved
- **PM RTDS** (`shared/ws/polymarket.py: PolymarketRTDSFeed`) — Polymarket's Binance/Chainlink price feed
- All WS feeds push typed dataclass updates onto `asyncio.Queue`

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

## Deployment & Operations
- **Deploy method: rsync to GCP VM. NEVER use `git pull` on the VM.**
- GCP VM: `<GCP_ZONE>`, `<VM_IP>`, e2-small (2GB RAM)
- GCP project: `<GCP_PROJECT>` (named "Bots"), account: `kshjhun@gmail.com`
  - NOT `profitlabs` — that is a different project with a different account (`<CONTACT_EMAIL>`)
- **GCP config:** Always use `gcloud config configurations activate bots` before running gcloud commands. Verify with `gcloud config configurations list`.
- Docker services: see `docker-compose.yml`. Whale bot + Kalshi collectors (4 coins) + Polymarket collectors (4 coins × 4 durations). Collectors are currently paused; re-enable by `docker compose up -d <name>`.
- See `OPS.md` for full operations guide (health checks, logs, data, deploy, emergency controls)
- SSH: `gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT>`
- When diagnosing production issues, always check VM state (SSH + docker logs), not local state.

## Risk Rules
- Never place orders without kill switch check
- Always check RiskLimits before placing orders
- Paper trade every strategy before going live
- Position sizing must be derived from validated data — never assume win rates or edge sizes without sufficient sample (96+ rounds minimum). Use fractional Kelly at most.

## Strategy Principles
- No assumed edge — every strategy must be validated from data before going live
- EV per contract matters more than WR alone
- Paper trade every strategy before going live
- WebSocket over REST — by the time REST round-trips, opportunity is gone
- Simple mechanical rules over complex models

## Before Going Live Checklist
1. Paper trade for 96+ rounds minimum with sufficient sample across market conditions
2. Validate edge from data — no assumed win rates or edge sizes
3. Confirm risk limits are set (percentage-based)
4. Verify kill switch works (`touch KILL` in project root)
5. Check circuit breaker state is clean (`data/circuit_breaker.json`)
6. Set `PAPER_TRADING=false` explicitly (default is paper mode)
7. Monitor first live session end-to-end via Telegram alerts

## Git & Accounts

- **GCP:** `gcloud config configurations activate bots` → `kshjhun@gmail.com` / project `<GCP_PROJECT>`. **NOT** the ProfitLabs account.
- **Supabase:** Not used in this project.

## Vault Integration

Research findings (strategy research, market mechanics) get routed to `Code/Vault/Projects/Bots/actions.md`. Strategy notes go in `Code/Vault/Projects/Bots/Notes/`.

