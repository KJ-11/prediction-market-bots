# VM Operations

Quick reference for the bot + collectors running on GCP.

## SSH

```bash
# Preferred (uses gcloud auth)
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT>

# Direct SSH alternative
ssh -i ~/.ssh/google_compute_engine <SSH_USER>@<VM_IP>
```

All commands below assume the gcloud form. The prefix is abbreviated as `SSH --` where it would repeat.

VM: `<VM_NAME>`, zone `<GCP_ZONE>`, project `<GCP_PROJECT>`, type `e2-small` (2 vCPU / 2 GB RAM).

## Services

Defined in `docker-compose.yml`:
- `whale` — `bots.kalshi_whale.main`, currently running in paper mode
- `collector-{btc,eth,sol,xrp}` — Kalshi 15m round snapshots
- `poly-{5m,15m,1h,4h}-{btc,eth,sol,xrp}` — Polymarket 4×4 = 16 collectors

**Current state: only `whale` is running.** Collectors are paused (OOM on e2-small when all ran together). Restart any with `docker compose up -d <service>`.

```bash
# Which services are up?
SSH -- 'cd ~/prediction-market-bots && docker compose ps'
```

## Logs

```bash
# Bot (live)
SSH -- 'cd ~/prediction-market-bots && docker compose logs -f whale'

# Today's alert log (human-readable: signals, trades, settlements, daily summary)
SSH -- 'cat ~/prediction-market-bots/data/alerts/kalshi-whale-$(date -u +%Y-%m-%d).log'

# Collector logs
SSH -- 'cd ~/prediction-market-bots && docker compose logs collector-btc --tail 20'
SSH -- 'cd ~/prediction-market-bots && docker compose logs poly-5m-btc --tail 20'
```

## Data

```
data/rounds/kalshi/KX*15M-YYYY-MM-DD.csv   # Kalshi collector (one per coin per day)
data/rounds/polymarket/{coin}-{dur}-*.csv  # Polymarket collector (one per coin × duration × day)
data/trades/*.csv                          # Executed trades (live + paper)
data/alerts/*.log                          # Human-readable event stream
data/circuit_breaker.json                  # Daily loss + drawdown state
data/paper_balance.json                    # Paper engine persistent balance
```

Pull data to laptop: run the `sync-data` skill, which does a tar-over-ssh of `rounds/`, `trades/`, `alerts/`.

## Health check

```bash
SSH -- '
cd ~/prediction-market-bots
echo "=== SERVICES ==="
docker compose ps --format "table {{.Service}}\t{{.Status}}"
echo "=== LAST ALERTS ==="
tail -5 data/alerts/kalshi-whale-$(date -u +%Y-%m-%d).log 2>/dev/null
echo "=== DATA SIZES ==="
du -sh data/rounds data/trades data/alerts 2>/dev/null
echo "=== BOT (last 10) ==="
docker compose logs whale --tail 10 2>&1 | tail -10
'
```

## Deploy

`rsync` from laptop → VM, then rebuild. Never `git pull` on the VM — the laptop is source of truth.

```bash
rsync -avz --exclude='.env' --exclude='venv/' --exclude='data/' \
  --exclude='__pycache__/' --exclude='.git/' --exclude='*.pyc' \
  -e "ssh -i ~/.ssh/google_compute_engine" \
  /Users/kj/Code/Personal/prediction-market-bots/ \
  <SSH_USER>@<VM_IP>:~/prediction-market-bots/

SSH -- 'cd ~/prediction-market-bots && docker compose up -d --build'
```

## Emergency controls

```bash
# Stop bot (collectors keep running, if they were up)
SSH -- 'cd ~/prediction-market-bots && docker compose stop whale'

# Resume bot
SSH -- 'cd ~/prediction-market-bots && docker compose start whale'

# Kill switch (immediate stop, checked on every loop)
SSH -- 'touch ~/prediction-market-bots/KILL'

# Clear kill switch
SSH -- 'rm ~/prediction-market-bots/KILL'

# Stop everything
SSH -- 'cd ~/prediction-market-bots && docker compose stop'
```

## Circuit breaker

Persists to `data/circuit_breaker.json` (survives restarts). On daily loss + drawdown breach the bot auto-pauses 24 h. Manual reset:

```bash
# Inspect
SSH -- 'cat ~/prediction-market-bots/data/circuit_breaker.json'

# Reset (delete + restart bot recreates fresh state)
SSH -- 'rm ~/prediction-market-bots/data/circuit_breaker.json && \
        cd ~/prediction-market-bots && docker compose restart whale'
```

## Resource monitoring

```bash
SSH -- 'docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" | head -25'
SSH -- 'df -h /'
```

e2-small is tight. The whale bot alone uses ~55 MB. Running all 21 services together is what caused the OOM on 04-13.
