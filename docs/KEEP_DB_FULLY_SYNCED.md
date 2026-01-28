# Keep Database Fully Synced (18k+ Models)

Complete guide to maintain a fully synced database with all provider models.

---

## 🎯 Goal

Ensure your database always has **all 18,000+ models** matching the live API, not just 11,000.

---

## 🚀 Quick Start (3 Steps)

### Step 1: Initial Full Sync (One-Time)

```bash
# Sync ALL providers to database immediately
curl -X POST https://api.gatewayz.ai/admin/model-sync/all

# This will take 2-5 minutes and sync all 30 providers
# Expected: 18,000+ models in database
```

### Step 2: Configure Auto-Sync for All Providers

Update your environment variables:

```bash
# Option A: In .env file (local development)
PRICING_SYNC_ENABLED=true
PRICING_SYNC_INTERVAL_HOURS=6
PRICING_SYNC_PROVIDERS=openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface

# Option B: Railway/Vercel Dashboard
# Add this as environment variable:
# Name: PRICING_SYNC_PROVIDERS
# Value: openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface
```

### Step 3: Restart and Verify

```bash
# Restart your application
railway restart  # Railway
# or
vercel redeploy  # Vercel
# or
systemctl restart gatewayz  # Docker/VM

# Verify count after restart
curl "https://api.gatewayz.ai/admin/model-sync/status" | jq '.models.stats.total_active'
# Expected: ~18,000
```

---

## 📋 Detailed Configuration

### Environment Variables (Complete List)

```bash
# ============================================
# Model & Pricing Sync Configuration
# ============================================

# Enable automatic sync
PRICING_SYNC_ENABLED=true

# Sync frequency (6 hours recommended, 2-4 for high-frequency updates)
PRICING_SYNC_INTERVAL_HOURS=6

# ALL PROVIDERS (30 total) - Copy this exactly:
PRICING_SYNC_PROVIDERS=openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface

# Database connection (should already be configured)
SUPABASE_URL=your-supabase-url
SUPABASE_KEY=your-service-role-key

# Provider API keys (must have these for providers to sync)
OPENROUTER_API_KEY=your-key
FEATHERLESS_API_KEY=your-key
DEEPINFRA_API_KEY=your-key
GROQ_API_KEY=your-key
FIREWORKS_API_KEY=your-key
TOGETHER_API_KEY=your-key
CEREBRAS_API_KEY=your-key
XAI_API_KEY=your-key
# ... (other provider keys)
```

---

## 🔧 Implementation Steps

### Option 1: Using Railway

```bash
# 1. Set environment variable via CLI
railway variables set PRICING_SYNC_PROVIDERS="openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface"

# 2. Redeploy
railway up

# 3. Trigger initial full sync
railway run curl -X POST http://localhost:8000/admin/model-sync/all
```

### Option 2: Using Vercel

```bash
# 1. Update vercel.json
{
  "env": {
    "PRICING_SYNC_ENABLED": "true",
    "PRICING_SYNC_INTERVAL_HOURS": "6",
    "PRICING_SYNC_PROVIDERS": "openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface"
  }
}

# 2. Redeploy
vercel --prod

# 3. Trigger initial sync
curl -X POST https://your-domain.vercel.app/admin/model-sync/all
```

### Option 3: Using Docker

```dockerfile
# In docker-compose.yml or Dockerfile ENV
environment:
  - PRICING_SYNC_ENABLED=true
  - PRICING_SYNC_INTERVAL_HOURS=6
  - PRICING_SYNC_PROVIDERS=openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface
```

```bash
# Restart container
docker-compose restart

# Trigger initial sync
docker exec gatewayz-api curl -X POST http://localhost:8000/admin/model-sync/all
```

---

## 🔍 Verification & Monitoring

### Create Verification Script

Save as `scripts/verify_full_sync.sh`:

```bash
#!/bin/bash
set -e

echo "🔍 Verifying Database Sync Status..."
echo ""

# Configuration
API_URL="${API_URL:-https://api.gatewayz.ai}"
DB_URL="${SUPABASE_URL}"

# 1. Check API model count
echo "📊 Checking API model count..."
API_COUNT=$(curl -s "${API_URL}/models?gateway=all&limit=30000" | jq '.data | length')
echo "   API Models: $API_COUNT"

# 2. Check database model count
echo ""
echo "📊 Checking database model count..."
DB_COUNT=$(psql "$DB_URL" -t -c "SELECT COUNT(*) FROM models WHERE is_active = true;" | xargs)
echo "   Database Models: $DB_COUNT"

# 3. Calculate difference
DIFF=$((API_COUNT - DB_COUNT))
PERCENT=$(echo "scale=2; ($DB_COUNT / $API_COUNT) * 100" | bc)

echo ""
echo "📈 Sync Status:"
echo "   Difference: $DIFF models"
echo "   Sync %: ${PERCENT}%"

# 4. Check by provider
echo ""
echo "📊 Models by Provider (Database):"
psql "$DB_URL" -c "
  SELECT
    p.slug as provider,
    COUNT(m.id) as model_count
  FROM models m
  JOIN providers p ON m.provider_id = p.id
  WHERE m.is_active = true
  GROUP BY p.slug
  ORDER BY model_count DESC;
"

# 5. Check last sync
echo ""
echo "🕒 Last Sync Jobs:"
psql "$DB_URL" -c "
  SELECT
    provider_slug,
    models_updated,
    sync_started_at,
    status
  FROM pricing_sync_log
  ORDER BY sync_started_at DESC
  LIMIT 10;
"

# 6. Status summary
echo ""
if [ "$DIFF" -lt 100 ]; then
  echo "✅ Database is fully synced! (within 100 models)"
elif [ "$DIFF" -lt 1000 ]; then
  echo "⚠️  Database is mostly synced (within 1,000 models)"
else
  echo "❌ Database needs full sync ($DIFF models missing)"
  echo ""
  echo "Run: curl -X POST ${API_URL}/admin/model-sync/all"
fi
```

```bash
# Make executable
chmod +x scripts/verify_full_sync.sh

# Run verification
./scripts/verify_full_sync.sh
```

### Create Monitoring Script

Save as `scripts/monitor_sync_health.py`:

```python
#!/usr/bin/env python3
"""Monitor database sync health and send alerts if out of sync"""

import os
import sys
import requests
from datetime import datetime, timezone
import psycopg2

API_URL = os.getenv("API_URL", "https://api.gatewayz.ai")
SUPABASE_URL = os.getenv("SUPABASE_URL")
ALERT_THRESHOLD = 1000  # Alert if difference > 1000 models

def get_api_count():
    """Get model count from API"""
    try:
        response = requests.get(f"{API_URL}/models?gateway=all&limit=30000", timeout=30)
        response.raise_for_status()
        data = response.json()
        return len(data.get("data", []))
    except Exception as e:
        print(f"❌ Error fetching API count: {e}")
        return None

def get_db_count():
    """Get model count from database"""
    try:
        conn = psycopg2.connect(SUPABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM models WHERE is_active = true")
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return count
    except Exception as e:
        print(f"❌ Error fetching DB count: {e}")
        return None

def check_last_sync():
    """Check when last sync occurred"""
    try:
        conn = psycopg2.connect(SUPABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            SELECT sync_started_at, status
            FROM pricing_sync_log
            ORDER BY sync_started_at DESC
            LIMIT 1
        """)
        result = cur.fetchone()
        cur.close()
        conn.close()
        return result
    except Exception as e:
        print(f"❌ Error checking last sync: {e}")
        return None

def main():
    print(f"🔍 Database Sync Health Monitor")
    print(f"   Time: {datetime.now(timezone.utc).isoformat()}")
    print("")

    # Get counts
    api_count = get_api_count()
    db_count = get_db_count()

    if api_count is None or db_count is None:
        print("❌ Failed to fetch counts")
        sys.exit(1)

    # Calculate difference
    diff = api_count - db_count
    percent = (db_count / api_count) * 100 if api_count > 0 else 0

    print(f"📊 Model Counts:")
    print(f"   API:      {api_count:,} models")
    print(f"   Database: {db_count:,} models")
    print(f"   Diff:     {diff:,} models")
    print(f"   Sync:     {percent:.1f}%")
    print("")

    # Check last sync
    last_sync = check_last_sync()
    if last_sync:
        sync_time, sync_status = last_sync
        print(f"🕒 Last Sync:")
        print(f"   Time:   {sync_time}")
        print(f"   Status: {sync_status}")
        print("")

    # Determine health status
    if diff <= 100:
        print("✅ STATUS: HEALTHY - Database is fully synced")
        sys.exit(0)
    elif diff <= ALERT_THRESHOLD:
        print("⚠️  STATUS: WARNING - Database is mostly synced")
        print(f"   Missing {diff} models (within threshold)")
        sys.exit(0)
    else:
        print("❌ STATUS: OUT OF SYNC - Database needs full sync")
        print(f"   Missing {diff} models (exceeds threshold of {ALERT_THRESHOLD})")
        print("")
        print("🔧 Recommended Action:")
        print(f"   curl -X POST {API_URL}/admin/model-sync/all")
        sys.exit(1)

if __name__ == "__main__":
    main()
```

