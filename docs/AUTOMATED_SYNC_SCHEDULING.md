# Automated Sync Scheduling Guide

Complete guide to setting up automated model & pricing syncs that run at specific times.

---

## 🎯 Overview

You have **multiple scheduling options**:

1. **Built-in Scheduler** (Already Running!) - Syncs every N hours
2. **Cron Jobs** - Run at specific times (e.g., 2 AM daily)
3. **GitHub Actions** - Cloud-based scheduled workflows
4. **Railway Cron** - Built-in Railway cron jobs
5. **Vercel Cron** - Vercel Edge Functions cron
6. **External Services** - EasyCron, Cronitor, etc.

---

## ✅ Option 1: Built-in Scheduler (Easiest)

### Current Status
Your app **already has an automatic scheduler running!**

**Location**: `src/services/pricing_sync_scheduler.py`
**Started**: Automatically on app startup
**Configuration**: Environment variables

### How It Works
```python
# Runs automatically every N hours (default: 6)
PRICING_SYNC_ENABLED=true
PRICING_SYNC_INTERVAL_HOURS=6
```

### Change Sync Frequency

```bash
# Sync every 2 hours
PRICING_SYNC_INTERVAL_HOURS=2

# Sync every 12 hours
PRICING_SYNC_INTERVAL_HOURS=12

# Sync every 24 hours (daily)
PRICING_SYNC_INTERVAL_HOURS=24
```

### Advantages
✅ No setup needed - already running
✅ Auto-restarts if app restarts
✅ Built-in error handling
✅ Prometheus metrics
✅ Automatic cleanup of stuck syncs

### Disadvantages
❌ Can't specify exact time (e.g., "2 AM daily")
❌ Runs every N hours from app start

---

## ⏰ Option 2: Cron Jobs (Most Control)

### Setup Time-Based Scheduling

Run syncs at **specific times** (e.g., 2 AM, 8 AM, 2 PM, 8 PM daily).

#### Step 1: Create Cron Script

Save as `scripts/cron_sync.sh`:

```bash
#!/bin/bash
# Automated cron sync script
# Run via: crontab -e

set -e

# Configuration
API_URL="${API_URL:-https://api.gatewayz.ai}"
LOG_FILE="${LOG_FILE:-/var/log/gatewayz_cron_sync.log}"

# Timestamp
echo "========================================" >> "$LOG_FILE"
echo "Sync started: $(date -u +"%Y-%m-%d %H:%M:%S UTC")" >> "$LOG_FILE"

# Run full sync
echo "Triggering full model & pricing sync..." >> "$LOG_FILE"
RESPONSE=$(curl -s -X POST "${API_URL}/admin/model-sync/all" 2>&1)

# Check result
if echo "$RESPONSE" | jq -e '.success' > /dev/null 2>&1; then
    MODELS_SYNCED=$(echo "$RESPONSE" | jq -r '.details.total_models_synced // 0')
    echo "✅ Sync completed: $MODELS_SYNCED models synced" >> "$LOG_FILE"
else
    echo "❌ Sync failed: $RESPONSE" >> "$LOG_FILE"
    exit 1
fi

echo "Sync finished: $(date -u +"%Y-%m-%d %H:%M:%S UTC")" >> "$LOG_FILE"
```

```bash
# Make executable
chmod +x scripts/cron_sync.sh
```

#### Step 2: Configure Crontab

```bash
# Edit crontab
crontab -e
```

Add one of these schedules:

```bash
# ============================================
# Schedule Examples (Choose One)
# ============================================

# Every 6 hours (at 00:00, 06:00, 12:00, 18:00 UTC)
0 */6 * * * /path/to/gatewayz-backend/scripts/cron_sync.sh

# Twice daily (2 AM and 2 PM UTC)
0 2,14 * * * /path/to/gatewayz-backend/scripts/cron_sync.sh

# Daily at 2 AM UTC
0 2 * * * /path/to/gatewayz-backend/scripts/cron_sync.sh

# Every 4 hours
0 */4 * * * /path/to/gatewayz-backend/scripts/cron_sync.sh

# Business hours (8 AM - 8 PM UTC, every 2 hours)
0 8,10,12,14,16,18,20 * * * /path/to/gatewayz-backend/scripts/cron_sync.sh

# Weekdays only, 3 AM UTC
0 3 * * 1-5 /path/to/gatewayz-backend/scripts/cron_sync.sh
```

#### Step 3: Verify Cron Setup

