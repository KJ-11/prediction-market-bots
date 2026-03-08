# VM Operations Guide

Quick reference for checking on the Kalshi trading bot running on GCP.

## Connection
```bash
# SSH shorthand (all commands below assume this prefix)
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT>

# Or direct SSH
ssh -i ~/.ssh/google_compute_engine kj@35.245.140.169
```

## Service Status
```bash
# Are all 5 services running?
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose ps'
```
Expected: bot + 4 collectors (btc, eth, sol, xrp), all STATUS "Up".

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

### Collector logs
```bash
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose logs collector-btc --tail 10'
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
Files: `data/rounds/KXBTC15M-YYYY-MM-DD.csv` (one per coin per day).
Columns: timestamp, round_ticker, strike, seconds_remaining, seconds_elapsed, spot_price, yes_bid, yes_ask, no_bid, no_ask, volume, spot_minus_strike, spot_move_pct, row_type, outcome, kraken_spot.
Special rows: `row_type=round_end` has the settlement outcome.

### Trade logs
```bash
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'ls -lh ~/prediction-market-bots/data/trades/ && tail -20 ~/prediction-market-bots/data/trades/*.csv 2>/dev/null || echo "No trades yet"'
```
Files: `data/trades/YYYY-MM-DD.csv` (created when first trade executes).

### Pull data to laptop
```bash
rsync -avz -e "ssh -i ~/.ssh/google_compute_engine" \
  kj@35.245.140.169:~/prediction-market-bots/data/ \
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
echo "=== ROUND DATA ==="
wc -l data/rounds/*.csv 2>/dev/null || echo "No round data"
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
# Stop bot only (collectors keep running)
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose stop bot'

# Resume bot
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose start bot'

# Stop everything
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose stop'

# Restart everything
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose restart'
```

## Deploy Code Update
```bash
# From laptop: rsync + rebuild
rsync -avz --exclude='.env' --exclude='venv/' --exclude='data/' \
  --exclude='__pycache__/' --exclude='.git/' --exclude='*.pyc' \
  -e "ssh -i ~/.ssh/google_compute_engine" \
  /Users/kj/Code/Personal/prediction-market-bots/ \
  kj@35.245.140.169:~/prediction-market-bots/

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