```bash
# Make executable
chmod +x scripts/monitor_sync_health.py

# Run monitoring
python3 scripts/monitor_sync_health.py
```

---

## ⏰ Automated Monitoring (Production)

### Option 1: Cron Job

```bash
# Add to crontab (check every hour)
0 * * * * cd /path/to/gatewayz-backend && ./scripts/monitor_sync_health.py >> /var/log/sync_monitor.log 2>&1
```

### Option 2: GitHub Actions

Create `.github/workflows/monitor-db-sync.yml`:

```yaml
name: Monitor Database Sync

on:
  schedule:
    # Run every 6 hours
    - cron: '0 */6 * * *'
  workflow_dispatch: # Allow manual trigger

jobs:
  check-sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Check Database Sync Status
        env:
          API_URL: ${{ secrets.API_URL }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
        run: |
          python3 scripts/monitor_sync_health.py

      - name: Trigger Full Sync if Needed
        if: failure()
        run: |
          curl -X POST ${{ secrets.API_URL }}/admin/model-sync/all
```

### Option 3: Railway Cron (Built-in)

Create `railway.json`:

```json
{
  "cron": [
    {
      "schedule": "0 */6 * * *",
      "command": "python scripts/monitor_sync_health.py && curl -X POST http://localhost:8000/admin/model-sync/all"
    }
  ]
}
```

---

## 🔔 Alerting Setup

### Slack Webhook Alert

Add to `scripts/monitor_sync_health.py`:

```python
import requests

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

def send_slack_alert(diff, api_count, db_count):
    """Send alert to Slack if out of sync"""
    if not SLACK_WEBHOOK_URL:
        return

    message = {
        "text": "🚨 Database Sync Alert",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Database Out of Sync*\n"
                            f"• API Models: {api_count:,}\n"
                            f"• DB Models: {db_count:,}\n"
                            f"• Missing: {diff:,} models\n"
                            f"\n*Action Required:* Run full sync"
                }
            }
        ]
    }

    try:
        requests.post(SLACK_WEBHOOK_URL, json=message)
    except Exception as e:
        print(f"Failed to send Slack alert: {e}")

# Add to main():
if diff > ALERT_THRESHOLD:
    send_slack_alert(diff, api_count, db_count)
```

### Email Alert (via Resend)

```python
from resend import Resend

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "admin@yourdomain.com")

def send_email_alert(diff, api_count, db_count):
    """Send email alert if out of sync"""
    if not RESEND_API_KEY:
        return

    resend = Resend(RESEND_API_KEY)

    try:
        resend.emails.send({
            "from": "alerts@gatewayz.ai",
            "to": ALERT_EMAIL,
            "subject": "🚨 Database Sync Alert - Action Required",
            "html": f"""
            <h2>Database Out of Sync</h2>
            <p>Your Gatewayz database is out of sync with the live API.</p>
            <ul>
                <li><strong>API Models:</strong> {api_count:,}</li>
                <li><strong>Database Models:</strong> {db_count:,}</li>
                <li><strong>Missing:</strong> {diff:,} models</li>
            </ul>
            <p><strong>Action Required:</strong></p>
            <pre>curl -X POST {API_URL}/admin/model-sync/all</pre>
            """
        })
    except Exception as e:
        print(f"Failed to send email alert: {e}")
```

---

## 📊 Dashboard Query (Grafana/Metabase)

### PostgreSQL Query for Dashboard