```bash
# List current cron jobs
crontab -l

# Test script manually
./scripts/cron_sync.sh

# Check logs
tail -f /var/log/gatewayz_cron_sync.log
```

### Cron Schedule Reference

```
* * * * *  Command to execute
│ │ │ │ │
│ │ │ │ └─── Day of week (0-7, 0 or 7 = Sunday)
│ │ │ └───── Month (1-12)
│ │ └─────── Day of month (1-31)
│ └───────── Hour (0-23)
└─────────── Minute (0-59)

Examples:
0 2 * * *     = Daily at 2:00 AM
0 */6 * * *   = Every 6 hours
30 8 * * 1-5  = Weekdays at 8:30 AM
0 0,12 * * *  = Twice daily (midnight and noon)
*/15 * * * *  = Every 15 minutes
```

---

## 🚀 Option 3: GitHub Actions (Cloud-Based)

### Setup Scheduled GitHub Actions Workflow

**Advantages**:
✅ No server needed - runs in cloud
✅ Free for public repos
✅ Email notifications on failure
✅ Can run multiple schedules
✅ Works even if your server is down

Create `.github/workflows/scheduled-sync.yml`:

```yaml
name: Scheduled Model & Pricing Sync

on:
  schedule:
    # Run every 6 hours (at 00:00, 06:00, 12:00, 18:00 UTC)
    - cron: '0 */6 * * *'

    # OR choose a different schedule:
    # - cron: '0 2 * * *'        # Daily at 2 AM UTC
    # - cron: '0 2,14 * * *'     # Twice daily (2 AM and 2 PM UTC)
    # - cron: '0 */4 * * *'      # Every 4 hours
    # - cron: '0 8 * * 1-5'      # Weekdays at 8 AM UTC

  workflow_dispatch: # Allow manual trigger from GitHub UI

env:
  API_URL: ${{ secrets.API_URL }}
  ADMIN_KEY: ${{ secrets.ADMIN_KEY }}

jobs:
  sync-models:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Check API health
        run: |
          curl -f "$API_URL/health" || exit 1
          echo "✅ API is healthy"

      - name: Trigger model sync
        id: model_sync
        run: |
          echo "🔄 Starting model sync..."
          RESPONSE=$(curl -s -X POST "$API_URL/admin/model-sync/all")
          echo "$RESPONSE" | jq '.'

          # Check if successful
          SUCCESS=$(echo "$RESPONSE" | jq -r '.success')
          if [ "$SUCCESS" = "true" ]; then
            MODELS_SYNCED=$(echo "$RESPONSE" | jq -r '.details.total_models_synced // 0')
            echo "✅ Model sync completed: $MODELS_SYNCED models"
            echo "models_synced=$MODELS_SYNCED" >> $GITHUB_OUTPUT
          else
            echo "❌ Model sync failed"
            exit 1
          fi

      - name: Trigger pricing sync
        id: pricing_sync
        run: |
          echo "🔄 Starting pricing sync..."
          RESPONSE=$(curl -s -X POST "$API_URL/admin/pricing/sync/$ADMIN_KEY")
          echo "$RESPONSE" | jq '.'

          # Extract sync ID
          SYNC_ID=$(echo "$RESPONSE" | jq -r '.sync_id // empty')
          if [ -n "$SYNC_ID" ]; then
            echo "✅ Pricing sync queued: $SYNC_ID"
            echo "sync_id=$SYNC_ID" >> $GITHUB_OUTPUT
          else
            echo "⚠️  Could not extract sync ID"
          fi

      - name: Wait for pricing sync completion
        if: steps.pricing_sync.outputs.sync_id != ''
        run: |
          SYNC_ID="${{ steps.pricing_sync.outputs.sync_id }}"
          echo "⏳ Waiting for pricing sync $SYNC_ID to complete..."

          for i in {1..30}; do
            sleep 10
            STATUS=$(curl -s "$API_URL/admin/pricing/sync/$ADMIN_KEY/status/$SYNC_ID" | jq -r '.status')
            echo "   Status: $STATUS (check $i/30)"

            if [ "$STATUS" = "completed" ]; then
              echo "✅ Pricing sync completed"
              exit 0
            elif [ "$STATUS" = "failed" ]; then
              echo "❌ Pricing sync failed"
              exit 1
            fi
          done

          echo "⚠️  Pricing sync still running after 5 minutes"

      - name: Verify sync results
        run: |
          echo "📊 Checking sync results..."
          STATUS=$(curl -s "$API_URL/admin/model-sync/status")

          TOTAL_MODELS=$(echo "$STATUS" | jq -r '.models.stats.total_active // 0')
          echo "   Database models: $TOTAL_MODELS"

          if [ "$TOTAL_MODELS" -ge 17000 ]; then
            echo "✅ Database is fully synced ($TOTAL_MODELS models)"
          else
            echo "⚠️  Database has fewer models than expected: $TOTAL_MODELS"
          fi

      - name: Send notification on failure
        if: failure()
        run: |
          echo "❌ Scheduled sync failed!"
          echo "Check workflow logs: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"

          # Optional: Send to Slack, Discord, email, etc.
          # curl -X POST $SLACK_WEBHOOK_URL -d '{"text":"Sync failed!"}'

  # Optional: Health monitoring job
  monitor-sync-health:
    runs-on: ubuntu-latest
    needs: sync-models
    if: always()

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install requests psycopg2-binary

      - name: Run health monitoring
        env:
          API_URL: ${{ secrets.API_URL }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
        run: |
          python3 scripts/monitor_sync_health.py
```

