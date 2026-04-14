# Prediction Market Bots

Automated trading bots for [Kalshi](https://kalshi.com) and [Polymarket](https://polymarket.com).

Built as a shared-infrastructure monorepo: each bot lives under `bots/<name>/` and composes the same primitives from `shared/` (API clients, WebSocket feeds, execution engines, risk management, alerting). New verticals (sports, elections, weather, crypto) are added by dropping in a new directory, not by forking.

## Status

Running one strategy in production (paper mode) and collecting market data for the next.

- **`bots/kalshi_whale/`** — follows large Kalshi traders ("whales") into sports + economics markets. Currently paper-trading a $300 bankroll on GCP.
- **`bots/kalshi_crypto/`** — reference implementation for 15-minute Kalshi crypto markets. Paused; retained as a template.
- **Collectors** — Kalshi + Polymarket round snapshots, feeding future strategy work. Paused; see `scripts/collect_rounds.py`, `scripts/collect_polymarket.py`.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     bots/<vertical>/                         │
│  discovery.py → strategy.py → sizing.py → main.py → monitor  │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                         shared/                              │
│                                                              │
│  clients/   — Kalshi + Polymarket REST + signed auth         │
│  ws/        — async WebSocket feeds (Kalshi, PM, Coinbase,   │
│               Kraken); all push typed dataclasses onto       │
│               asyncio.Queue                                  │
│  execution/ — live + paper execution engines (same interface)│
│  risk/      — kill switch, circuit breaker, risk limits      │
│  alerts/    — Telegram + structured file logging             │
│  runner.py  — bot lifecycle (signals, shutdown, heartbeat)   │
└──────────────────────────────────────────────────────────────┘
```

**Design choices worth naming:**

- **WebSocket-first.** By the time a REST round-trip completes, the edge is gone. Every data source is a long-lived WS with reconnection + backoff; handlers push typed dataclass events onto `asyncio.Queue`s that the bot consumes. REST is reserved for discovery (listing markets) and settlement verification.
- **Paper trading is the default.** `PAPER_TRADING=true` is the default in `.env`; live mode requires explicit opt-in. The paper engine exposes the exact same `place_order` / `get_positions` / `get_balance` interface as the live engine, so bots are paper/live-agnostic.
- **`Decimal` for every price.** Kalshi prices are quoted to the cent; float drift in money math is never worth the speed.
- **Risk checks before every order.** Kill switch (file-based + error-based), circuit breaker (daily loss + drawdown, persisted to disk), and `RiskLimits` (per-trade size, exposure, rate limits) are checked on every signal — not just at bot start.
- **Bot template pattern.** A new bot is one directory: `discovery.py` + `strategy.py` + `sizing.py` + `main.py` (setup) + per-bot execution file (`round.py` for per-round bots, `signal.py` + `monitor.py` for event-driven bots). `shared/` stays thin and reusable.

## Quickstart

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env           # fill in Kalshi keys + Telegram (optional)

# Run the whale bot in paper mode (default)
python -m bots.kalshi_whale.main

# Run tests
pytest

# Lint
ruff check .
```

Emergency stop: `touch KILL` in the project root. Every bot checks for this file on every loop iteration.

## Directory map

| Path | Purpose |
|------|---------|
| `bots/` | Trading strategies. Each subdir is a self-contained bot. |
| `shared/` | Reusable infrastructure (clients, execution, risk, ws, alerts). |
| `scripts/` | Data collectors, performance report, smoke tests, data audits. |
| `sim/` | Monte Carlo simulation harness for strategy validation. |
| `tests/` | Pytest suite. |
| `research/` | Market mechanics reference for Kalshi and Polymarket. |
| `data/` | Trade logs, round snapshots, alert logs, runtime state. Gitignored. |

## Further reading

- `docs/WALKTHROUGH.md` — full codebase tour: layer-by-layer explanation of
  every directory and file, design decisions, and how to extend.
- `CLAUDE.md` — conventions, risk rules, pre-live checklist.
- `OPS.md` — deployment and operations on the GCP VM.
- `research/` — market mechanics for Kalshi and Polymarket.
