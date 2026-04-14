# Codebase Walkthrough

A self-study guide covering everything in this repo. Read top to bottom for
a full tour, or jump to a section.

---

## Contents

1. [The big picture](#1-the-big-picture)
2. [Repo layout](#2-repo-layout)
3. [`shared/` — reusable infrastructure](#3-shared--reusable-infrastructure)
4. [`bots/kalshi_whale/` — the event-driven bot](#4-botskalshi_whale--the-event-driven-bot)
5. [`bots/kalshi_crypto/` — the per-round bot](#5-botskalshi_crypto--the-per-round-bot)
6. [`scripts/` — operational tools](#6-scripts--operational-tools)
7. [`sim/` — Monte Carlo harness](#7-sim--monte-carlo-harness)
8. [`tests/` — pytest suite](#8-tests--pytest-suite)
9. [Deployment & operations](#9-deployment--operations)
10. [How to extend — adding a new bot](#10-how-to-extend--adding-a-new-bot)
11. [Current state & open questions](#11-current-state--open-questions)

---

## 1. The big picture

This is a framework for running prediction-market trading bots. A bot is a
directory under `bots/`. Generic infrastructure — HTTP clients, WebSocket
feeds, order execution, risk, alerts — lives in `shared/` and gets composed.

Two bots exist today:

- **`bots/kalshi_whale/`** (running in paper mode) — listens for large
  ("whale") trades on Kalshi sports/economics markets, follows them in,
  stops at 40% loss, holds winners to settlement.
- **`bots/kalshi_crypto/`** (paused) — trades Kalshi's 15-minute crypto
  markets via the cascade strategy (PM 5m slot-1 → Kalshi 15m YES).

Collectors under `scripts/` snapshot market data to CSV; paused today.

### Design principles

- **WebSocket first** — the edge is gone by the time REST round-trips.
  REST is only for discovery and settlement verification.
- **Paper trading is the default** — `PAPER_TRADING=true` in `.env` unless
  explicitly flipped. Same `place_order` interface for paper and live.
- **Decimal for every price** — no float drift in money math.
- **Risk checked before every order** — three independent guards.
- **Bot template pattern** — drop a new dir into `bots/`; don't touch
  `shared/` unless you need a new primitive.

---

## 2. Repo layout

```
prediction-market-bots/
├── README.md, CLAUDE.md, OPS.md
├── docker-compose.yml, Dockerfile, pyproject.toml, .env.example
│
├── bots/                    ← strategy code, one dir per bot
│   ├── kalshi_whale/        ← event-driven
│   └── kalshi_crypto/       ← per-round
│
├── shared/                  ← reusable infrastructure
│   ├── clients/             ← REST HTTP clients (Kalshi, Polymarket)
│   ├── ws/                  ← WebSocket feeds (typed events onto queues)
│   ├── execution/           ← place_order abstraction (live + paper)
│   ├── risk/                ← kill switch, risk limits, circuit breaker
│   ├── alerts/              ← Telegram + file logging
│   ├── utils/               ← Decimal parsing, retry decorator, logging
│   ├── config.py, types.py, runner.py, fees.py, summary.py, trade_log.py
│
├── scripts/                 ← collectors + operational tools
├── sim/                     ← Monte Carlo harness
├── tests/                   ← pytest suite (144 tests)
├── research/                ← mechanics reference for Kalshi + Polymarket
├── docs/                    ← this file
└── data/                    ← CSVs, alert logs, runtime state (gitignored)
```

---

## 3. `shared/` — reusable infrastructure

Bottom-up dependency order (a depends on what comes before it).

### `shared/config.py`

One pydantic `Settings` class. Reads env at startup. Holds Kalshi creds,
Telegram config, risk thresholds, `paper_trading` flag, log level.

### `shared/types.py`

Shared vocabulary. Enums (`Side`, `Outcome`, `OrderStatus`) and dataclasses
(`OrderRequest`, `OrderResponse`, `Position`, `PriceUpdate`). These are the
interfaces that flow between layers.

### `shared/fees.py`

One function: `kalshi_fee(price, contracts) = ceil_to_cent(0.07 × C × P ×
(1 − P))`. Used by both bots' sizing code and by paper execution fills.

### `shared/utils/`

Three tiny helpers:

- `decimals.py` — `dec(value)` safely parses to `Decimal | None`.
- `retry.py` — `@http_retry` decorator, retries 5xx / 401 / 429 with
  exponential backoff. Wraps every HTTP call.
- `logging.py` — JSON or human-readable log format; injects `bot_name`
  into every record.

### `shared/clients/` — REST

- **`kalshi.py`** — `KalshiClient`. Kalshi uses RSA-PSS key-pair auth;
  `_sign()` builds the signature. Exposes `fetch_market`, `fetch_events`,
  `get_balance`, `get_positions`, `place_order`, `cancel_order`. Also
  `sign_ws()` for WebSocket auth headers.
- **`polymarket.py`** — `PolymarketClient`. No auth (read-only Gamma API).
  Discovers crypto markets by building predictable slugs like
  `btc-updown-5m-1773150900`.

### `shared/ws/` — WebSocket feeds

The theme: **WS feeds don't make decisions**. They convert raw JSON into
typed dataclasses and put them on an `asyncio.Queue`. Bots consume.

- **`kalshi.py`** — `KalshiWSManager`. Subscribes to `ticker` + `trade`
  channels. Batches subscribes (500/batch — Kalshi rejects larger).
  Reconnect with exponential backoff (1s → 60s), 30s recv timeout with ping.
- **`spot.py`** — `SpotWSFeed` (Coinbase) and `KrakenWSFeed` (secondary).
  Real-time crypto spot. Pushes `SpotPriceUpdate`.
- **`polymarket.py`** — two feeds. `PolymarketMarketWSFeed` for PM order
  books + trades + `market_resolved` events. `PolymarketRTDSFeed` for PM's
  Binance/Chainlink price channel.

### `shared/execution/` — placing orders

The key abstraction. A bot calls `engine.place_order(order)` and doesn't
know if it's live or paper.

- **`base.py`** — `AbstractExecutionEngine` ABC. Contract: `place_order`,
  `cancel_order`, `cancel_all`, `get_open_orders`, `get_positions`,
  `get_balance`.
- **`kalshi.py`** — `KalshiExecutionEngine`. Maps `OrderRequest` to
  Kalshi's REST. Always IOC (immediate-or-cancel) with a 2¢ price cushion
  on buys. Converts NO prices via `yes_price = 100 - price_cents`.
  Static helpers `_map_status`, `_get_fill_count`, `_compute_fill_price`
  parse Kalshi v2 response fields.
- **`paper.py`** — `PaperExecutionEngine`. Same interface, fills
  immediately at order price + optional slippage bps. Persists balance to
  `data/paper_balance.json` (survives Docker restarts).

### `shared/risk/` — three guards

Every order goes through all three. Any one can reject.

- **`kill_switch.py`** — `KillSwitch`. File-based (`touch KILL`),
  error-based (N consecutive HTTP errors), and optional loss-based.
  Raises `KillSwitchTriggered` → `BotRunner` catches → exit code 42.
- **`limits.py`** — `RiskLimits`. Async `check(order, engine, balance,
  equity)`. Per-trade size %, total exposure %, orders-per-minute rate
  limit. Returns `RiskCheckResult(allowed, reason)`.
- **`circuit_breaker.py`** — `CircuitBreaker`. Daily loss %, drawdown %
  from ATH, N consecutive losses. **Persists state to
  `data/circuit_breaker.json`** so Docker restarts don't erase mid-day
  state. Dynamic drawdown tiers: <$500 → 70% allowed, $500–1k → 60%,
  $1k–5k → 50%, $5k+ → 40%.

### `shared/alerts/`

- **`telegram.py`** — one function `send_telegram(token, chat_id, msg)`.
- **`manager.py`** — `AlertManager`. Formats bot events as Telegram
  messages (`bot_started`, `whale_entry`, `whale_settled`, `daily_summary`,
  etc.). Mirrors every message to `data/alerts/<bot>-YYYY-MM-DD.log` so
  you have a local timeline even when Telegram is down.

### Root of `shared/`

- **`runner.py`** — `BotRunner`. Installs SIGINT/SIGTERM handlers, runs
  the bot coroutine, catches `KillSwitchTriggered`, sends shutdown alert.
  Every `main.py` ends with `runner.run(run_bot, settings, runner, alerts)`.
- **`summary.py`** — `midnight_summary_loop(summary_fn)`. Sleeps until
  midnight CST, calls a bot-supplied closure, loops.
- **`trade_log.py`** — `TradeLog`. CSV logger (used only by
  `kalshi_crypto` — whale has its own `tracking.py`).

---

## 4. `bots/kalshi_whale/` — the event-driven bot

**Shape:** four concurrent async loops communicating through asyncio queues.

### What it does

1. Every 60s, discover Kalshi sports + economics markets closing soon.
2. Keep a WebSocket subscribed to `trade` channel for every watchlist market.
3. When 3+ trades ≥ $1000 land within 30 min with ≥90% agreement on one
   side, emit a `WhaleSignal`.
4. Buy that side at current ask, capped to favorites ($0.85–$0.95).
5. Monitor the position: stop-loss at 40% below entry, else hold to settlement.

### Files

```
strategy.py   (99)   ← WhaleConfig, WhaleTrade, WhaleSignal, MarketWhaleState
discovery.py  (200)  ← REST polls /markets, builds Watchlist
signal.py     (402)  ← long-lived WS loop, emits WhaleSignal onto queue
sizing.py     (79)   ← phase-based half_port (100% under $500, tapers to 10%)
monitor.py    (416)  ← stop-loss watcher + settlement poller
tracking.py   (322)  ← full-fidelity CSV audit log (every event)
main.py       (552)  ← entry point + orchestration of 7 concurrent tasks
```

### Data flow

```
discovery (60s REST) ──► Watchlist (shared state)
                              │
                              ▼
              signal.py (Kalshi WS: ticker + trade channels)
                              │
                              ▼    WhaleSignal
                        signal_queue (asyncio.Queue)
                              │
                              ▼
                  _signal_consumer in main.py
             (dedup → risk gates → sizing → place order)
                              │
                              ▼
                         monitor.py
           (stop-loss via WS, settlement via REST poll)
```

### Key files in detail

- **`strategy.py`** — pure dataclasses, no logic. `WhaleConfig` holds every
  tunable: whale_threshold=$1000, min_whale_count=3, consensus_pct=0.90,
  price range 0.85–0.95, 30-min window, max_concurrent=2, stop_loss=0.40.
  `MarketWhaleState.add_trade()` accumulates, `consensus_side` /
  `consensus_pct` compute on the fly.
- **`signal.py`** — the beating heart. One WS connection. Subscribes
  `WS_SUBSCRIBE_BATCH=500` tickers at a time. Every incoming trade is
  filtered by notional ≥ $1000 and added to the market's state. `_maybe_
  emit_signal()` checks: ≥3 whales in last 30 min, ≥90% consensus, ticker
  ask in [0.85, 0.95]. Emits once per market.
- **`sizing.py`** — `compute_size(price, balance)`. Phases:
  ```
  < $500      → 100% per slot
  $500 – 1k   → 50%
  $1k – 5k    → 30%
  $5k – 50k   → 20%
  > $50k      → 10%
  ```
  **Not Kelly.** This is an ad-hoc schedule from a Monte Carlo sim, tuned
  for small-bankroll bootstrap. Full Kelly at 95% WR would be ~50% — this
  is more aggressive at low balances.
- **`monitor.py`** — two watchers:
  - `run_price_monitor()` consumes `price_queue` from `signal.py`. Fires
    an IOC sell when bid ≤ entry × 0.60. `STOP_LOSS_PRICE_FLOOR=$0.05` —
    books too illiquid below that; hold to settlement.
  - `run_settlement_poller()` REST polls positions. When resolved,
    computes PnL, logs, clears.
- **`tracking.py`** — `WhaleTracker`. Every event type writes a row to
  `data/trades/kalshi-whale-YYYY-MM-DD.csv`: WATCHLIST, WHALE_TRADE,
  SIGNAL_PASS, SIGNAL_SKIP (with reason), ENTRY, STOP_LOSS, SETTLEMENT,
  ROUND_SUMMARY. This is your post-mortem audit trail.
- **`main.py`** — orchestrator. Spawns 7 tasks; `asyncio.wait(return_when=
  FIRST_EXCEPTION)` — any crash cancels everything. Key helpers extracted
  in Pass 3: `_event_already_entered`, `_pass_risk_gates`, `_execute_entry`.

---

## 5. `bots/kalshi_crypto/` — the per-round bot

**Shape:** outer loop iterates rounds. Each round is a finite execution.

### What it does

1. Discover the current 15-minute market for each coin (BTC, ETH, XRP).
2. Open Kalshi WS + Coinbase spot WS.
3. For 15 min, tick-by-tick run strategies against latest prices.
4. On signal → size (Kelly) → risk check → place order.
5. Round ends → settle → summary → next round.

### Files

```
strategy.py            (68)   ← BaseStrategy ABC, RoundContext, TradeSignal
discovery.py           (136)  ← find active round per series
sizing.py              (125)  ← fractional Kelly with fee model
strategies/
  cascade.py           (129)  ← ACTIVE: PM 5m slot-1 → Kalshi YES
  spot_distance.py     (101)  ← disabled (negative EV); kept as reference
pm_signal.py           (162)  ← polls PM for 5m resolutions
round.py               (670)  ← run_round(): one 15-min round end-to-end
main.py                (440)  ← outer loop: discover → run_round → repeat
```

### Data flow

```
main.py outer loop:
│
├─ discover active rounds (one RoundContext per series)
├─ spawn midnight-summary task
│
├─ await run_round(contexts, strategies, ...)
│     │
│     │ SETUP
│     │   subscribe Kalshi WS to this round's tickers
│     │   start PM 5m signal pollers (one task per coin)
│     │   strategies' on_round_start()
│     │
│     │ MAIN LOOP (until seconds_remaining == 0)
│     │   drain spot + kalshi queues → latest_spots, latest_kalshi
│     │   for each strategy: signals = on_update(ctx, kalshi, spot)
│     │   for each signal: size → risk → place order (live + shadow)
│     │
│     │ TEARDOWN
│     │   _teardown_round: stop WS, cancel PM pollers, cancel orders
│     │   _settle_paper_engines: use Kalshi resolution to settle paper
│     │   _compute_live_pnl: per-fill PnL from settled markets
│     │   _build_shadow_summary: same for shadow paper engine
│     │   alerts.round_summary(...)
│     │
│     └─ return {trades, wins, pnl, signals, balance_after}
│
└─ sleep until next round
```

### Strategy plugin pattern

`BaseStrategy` has 3 methods: `on_round_start`, `on_update`, `on_round_end`.
The runner knows nothing about what a strategy decides — it just calls
`on_update` each tick and executes whatever `TradeSignal` list comes back.

Adding a new strategy = write a class that extends `BaseStrategy`. Wire it
in `main.py:184`.

### The cascade strategy (active)

Kalshi 15m round contains three PM 5-minute slots. Slot 1 (minute 0–5)
resolves with ~10 min remaining on Kalshi. **Claim:** if PM slot 1 resolves
"up," Kalshi 15m YES resolves YES more often than the Kalshi book implies
(71–76% WR on 600+ validated samples per coin).

Strategy reads the shared `pm_signals: dict[coin → str]` that `pm_signal.py`
populates. When SR ≈ 600 and pm_signals[coin] == "up", emit BUY YES.

### Shadow paper mode

When live, crypto bot runs a shadow `PaperExecutionEngine` in parallel.
Every signal executes against both engines. Round summary alerts show
`live balance | shadow paper balance`. Lets you see what the strategy
would have done without slippage/fill failures.

---

## 6. `scripts/` — operational tools

### Collectors (run 24/7 in Docker on the VM)

- **`collect_rounds.py`** — Kalshi round snapshotter. Uses `KalshiWSManager`.
  Row per second during a round (timestamp, round_ticker, spot_price,
  yes_bid/ask, no_bid/ask, volume), plus a `row_type=round_end` row at
  close. Output: `data/rounds/kalshi/KX<coin>15M-YYYY-MM-DD.csv`.
- **`collect_polymarket.py`** — PM equivalent. Uses `PolymarketMarketWSFeed`
  + `PolymarketRTDSFeed` + Coinbase spot. One file per coin × duration ×
  day.

Both paused after the 04-13 OOM on e2-small.

### Operational tools (run locally when needed)

- **`performance.py`** — real-money reporter. Queries Kalshi API for
  settlements + fills, parses alert logs for signals. `--sync` rsyncs from
  VM first.
- **`smoke_test_kalshi.py`** — end-to-end connectivity check (REST + WS +
  Telegram).
- **`audit_data.py`** — CSV completeness audit. Reports gaps and stale
  timestamps.
- **`snapshot_coverage.py`** — per-time-zone density. Useful when
  validating a time-windowed strategy.

---

## 7. `sim/` — Monte Carlo harness

Simulates a strategy N thousand times to see the equity curve distribution.

```
config.py      (94)   ← SimConfig, SweepConfig, TradeParams
fees.py        (20)   ← float-based kalshi_fee (for speed in hot loop)
sizing.py      (101)  ← float mirrors of sizing strategies
engine.py      (245)  ← vectorized numpy MC engine
outputs.py     (188)  ← CSV export + summary
whale_sim.py   (149)  ← CLI entry point
```

**How it works.** You describe a trade distribution and a config. The
engine simulates all N paths simultaneously using numpy arrays — no
per-sim python loop. Day by day, it samples outcomes, applies sizing +
fees + stops, tracks equity.

**Output.** Median final, p5 / p95 percentiles, max drawdown distribution,
days-to-milestones ($1k / $10k / $100k). Sweeps (`SweepConfig`) find
robust regions of the design space.

**Why it exists.** Before the whale strategy went to paper, the sim
validated: does 100%-under-$500 phase sizing compound? What drawdowns
should we expect? The answer drove the dynamic drawdown tiers in
`CircuitBreaker`.

Run: `python -m sim.whale_sim --quick --sims 1000 --days 30`.

---

## 8. `tests/` — pytest suite

```
test_discovery.py   (27)    ← Kalshi discovery: parsing, filtering
test_sizing.py      (51)    ← Kelly sizer + fee math
test_paper.py       (86)    ← PaperExecutionEngine
test_polymarket.py  (148)   ← PM slug construction, collector logic
test_strategies.py  (215)   ← SpotDistance + Cascade
test_risk.py        (312)   ← all 3 risk guards
test_whale.py       (1,143) ← whale bot core
```

**Covered.** Happy paths, zero-balance edge cases, partial fills, IOC
no-fills, day rollover, breaker state persistence, fee rounding, Kelly
math, fill-price inversion (regression).

**Not covered.** WebSocket feeds (integration-heavy), live execution (no
mock Kalshi), main-loop orchestration (too brittle; helpers are unit-
tested separately).

`asyncio_mode=auto` in `pyproject.toml` → async tests need no decorator.

Run: `pytest`, `pytest tests/test_risk.py`, `pytest -k kelly`.

---

## 9. Deployment & operations

### VM

GCP Compute Engine, zone `<GCP_ZONE>`, project `<GCP_PROJECT>`,
instance `<VM_NAME>`, type `e2-small` (2 vCPU / 2 GB).

### Deploy

**Never `git pull` on the VM.** Laptop is source of truth. Workflow:

```bash
rsync -avz --exclude='.env' --exclude='venv/' --exclude='data/' \
  --exclude='__pycache__/' --exclude='.git/' --exclude='*.pyc' \
  -e "ssh -i ~/.ssh/google_compute_engine" \
  ./ kj@<VM_IP>:~/prediction-market-bots/

gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose up -d --build'
```

### Emergency controls

- `touch KILL` in project root → kill switch fires on next loop iteration.
- `docker compose stop whale` → stop just the bot.
- `rm data/circuit_breaker.json` → force-reset breaker state.

See `OPS.md` for the full runbook.

### Services (from `docker-compose.yml`)

All use YAML anchors (`x-bot-base`, `x-collector-base`, `x-default-logging`)
so shared config (restart policy, volumes, log rotation) lives in one
place. 30 MB per-container log cap.

Active: `whale`. Paused: `collector-{btc,eth,sol,xrp}`, `poly-{5m,15m,1h,4h}-{btc,eth,sol,xrp}` (4×4=16).

---

## 10. How to extend — adding a new bot

The framework is built to make this cheap. Two templates to copy:

### Per-round bot (pattern: `kalshi_crypto/`)

Use when the market has a discrete cadence (15-min rounds, hourly
windows, daily closes).

```
bots/<new>/
  strategy.py       ← BaseStrategy ABC + RoundContext + TradeSignal
  strategies/       ← concrete strategies (inherit BaseStrategy)
  discovery.py      ← find active round(s)
  sizing.py         ← position sizing
  main.py           ← outer loop: discover → run_round → repeat
  round.py          ← per-round execution (subscribe → run → settle)
```

### Event-driven bot (pattern: `kalshi_whale/`)

Use when markets are always-on and signals are rare.

```
bots/<new>/
  strategy.py       ← config dataclass + signal types
  discovery.py      ← maintain a watchlist
  signal.py         ← WS loop; emits signals onto a queue
  sizing.py         ← position sizing
  monitor.py        ← position monitor (stop-loss + settlement)
  main.py           ← spawn 4 concurrent loops
```

### What `shared/` gives you for free

- API clients (`shared/clients/`)
- WS feeds (`shared/ws/`)
- Paper + live execution engines (`shared/execution/`)
- Risk guards (`shared/risk/`)
- Telegram + file alerts (`shared/alerts/`)
- BotRunner lifecycle (`shared/runner.py`)
- Midnight summary loop (`shared/summary.py`)
- Kalshi fee math (`shared/fees.py`)

You should only modify `shared/` if you genuinely need a new primitive
(e.g. adding a new exchange's client).

---

## 11. Current state & open questions

### What's running right now

- Whale bot in **paper mode**, $300 starting bankroll, on the GCP VM.
- 40% stop-loss tolerance (widened from 15% after the first stop-out
  storm on 04-14).
- Collectors all paused (OOM'd on 04-13).

### Key open questions (from the 04-14 paper-run analysis)

- **True win rate.** Sim assumed 95% WR, 35-day backtest confirmed it.
  First real paper day (04-14) showed **70% WR** — 23 wins / 10 stops out
  of 33 trades. The stops are expensive because sizing is aggressive (~50%
  of bankroll per trade at small balances).
- **Sizing aggression.** Current "phase-based half_port" at ≤$500 is
  effectively **full Kelly at p=0.95**. If true p is 0.85 or lower, this
  overbets and log-growth goes negative. Options: switch to ¼ Kelly with
  conservative `p`, or reduce the <$500 phase from 100% → 50%.
- **Position sizing story.** Did the 3-stop cluster on 04-14 represent
  true tail risk, or a one-off? Need more paper days before deciding.
- **Collectors.** Paused for now. Re-enable when there's a new strategy
  that needs the data. e2-small can't run all 20 collectors + the bot
  together — either stagger, or upsize the VM, or prune the collector set.

### Cleanup done (history on branch `cleanup/presentation-ready`)

- Pass 1 — root docs, README, DRY compose with YAML anchors
- Pass 2 — `shared/risk.py` split into package; KillSwitch default bug
  fix; Polymarket EDT → `ZoneInfo`; Kalshi execution helpers into class;
  legacy API v1 fallback removed
- Pass 3 — `shared/fees.py` and `shared/summary.py` extracted; `max_loss_
  pct=999.0` hack replaced with `None`; `run_round()` split into 4
  helpers; `_signal_consumer()` split into 3 helpers; magic numbers named
- Pass 4 — `scripts/` pruned 25 → 9 files (−8,957 LOC); data layout
  normalized (`data/rounds/kalshi/` + `data/rounds/polymarket/`)
- Pass 5 — `sim/` and `tests/` docstring fixes + lint polish
- Pass 6 — stale file pruning (strategy-evaluation.md, old whale scripts,
  `sim/charts.py`)

Cumulative: **−10,219 LOC**. 144/144 tests passing. Lint clean.

---

## Quick reference — common tasks

| Task | Command |
|---|---|
| Run tests | `pytest` |
| Lint | `ruff check .` |
| Run whale bot locally (paper) | `python -m bots.kalshi_whale.main` |
| Run sim (quick) | `python -m sim.whale_sim --quick --sims 1000 --days 30` |
| SSH to VM | `gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT>` |
| Check bot health on VM | Use the `health-check` skill, or see `OPS.md` |
| Pull data from VM | Use the `sync-data` skill |
| Performance report | `python scripts/performance.py --sync` |
| Emergency stop bot | `touch KILL` in project root |
| Reset circuit breaker | `rm data/circuit_breaker.json && docker compose restart whale` |