#### Setup GitHub Secrets

1. Go to your repository on GitHub
2. Settings → Secrets and variables → Actions
3. Add secrets:
   - `API_URL`: Your API URL (e.g., `https://api.gatewayz.ai`)
   - `ADMIN_KEY`: Your admin API key
   - `SUPABASE_URL`: Your Supabase database URL

#### Test Workflow

```bash
# Commit and push
git add .github/workflows/scheduled-sync.yml
git commit -m "Add automated sync scheduling"
git push

# Manually trigger from GitHub:
# Go to Actions → Scheduled Model & Pricing Sync → Run workflow
```

---

## 🚂 Option 4: Railway Cron

### Railway Built-in Cron Jobs

Railway supports cron jobs natively!

Create `railway.toml`:

```toml
[[crons]]
schedule = "0 */6 * * *"  # Every 6 hours
command = "curl -X POST http://localhost:8000/admin/model-sync/all"

[[crons]]
schedule = "0 2 * * *"  # Daily at 2 AM UTC
command = "python scripts/cron_sync.py"
```

Or via Railway Dashboard:
1. Project → Settings → Cron Jobs
2. Add new cron job:
   - Schedule: `0 */6 * * *`
   - Command: `curl -X POST http://localhost:8000/admin/model-sync/all`

---

## ▲ Option 5: Vercel Cron

### Vercel Edge Functions Cron

Create `api/cron/sync.ts`:

```typescript
import type { NextApiRequest, NextApiResponse } from 'next';

export const config = {
  // Run every 6 hours
  schedule: '0 */6 * * *',
};

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  // Verify cron secret (for security)
  if (req.headers.authorization !== `Bearer ${process.env.CRON_SECRET}`) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  const apiUrl = process.env.API_URL || 'http://localhost:8000';

  try {
    // Trigger model sync
    const response = await fetch(`${apiUrl}/admin/model-sync/all`, {
      method: 'POST',
    });

    const data = await response.json();

    return res.status(200).json({
      success: true,
      timestamp: new Date().toISOString(),
      sync_result: data,
    });
  } catch (error) {
    console.error('Sync failed:', error);
    return res.status(500).json({
      success: false,
      error: error.message,
    });
  }
}
```

Configure in `vercel.json`:

```json
{
  "crons": [
    {
      "path": "/api/cron/sync",
      "schedule": "0 */6 * * *"
    }
  ]
}
```

---

## 🌐 Option 6: External Cron Services

### EasyCron, Cronitor, or Cron-Job.org

1. Sign up for free cron service
2. Create new cron job:
   - **URL**: `https://api.gatewayz.ai/admin/model-sync/all`
   - **Method**: POST
   - **Schedule**: `0 */6 * * *` (or your preference)
   - **Timeout**: 300 seconds
3. Save and enable

