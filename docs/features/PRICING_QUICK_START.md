# Pricing Audit System - Quick Start Guide

**Last Updated**: 2025-11-23

## 🚀 Quick Start (5 minutes)

### 1. Check Dashboard
```bash
# Get full pricing dashboard
curl http://localhost:8000/pricing/audit/dashboard?days=30
```

### 2. Find Pricing Anomalies
```bash
# Show all critical pricing anomalies
curl "http://localhost:8000/pricing/audit/anomalies?severity=critical&threshold=50"
```

### 3. Audit Provider APIs
```bash
# Audit all providers and compare against our pricing
curl http://localhost:8000/pricing/audit/providers
```

### 4. Test Price Sync
```bash
# Dry-run: See what would change without making changes
curl -X POST http://localhost:8000/pricing/sync/dry-run
```

---

## 📊 Common Tasks

### View Model Pricing History
```bash
curl "http://localhost:8000/pricing/audit/model/gpt-4?limit=50"
```

### Compare Same Model Across Gateways
```bash
curl "http://localhost:8000/pricing/audit/comparisons/Qwen2.5-72B-Instruct"
```

### Calculate Cost Savings
```bash
# For 1B monthly tokens, which gateway is cheapest?
curl "http://localhost:8000/pricing/audit/cost-impact/gpt-4?monthly_tokens=1000000000"
```

### Get Pricing for Specific Gateway
```bash
curl "http://localhost:8000/pricing/audit/gateway/openrouter?limit=100"
```

### Record Pricing Snapshot
```bash
curl -X POST http://localhost:8000/pricing/audit/snapshot
```

### Export Audit Data
```bash
# Export as CSV
curl "http://localhost:8000/pricing/audit/export?format=csv"

# Export as JSON
curl "http://localhost:8000/pricing/audit/export?format=json"
```

---

## 🔄 Sync Operations

### Dry-Run (Safe - No Changes)
```bash
# Test sync for all providers
curl -X POST http://localhost:8000/pricing/sync/dry-run

# Test sync for specific providers
curl -X POST "http://localhost:8000/pricing/sync/dry-run?providers=openrouter,featherless"

# Test sync for one provider
curl -X POST "http://localhost:8000/pricing/sync/run/openrouter?dry_run=true"
```

### Execute Sync (Makes Changes)
```bash
# Sync all providers
curl -X POST http://localhost:8000/pricing/sync/run

# Sync in background (returns immediately)
curl -X POST "http://localhost:8000/pricing/sync/run?background=true"

# Sync specific provider
curl -X POST http://localhost:8000/pricing/sync/run/openrouter
```

### View Sync History
```bash
curl "http://localhost:8000/pricing/sync/history?limit=50"
```

### Get Sync Status
```bash
curl http://localhost:8000/pricing/sync/status
```

---

## 🔍 Key Findings (As of 2025-11-23)

### Critical Pricing Anomalies
| Model | Cheapest | Most Expensive | Variance |
|-------|----------|----------------|----------|
| Qwen2.5-72B-Instruct | Alibaba ($0.016) | DeepInfra ($0.35) | **21.8x** 🔴 |
| Mistral-7B | Featherless ($0.05) | Clarifai ($0.14) | **2.8x** 🟠 |
| Llama-3.1-70B | Featherless ($0.35) | DeepInfra ($0.40) | **14%** 🟡 |

### Price Tiers
- **Ultra-Budget**: Alibaba Qwen-Flash at $0.001/1M tokens
- **Budget**: Featherless models at $0.05/1M tokens
- **Mid-Range**: DeepInfra/Portkey models at $0.35-$2.70/1M tokens
- **Premium**: Claude/GPT-4 at $3-$75/1M tokens

### Annual Savings Opportunity
For 1B monthly tokens (12B annually):
- **Current**: Using Clarifai GPT-4 = **$1,080,000/year**
- **Optimized**: Using Alibaba Qwen-Max = **$576/year**
- **Savings**: **$1,079,424/year** 🟢

---

## 📋 Response Examples

### Anomalies Response
```json
{
  "threshold_pct": 50,
  "severity_filter": "critical",
  "anomaly_count": 1,
  "anomalies": [
    {
      "model_id": "Qwen2.5-72B-Instruct",
      "gateway_a": "deepinfra",
      "gateway_b": "alibaba-cloud",
      "prompt_variance_pct": 2080.0,
      "completion_variance_pct": 2400.0,
      "severity": "critical"
    }
  ]
}
```

### Dashboard Response
```json
{
  "dashboard_version": "1.0",
  "period_days": 30,
  "report_summary": {
    "total_records": 156,
    "unique_gateways": 7,
    "unique_models": 35,
    "total_anomalies": 8,
    "critical_discrepancies": 0
  },
  "top_anomalies": [
    {
      "model_id": "Qwen2.5-72B-Instruct",
      "gateways": "deepinfra vs alibaba-cloud",
      "variance_pct": 2080.0,
      "severity": "critical"
    }
  ],
  "recommendations": [
    "🔴 CRITICAL: 1 critical anomalies found (>500% variance)...",
    "💰 Cumulative pricing variance: 2080.0%..."
  ]
}
```

