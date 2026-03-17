# VM Operations Guide

Quick reference for the Kalshi trading bot and data collectors running on GCP.

## Connection
```bash
# SSH shorthand (all commands below assume this prefix)
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT>

# Or direct SSH
ssh -i ~/.ssh/google_compute_engine kj@<VM_IP>
```

## Service Status
```bash
# Are all 21 services running?
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose ps'
```
Expected: 21 services all STATUS "Up":
- `bot` — Kalshi trading bot
- 4 Kalshi collectors: `collector-btc`, `collector-eth`, `collector-sol`, `collector-xrp`
- 16 PM collectors: `poly-{duration}-{coin}` for each of 5m/15m/1h/4h x btc/eth/sol/xrp

## Logs

### Bot (trading activity)
```bash
# Recent bot logs (signals, trades, round starts)
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose logs bot --tail 50'

# Follow live
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose logs -f bot'
```

### Alert log (human-readable timeline)
```bash
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cat ~/prediction-market-bots/data/alerts/$(date -u +%Y-%m-%d).log'
```
Contains: BOT STARTED, ROUND start/end, SIGNAL, SKIP, TRADE FILLED, DAILY SUMMARY.

### Kalshi collector logs
```bash
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose logs collector-btc --tail 10'
```

### PM collector logs
```bash
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose logs poly-5m-btc --tail 10'
```

## Data Files

### Round snapshots (collector output)
```bash
# List round CSVs and sizes
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'ls -lh ~/prediction-market-bots/data/rounds/'

# Row counts
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'wc -l ~/prediction-market-bots/data/rounds/*.csv'
```
Kalshi files: `data/rounds/KXBTC15M-YYYY-MM-DD.csv` (one per coin per day).
Columns: timestamp, round_ticker, strike, seconds_remaining, seconds_elapsed, spot_price, yes_bid, yes_ask, no_bid, no_ask, volume, spot_minus_strike, spot_move_pct, row_type, outcome, kraken_spot.
Special rows: `row_type=round_end` has the settlement outcome.

PM files: `data/rounds/polymarket/BTC-5m-YYYY-MM-DD.csv` (one per coin per duration per day).
Columns: timestamp, slug, condition_id, coin, end_date, seconds_remaining, up_token_id, down_token_id, up_bid, up_ask, down_bid, down_ask, midpoint, spread, last_trade_price, last_trade_side, volume, spot_price, kraken_price, rtds_price, row_type, outcome.
Special rows: `row_type=round_end` has the resolution outcome (up/down).

### Trade logs
```bash
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'ls -lh ~/prediction-market-bots/data/trades/ && tail -20 ~/prediction-market-bots/data/trades/*.csv 2>/dev/null || echo "No trades yet"'
```
Files: `data/trades/kalshi-crypto-multi-YYYY-MM-DD.csv` (created when first trade executes).

### Pull data to laptop
```bash
rsync -avz -e "ssh -i ~/.ssh/google_compute_engine" \
  kj@<VM_IP>:~/prediction-market-bots/data/ \
  /Users/kj/Code/Personal/prediction-market-bots/data/
```

## Quick Health Check (all-in-one)
```bash
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- '
cd ~/prediction-market-bots
echo "=== SERVICES ==="
docker compose ps --format "table {{.Service}}\t{{.Status}}"
echo ""
echo "=== LAST 5 ALERTS ==="
tail -5 data/alerts/$(date -u +%Y-%m-%d).log 2>/dev/null || echo "No alerts today"
echo ""
echo "=== KALSHI ROUND DATA ==="
wc -l data/rounds/KX*.csv 2>/dev/null || echo "No Kalshi round data"
echo ""
echo "=== PM ROUND DATA ==="
wc -l data/rounds/polymarket/*.csv 2>/dev/null || echo "No PM round data"
echo ""
echo "=== TRADES ==="
ls -lh data/trades/*.csv 2>/dev/null || echo "No trades yet"
echo ""
echo "=== BOT (last 10 lines) ==="
docker compose logs bot --tail 10 2>&1 | tail -10
'
```

## Emergency Controls
```bash
# Stop bot only (all collectors keep running)
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose stop bot'

# Resume bot
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose start bot'

# Restart just Kalshi collectors
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose restart collector-btc collector-eth collector-sol collector-xrp'

# Restart just PM collectors (all 16)
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose restart $(docker compose config --services | grep poly-)'

# Stop everything
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose stop'

# Restart everything
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose restart'
```

## Circuit Breaker Reset
The circuit breaker persists state to `data/circuit_breaker.json`. If the bot is stuck on a drawdown kill switch (e.g. balance dropped >40% from ATH), the bot will sleep until midnight and auto-reset. To manually force a reset:
```bash
# View current state
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cat ~/prediction-market-bots/data/circuit_breaker.json'

# Reset: delete state file and restart bot (bot creates fresh state on startup)
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'rm ~/prediction-market-bots/data/circuit_breaker.json && cd ~/prediction-market-bots && docker compose restart bot'
```

## Resource Monitoring
21 Docker services on e2-small (~2 vCPU, 2GB RAM). Monitor if things get tight:
```bash
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" | head -25'
```

## Deploy Code Update
```bash
# From laptop: rsync + rebuild
rsync -avz --exclude='.env' --exclude='venv/' --exclude='data/' \
  --exclude='__pycache__/' --exclude='.git/' --exclude='*.pyc' \
  -e "ssh -i ~/.ssh/google_compute_engine" \
  /Users/kj/Code/Personal/prediction-market-bots/ \
  kj@<VM_IP>:~/prediction-market-bots/

gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose up -d --build'
```

## What to Tell Claude
When asking Claude to check on the bot, say one of:
- "Check on the bot" → runs the quick health check
- "Show me today's signals" → reads alert log
- "Show me recent trades" → checks trade CSVs
- "Pull data" → rsyncs data to laptop
- "How's the bot doing?" → health check + last N bot log lines
- "Deploy latest code" → rsync + docker compose up --build
