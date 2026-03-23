# Deploy to GCP VM

Deploy the prediction market bots to the GCP VM via rsync. NEVER use `git pull` on the VM.

## Steps

1. **Run tests first**: `source venv/bin/activate && pytest`
   - If any tests fail, fix them and re-run until green. Do NOT deploy with failing tests.

2. **Deploy via rsync**:
```bash
rsync -avz --exclude='.env' --exclude='venv/' --exclude='data/' \
  --exclude='__pycache__/' --exclude='.git/' --exclude='*.pyc' \
  -e "ssh -i ~/.ssh/google_compute_engine" \
  /Users/kj/Code/Personal/prediction-market-bots/ \
  kj@<VM_IP>:~/prediction-market-bots/
```

3. **Rebuild Docker services on VM**:
```bash
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose up -d --build'
```

4. **Verify all 21 services are running**:
```bash
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose ps --format "table {{.Service}}\t{{.Status}}"'
```

5. **Check logs for errors in first 60 seconds**:
```bash
gcloud compute ssh <VM_NAME> --zone=<GCP_ZONE> --project=<GCP_PROJECT> -- \
  'cd ~/prediction-market-bots && docker compose logs --tail 20 bot'
```

6. **Report status**: Show which services are up, any errors, and confirm deploy is clean.

If anything fails at steps 4-5, show the failure and propose a fix but do NOT rollback without user approval.