**Services**:
- [EasyCron](https://www.easycron.com/) - Free plan: 1 cron job
- [Cron-Job.org](https://cron-job.org/) - Free, unlimited jobs
- [Cronitor](https://cronitor.io/) - Free plan: 5 monitors

---

## 📋 Recommended Schedule Configurations

### High-Frequency (Active Development)
```bash
# Every 2 hours
PRICING_SYNC_INTERVAL_HOURS=2
# OR
0 */2 * * *  # Cron
```

### Standard (Production)
```bash
# Every 6 hours (default)
PRICING_SYNC_INTERVAL_HOURS=6
# OR
0 */6 * * *  # Cron: 00:00, 06:00, 12:00, 18:00 UTC
```

### Low-Frequency (Stable/Mature)
```bash
# Once daily at 2 AM UTC
PRICING_SYNC_INTERVAL_HOURS=24
# OR
0 2 * * *  # Cron
```

### Business Hours Only
```bash
# Every 2 hours during business hours (8 AM - 8 PM UTC)
0 8,10,12,14,16,18,20 * * *
```

---

## 🔔 Setup Monitoring & Alerts

### Email Alerts (GitHub Actions)

Add to workflow:

```yaml
- name: Send email on failure
  if: failure()
  uses: dawidd6/action-send-mail@v3
  with:
    server_address: smtp.gmail.com
    server_port: 465
    username: ${{ secrets.EMAIL_USERNAME }}
    password: ${{ secrets.EMAIL_PASSWORD }}
    subject: "⚠️ Gatewayz Sync Failed"
    body: "The scheduled sync failed. Check logs."
    to: admin@yourdomain.com
    from: alerts@gatewayz.ai
```

### Slack Notifications

```yaml
- name: Slack notification
  if: always()
  uses: rtCamp/action-slack-notify@v2
  env:
    SLACK_WEBHOOK: ${{ secrets.SLACK_WEBHOOK }}
    SLACK_MESSAGE: |
      Sync completed
      Models: ${{ steps.model_sync.outputs.models_synced }}
```

### Discord Webhook

```yaml
- name: Discord notification
  if: failure()
  run: |
    curl -X POST "${{ secrets.DISCORD_WEBHOOK }}" \
      -H "Content-Type: application/json" \
      -d '{"content":"🚨 Scheduled sync failed! Check logs."}'
```

---

## ✅ Verification Checklist

After setup:

- [ ] Scheduler is running (check logs)
- [ ] First sync completed successfully
- [ ] Database has ~18,000 models
- [ ] Cron schedule is correct (verify with `crontab -l` or workflow file)
- [ ] Logs are accessible
- [ ] Alerts configured (optional)
- [ ] Monitoring script runs successfully

---

## 📊 Comparison Matrix

| Method | Setup | Control | Reliability | Free | Best For |
|--------|-------|---------|-------------|------|----------|
| Built-in | ✅ Easy | ⚠️ Interval only | ⭐⭐⭐⭐⭐ | ✅ Yes | Simple, already running |
| Cron | ⚠️ Medium | ⭐⭐⭐⭐⭐ Exact time | ⭐⭐⭐⭐ | ✅ Yes | Server control, precise timing |
| GitHub Actions | ⚠️ Medium | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ✅ Yes | Cloud-based, notifications |
| Railway Cron | ✅ Easy | ⭐⭐⭐ | ⭐⭐⭐⭐ | ✅ Yes | Railway deployments |
| Vercel Cron | ⚠️ Medium | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⚠️ Pro only | Vercel deployments |
| External | ✅ Easy | ⭐⭐⭐ | ⭐⭐⭐ | ⚠️ Limited | No server access |

---

## 🎯 Recommended Setup

### For Most Users (Hybrid Approach)
1. **Keep built-in scheduler** for reliability (every 6 hours)
2. **Add GitHub Actions** for daily deep sync at specific time (2 AM)
3. **Setup monitoring** to verify both are working

This gives you:
- ✅ Continuous syncing (built-in, every 6 hours)
- ✅ Daily guaranteed sync (GitHub Actions, 2 AM)
- ✅ Redundancy (if one fails, other still works)
- ✅ Monitoring & alerts

---

## 🚨 Troubleshooting

### Cron not running
```bash
# Check cron service
sudo systemctl status cron

# Check cron logs
grep CRON /var/log/syslog

# Test script manually
./scripts/cron_sync.sh
```

### GitHub Actions not triggering
- Check workflow syntax: Use [Crontab Guru](https://crontab.guru/)
- Verify repository is active (has recent commits)
- Check Actions tab for errors

### Railway/Vercel cron issues
- Verify cron syntax in dashboard
- Check application logs
- Ensure command is correct

---

## 📝 Quick Setup Command

```bash
# Complete automated setup (combines all options)
./scripts/setup_automated_sync.sh
```

---

## 📚 Related Documentation

- `docs/KEEP_DB_FULLY_SYNCED.md` - Full sync guide
- `docs/MODEL_SYNC_GUIDE.md` - Sync system documentation
- `docs/QUICK_START_FULL_SYNC.md` - Quick start

---

**Last Updated**: 2026-01-27
**Recommended**: Use Built-in + GitHub Actions hybrid approach
