# Sync Data from VM

Pull the latest data files from the GCP VM to local for analysis. This syncs rounds, trades, and alerts.

## Steps

1. **Sync data via compressed tar over gcloud SSH**:
```bash
mkdir -p /Users/kj/Code/Personal/prediction-market-bots/data/{rounds/polymarket,trades,alerts}
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'tar czf - -C ~/prediction-market-bots/data rounds trades alerts' | \
  tar xzf - -C /Users/kj/Code/Personal/prediction-market-bots/data/
```

2. **Verify local data**:
```bash
echo "=== LOCAL DATA ===" && \
for dir in data/rounds data/rounds/polymarket data/trades data/alerts; do
  count=$(ls -1 /Users/kj/Code/Personal/prediction-market-bots/$dir/ 2>/dev/null | wc -l)
  size=$(du -sh /Users/kj/Code/Personal/prediction-market-bots/$dir 2>/dev/null | cut -f1)
  echo "$dir: $count files, $size"
done
```

3. **Report**: Show file counts and total size synced. Flag any directories that are empty or missing.