```sql
-- Sync Status Dashboard Query
WITH api_estimate AS (
  -- Estimate API count based on recent growth
  SELECT 18000 AS estimated_api_count
),
db_stats AS (
  SELECT
    COUNT(*) FILTER (WHERE is_active = true) as active_models,
    COUNT(*) as total_models,
    COUNT(DISTINCT provider_id) as providers_count
  FROM models
),
last_syncs AS (
  SELECT
    provider_slug,
    models_updated,
    sync_started_at,
    status,
    ROW_NUMBER() OVER (PARTITION BY provider_slug ORDER BY sync_started_at DESC) as rn
  FROM pricing_sync_log
)
SELECT
  d.active_models,
  d.total_models,
  d.providers_count,
  a.estimated_api_count,
  (a.estimated_api_count - d.active_models) as models_missing,
  ROUND((d.active_models::numeric / a.estimated_api_count::numeric) * 100, 2) as sync_percentage,
  CASE
    WHEN (a.estimated_api_count - d.active_models) <= 100 THEN 'Healthy'
    WHEN (a.estimated_api_count - d.active_models) <= 1000 THEN 'Warning'
    ELSE 'Out of Sync'
  END as sync_status,
  (SELECT MAX(sync_started_at) FROM last_syncs WHERE status = 'success') as last_successful_sync
FROM db_stats d, api_estimate a;
```

---

## 🚨 Troubleshooting

### Issue: Sync Still Shows 11k After Configuration

**Check**:
```bash
# 1. Verify environment variable is set
echo $PRICING_SYNC_PROVIDERS

# 2. Check app logs for errors
tail -f logs/app.log | grep "model sync"

# 3. Manually trigger sync
curl -X POST https://api.gatewayz.ai/admin/model-sync/all
```

### Issue: Some Providers Not Syncing

**Check API keys**:
```bash
# Verify all provider API keys are configured
env | grep "_API_KEY"

# Test specific provider
curl -X POST https://api.gatewayz.ai/admin/model-sync/provider/groq
```

### Issue: Sync Timeouts

**Solution**: Increase sync timeout and sync providers individually:

```bash
# Sync in batches
curl -X POST "/admin/model-sync/all?providers=openrouter&providers=featherless"
# Wait 2 minutes
curl -X POST "/admin/model-sync/all?providers=deepinfra&providers=groq"
# Continue for all providers...
```

### Issue: Database Connection Pool Exhausted

**Solution**: Increase connection pool size:

```bash
# In .env
DB_POOL_SIZE=20  # Default: 10
DB_MAX_OVERFLOW=30  # Default: 20
```

---

## ✅ Verification Checklist

After configuration, verify:

- [ ] Environment variable `PRICING_SYNC_PROVIDERS` includes all 30 providers
- [ ] Application restarted with new configuration
- [ ] Initial full sync completed successfully
- [ ] Database has ~18,000 models (`SELECT COUNT(*) FROM models`)
- [ ] All providers have models in database (check `models` table by `provider_id`)
- [ ] Automatic sync scheduler is running (check logs)
- [ ] Monitoring script runs successfully
- [ ] Alerting configured (optional but recommended)

---

## 📝 Maintenance Schedule

### Daily
- ✅ Monitor sync health (automated via cron/GitHub Actions)
- ✅ Check sync logs for errors

### Weekly
- ✅ Review sync statistics
- ✅ Verify no providers are consistently failing
- ✅ Check for new providers added to GATEWAY_REGISTRY

### Monthly
- ✅ Review and update provider list if needed
- ✅ Optimize sync frequency based on usage patterns
- ✅ Clean up old sync logs (optional)

---

## 🎯 Expected Results

After full configuration:

### Before
```
Database: 11,000 models
API:      18,000 models
Sync:     61%
Status:   ⚠️  Partial sync
```

### After
```
Database: 18,000 models
API:      18,000 models
Sync:     100%
Status:   ✅ Fully synced
```

---

## 🔗 Related Files

**Configuration**:
- `src/config/config.py` - Environment variables
- `src/services/startup.py:303-320` - Auto-sync startup

**Sync Services**:
- `src/services/model_catalog_sync.py` - Model sync logic
- `src/services/pricing_sync_scheduler.py` - Scheduler
- `src/routes/model_sync.py` - API endpoints

**Database**:
- `src/db/models_catalog_db.py` - Database operations
- `supabase/migrations/` - Schema definitions

---

## 📞 Quick Commands

```bash
# Initial full sync
curl -X POST https://api.gatewayz.ai/admin/model-sync/all

# Check status
curl https://api.gatewayz.ai/admin/model-sync/status | jq

# Verify database count
psql $SUPABASE_URL -c "SELECT COUNT(*) FROM models WHERE is_active = true;"

# Check last sync
psql $SUPABASE_URL -c "SELECT * FROM pricing_sync_log ORDER BY sync_started_at DESC LIMIT 5;"

# Monitor health
./scripts/monitor_sync_health.py
```

---

**Last Updated**: 2026-01-27
**Maintainer**: Gatewayz Team