### Dry-Run Sync Response
```json
{
  "type": "dry_run_sync",
  "status": "complete",
  "changes_would_be_made": true,
  "summary": {
    "providers": 4,
    "total_updates": 3,
    "total_new": 1
  },
  "results": {
    "openrouter": {
      "provider": "openrouter",
      "status": "success",
      "models_updated": 2,
      "models_skipped": 0,
      "price_changes": [
        {
          "model_id": "gpt-4-turbo",
          "type": "updated",
          "old_pricing": {"prompt": "10.00", "completion": "30.00"},
          "new_pricing": {"prompt": "10.50", "completion": "31.50"}
        }
      ]
    }
  }
}
```

---

## 🛡️ Safety Features

- **Dry-Run First**: Always use `?dry_run=true` before syncing
- **Automatic Backups**: Every sync creates timestamped backup
- **30-Day Retention**: All backups kept for 30 days
- **Change Validation**: Rejects changes > 50% deviation
- **Manual Override**: Add `"_manual_override": true` to preserve prices
- **Rollback**: Automatic rollback on errors

---

## ⚠️ Common Issues & Solutions

### "Provider does not expose pricing via public API"
- DeepInfra, Alibaba Cloud don't have public APIs
- Use dry-run to see what's available
- Manual verification required for these providers

### "Sync rejected due to MAX_DEVIATION_PCT"
- Price change exceeds 50% threshold
- Verify in provider dashboard that price actually changed
- If legitimate, can increase threshold temporarily

### "No pricing data from API"
- Check provider API status/uptime
- Verify network connectivity
- Run dry-run to isolate issue

---

## 📂 File Locations

```
/root/repo/src/data/
├── manual_pricing.json              # Main pricing file
└── pricing_history/
    ├── pricing_history.jsonl        # All historical records
    ├── pricing_snapshots/           # Timestamped snapshots
    ├── pricing_backups/             # Auto-backups (30-day retention)
    ├── pricing_sync.log             # Sync operation log
    ├── pricing_anomalies.json       # Detected anomalies
    └── pricing_alerts.jsonl         # Price change alerts
```

---

## 🔧 Configuration

**File**: `src/services/pricing_sync_service.py`

```python
class PricingSyncConfig:
    # Providers to auto-sync
    AUTO_SYNC_PROVIDERS = ["openrouter", "featherless", "nearai", "alibaba-cloud"]

    # Thresholds
    MAX_DEVIATION_PCT = 50.0           # Reject changes > 50%
    MIN_CHANGE_THRESHOLD = 0.0001      # Only sync if changed
    BACKUP_RETENTION_DAYS = 30         # Keep backups for 30 days
    PRESERVE_MANUAL_OVERRIDES = True   # Don't overwrite manual prices
```

---

## 🎯 Usage by Role

### For Pricing Analysts
1. Run daily: `GET /pricing/audit/dashboard`
2. Check: `GET /pricing/audit/anomalies?severity=critical`
3. Investigate: `GET /pricing/audit/comparisons/{model_id}`
4. Report: Use exported data from `GET /pricing/audit/export`

### For DevOps/SRE
1. Monitor: `GET /pricing/sync/status`
2. Check logs: `tail -f /root/repo/src/data/pricing_history/pricing_sync.log`
3. Verify backups: `ls /root/repo/src/data/pricing_backups/`
4. Alert on: Sync failures or critical anomalies

### For Product/Business
1. Calculate savings: `GET /pricing/audit/cost-impact/{model_id}`
2. Identify opportunities: Review anomalies report
3. Monitor trends: Dashboard metrics over time
4. Quarterly review: Export data for analysis

---

## 📞 Quick Reference

| Task | Endpoint | Method |
|------|----------|--------|
| Dashboard | `/pricing/audit/dashboard` | GET |
| Anomalies | `/pricing/audit/anomalies` | GET |
| Model history | `/pricing/audit/model/{id}` | GET |
| Gateway history | `/pricing/audit/gateway/{gw}` | GET |
| Comparisons | `/pricing/audit/comparisons/{id}` | GET |
| Cost impact | `/pricing/audit/cost-impact/{id}` | GET |
| Providers audit | `/pricing/audit/providers` | GET |
| Dry-run sync | `/pricing/sync/dry-run` | POST |
| Execute sync | `/pricing/sync/run` | POST |
| Sync status | `/pricing/sync/status` | GET |
| Sync history | `/pricing/sync/history` | GET |

---

## 🚀 Getting Started in 5 Minutes

```bash
# 1. Check current pricing dashboard
curl http://localhost:8000/pricing/audit/dashboard

# 2. Find critical issues
curl "http://localhost:8000/pricing/audit/anomalies?severity=critical"

# 3. Audit providers
curl http://localhost:8000/pricing/audit/providers

# 4. Run dry-run sync (see what would change)
curl -X POST http://localhost:8000/pricing/sync/dry-run

# 5. Review sync history
curl http://localhost:8000/pricing/sync/history
```

That's it! You now have full visibility into pricing.

---

**For detailed documentation**: See `docs/PRICING_AUDIT_SYSTEM.md`
**For detailed analysis**: See `docs/PRICING_AUDIT_DETAILED_COMPARISON.md`
**For implementation details**: See `docs/PRICING_IMPLEMENTATION_SUMMARY.md`
