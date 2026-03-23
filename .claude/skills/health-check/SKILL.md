# VM Health Check

Run the all-in-one health check on the GCP VM. Do NOT check local state.

## Steps

1. **SSH and run full health check**:
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
'
```

2. **Summarize**: Report service count (expect 21 up), any down services, last alerts, data freshness, and any issues found.

3. **If issues found**: Check relevant docker logs for the failing service and report root cause.
