# Automated Sync - Quick Start

Get your database syncing automatically at specific times in 5 minutes.

---

## 🎯 Goal

Set up automatic syncing that runs at **specific times** (e.g., daily at 2 AM) instead of just every N hours.

---

## ⚡ Super Quick (Choose One)

### Option 1: Automated Wizard (Easiest)
```bash
./scripts/setup_automated_sync.sh
```
Asks questions and sets everything up for you.

### Option 2: One-Command Setup
```bash
# For Railway
git add railway.toml && git commit -m "Add automated sync" && git push
# Cron job runs every 6 hours automatically!

# For GitHub Actions
git add .github/workflows/scheduled-sync.yml
git commit -m "Add automated sync workflow"
git push
# Configure secrets: API_URL, ADMIN_KEY
```

### Option 3: Manual Cron (Most Control)
```bash
# Add to crontab
crontab -e

# Daily at 2 AM UTC
0 2 * * * /path/to/gatewayz-backend/scripts/cron_sync.sh
```

---

## 📅 Common Schedules

### Every 6 Hours
```bash
# Cron
0 */6 * * *  # 00:00, 06:00, 12:00, 18:00 UTC

# Built-in
PRICING_SYNC_INTERVAL_HOURS=6
```

### Daily at 2 AM UTC
```bash
# Cron
0 2 * * *

# Built-in (close approximation)
PRICING_SYNC_INTERVAL_HOURS=24
```

### Twice Daily (2 AM & 2 PM)
```bash
# Cron only
0 2,14 * * *
```

### Every 4 Hours
```bash
# Cron
0 */4 * * *

# Built-in
PRICING_SYNC_INTERVAL_HOURS=4
```

### Business Hours (8 AM - 8 PM, every 2 hours)
```bash
# Cron only
0 8,10,12,14,16,18,20 * * *
```

---

## 🎛️ What's Already Running

### Built-in Scheduler ✅

**Status**: Already active!
**Runs**: Every 6 hours by default
**Started**: Automatically on app startup
**Location**: `src/services/pricing_sync_scheduler.py`

To change frequency:
```bash
# In .env or environment
PRICING_SYNC_INTERVAL_HOURS=2  # Every 2 hours
```

**Limitations**: Can't specify exact times (e.g., "2 AM")

---

## 🚀 Platform-Specific Setup

### Railway (Recommended for Railway Users)

**1. Commit railway.toml** (already configured):
```bash
git add railway.toml
git commit -m "Enable Railway cron jobs"
git push
```

**2. Verify** in Railway dashboard:
- Project → Settings → Cron Jobs
- Should see: `0 */6 * * *` schedule

**Done!** Runs every 6 hours automatically.

---

### GitHub Actions (Best for All Platforms)

**1. Push workflow** (already created):
```bash
git add .github/workflows/scheduled-sync.yml
git commit -m "Add scheduled sync workflow"
git push
```

**2. Add secrets** on GitHub:
- Go to: Settings → Secrets → Actions
- Add:
  - `API_URL`: `https://api.gatewayz.ai`
  - `ADMIN_KEY`: Your admin key (optional)
  - `SUPABASE_URL`: Your database URL (optional)

**3. Test**:
- Go to: Actions → Scheduled Sync → Run workflow

**Done!** Runs every 6 hours in cloud.

---

### Cron Jobs (Local/Server)

**1. Make script executable**:
```bash
chmod +x scripts/cron_sync.sh
```

**2. Add to crontab**:
```bash
crontab -e
```

**3. Add schedule** (pick one):
```bash
# Every 6 hours
0 */6 * * * /full/path/to/gatewayz-backend/scripts/cron_sync.sh

# Daily at 2 AM UTC
0 2 * * * /full/path/to/gatewayz-backend/scripts/cron_sync.sh

# Twice daily
0 2,14 * * * /full/path/to/gatewayz-backend/scripts/cron_sync.sh
```

**4. Verify**:
```bash
crontab -l
```

**Done!** Runs at scheduled times.

---

## ✅ Verification

### Check Logs

**Built-in scheduler**:
```bash
# Application logs
tail -f logs/app.log | grep "pricing sync"
```

**Cron jobs**:
```bash
tail -f /var/log/gatewayz_cron_sync.log
```

**GitHub Actions**:
- GitHub → Actions → Workflow runs

**Railway**:
```bash
railway logs | grep "model-sync"
```

### Check Status
```bash
# API endpoint
curl https://api.gatewayz.ai/admin/model-sync/status | jq

# Health script
python3 scripts/monitor_sync_health.py

# Database count
psql $SUPABASE_URL -c "SELECT COUNT(*) FROM models WHERE is_active = true;"
```

---

## 🔔 Setup Alerts (Optional)

### Email Alerts (GitHub Actions)

Already configured in workflow! Just add email secrets:
```yaml
secrets:
  EMAIL_USERNAME: your-email@gmail.com
  EMAIL_PASSWORD: your-app-password
```

### Slack Alerts

Add webhook URL:
```yaml
secrets:
  SLACK_WEBHOOK: https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

---

## 🎯 Recommended Setup (Best Practice)

Use **multiple methods** for redundancy:

```bash
# 1. Keep built-in scheduler (every 6 hours)
PRICING_SYNC_ENABLED=true
PRICING_SYNC_INTERVAL_HOURS=6

# 2. Add GitHub Actions (daily at 2 AM)
# Already configured in .github/workflows/scheduled-sync.yml

# 3. (Optional) Add Railway cron (every 6 hours)
# Already configured in railway.toml
```

This gives you:
- ✅ Continuous syncing (built-in)
- ✅ Daily deep sync (GitHub Actions)
- ✅ Redundancy (multiple methods)

---

## 🚨 Troubleshooting

### Issue: Cron not running
```bash
# Check cron service
sudo systemctl status cron

# View cron logs
grep CRON /var/log/syslog

# Test manually
./scripts/cron_sync.sh
```

### Issue: GitHub Actions not triggering
- Check schedule syntax at [Crontab Guru](https://crontab.guru/)
- Verify repository is active
- Check Actions → All workflows

### Issue: Railway cron not visible
- Ensure railway.toml is committed
- Redeploy: `git push`
- Check Railway dashboard → Cron Jobs

---

## 📊 What You Get

### Before
```
Sync: Manual only
Schedule: Random (when you remember)
Status: ❌ Unreliable
```

### After
```
Sync: Automated
Schedule: Every 6 hours (or your custom schedule)
Redundancy: Multiple methods
Status: ✅ Always up to date
```

---

## 📝 Quick Commands

```bash
# Setup wizard
./scripts/setup_automated_sync.sh

# Manual sync now
./scripts/sync_all_providers_now.sh

# Check cron schedule
crontab -l

# View sync logs
tail -f /var/log/gatewayz_cron_sync.log

# Monitor health
python3 scripts/monitor_sync_health.py

# Verify setup
curl https://api.gatewayz.ai/admin/pricing/sync/$ADMIN_KEY/scheduler/status
```

---

## 🎉 That's It!

Your database now syncs automatically at your chosen schedule.

- **Next**: Monitor logs to ensure it's working
- **Docs**: Read `docs/AUTOMATED_SYNC_SCHEDULING.md` for details
- **Help**: Run `./scripts/setup_automated_sync.sh` for guided setup

---

**Last Updated**: 2026-01-27
**Setup Time**: 5 minutes
**Methods Available**: 5 (Built-in, Cron, GitHub Actions, Railway, Vercel)
