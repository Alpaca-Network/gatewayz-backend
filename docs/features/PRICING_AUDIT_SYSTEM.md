# Pricing Audit System Documentation

**Version**: 1.0
**Created**: 2025-11-23
**Status**: Production Ready

## Overview

The Pricing Audit System provides comprehensive monitoring, auditing, and synchronization of pricing data across all 7+ provider gateways. It enables real-time detection of pricing discrepancies, automatic price updates, and detailed cost-impact analysis.

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    API Routes                                │
├──────────────────┬──────────────────┬──────────────────┐    │
│ Audit Routes     │ Sync Routes      │ Provider Audit   │    │
│ /pricing/audit   │ /pricing/sync    │ Endpoints        │    │
└──────────────────┴──────────────────┴──────────────────┘    │
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
┌───────▼────────┐ ┌────────▼─────────┐ ┌──────▼──────────┐
│ Audit Service  │ │ Sync Service     │ │ Provider Auditor│
│ - Track        │ │ - API Sync       │ │ - Fetch Prices  │
│   history      │ │ - Backups        │ │ - Compare       │
│ - Compare      │ │ - Rollback       │ │ - Report        │
│   gateways     │ │ - Merging        │ │                 │
│ - Generate     │ │ - Validation     │ │                 │
│   reports      │ │                  │ │                 │
└────────┬───────┘ └────────┬─────────┘ └────────┬────────┘
         │                   │                    │
         └───────────────────┼────────────────────┘
                             │
                    ┌────────▼────────┐
                    │ Pricing Data    │
                    ├─────────────────┤
                    │ manual_pricing  │
                    │    .json        │
                    ├─────────────────┤
                    │ History Files   │
                    │ Snapshots       │
                    │ Backups         │
                    └─────────────────┘
```

## Components

### 1. Pricing Audit Service (`src/services/pricing_audit_service.py`)

Tracks historical pricing data and detects anomalies.

**Key Classes**:
- `PricingRecord`: Single pricing snapshot
- `PricingComparison`: Cross-gateway comparison
- `PricingChangeAlert`: Price change notifications
- `PricingAuditService`: Main service

**Key Methods**:

```python
# Record pricing data
service.record_pricing_snapshot(pricing_data)
service.record_all_pricing(pricing_data)
service.log_pricing_record(record)

# Retrieve history
service.get_pricing_history(gateway=None, model_id=None)
service.get_pricing_history(gateway="openrouter", model_id="gpt-4")

# Detect anomalies
anomalies = service.find_pricing_anomalies(variance_threshold_pct=50)
changes = service.detect_price_changes(gateway, model_id, threshold_pct=5)

# Compare across gateways
comparisons = service.compare_gateway_pricing(model_id="gpt-4")

# Cost impact analysis
analysis = service.get_cost_impact_analysis(model_id, monthly_tokens=1_000_000_000)

# Generate reports
report = service.generate_audit_report(days=30)
service.save_audit_report(report, filename="audit_2025_11_23.json")

# Export data
csv_data = service.export_audit_data(format="csv")
```

**Data Storage**:
```
/root/repo/src/data/pricing_history/
├── pricing_history.jsonl          # All pricing records
├── pricing_anomalies.json         # Detected anomalies
├── pricing_alerts.jsonl           # Price change alerts
├── pricing_comparisons.json       # Cross-gateway comparisons
└── snapshots/
    ├── pricing_snapshot_20251123_120000.json
    ├── pricing_snapshot_20251123_110000.json
    └── ...
```

### 2. Provider Pricing Auditor (`src/services/pricing_provider_auditor.py`)

Fetches pricing from provider APIs and compares against stored data.

**Key Methods**:

```python
auditor = PricingProviderAuditor()

# Audit specific providers
deepinfra_data = await auditor.audit_deepinfra()
featherless_data = await auditor.audit_featherless()
near_data = await auditor.audit_nearai()
alibaba_data = await auditor.audit_alibaba_cloud()
openrouter_data = await auditor.audit_openrouter()

# Audit all providers
results = await auditor.audit_all_providers()

# Compare with manual pricing
discrepancies = auditor.compare_with_manual_pricing(api_data, manual_pricing)

# Generate audit report
report = auditor.generate_audit_report(audit_results, manual_pricing)
```

**Supported Providers**:
- OpenRouter (API endpoint: https://openrouter.ai/api/v1/models)
- Featherless (API endpoint: https://api.featherless.ai/v1/models)
- Near AI (API endpoint: https://cloud-api.near.ai/v1/model/list)
- Alibaba Cloud (Manual verification recommended - no public API)
- DeepInfra (Manual verification required - no public API)

### 3. Pricing Sync Service (`src/services/pricing_sync_service.py`)

Automatically updates pricing from provider APIs.

**Key Methods**:

```python
service = PricingSyncService()

# Sync specific provider
result = await service.sync_provider_pricing("openrouter", dry_run=True)

# Sync all providers
summary = await service.sync_all_providers(dry_run=False)

# Get history
history = service.get_sync_history(limit=100)

# Cleanup old backups
service.cleanup_old_backups(retention_days=30)
```

**Features**:
- Automatic backup before updates
- Change detection and validation
- Merge conflict resolution
- Manual override preservation
- Rollback capability
- Comprehensive logging

**Configuration**:

```python
class PricingSyncConfig:
    AUTO_SYNC_PROVIDERS = [
        "openrouter",
        "featherless",
        "nearai",
        "alibaba-cloud",
    ]
    MAX_DEVIATION_PCT = 50.0  # Reject changes > 50%
    MIN_CHANGE_THRESHOLD = 0.0001
    BACKUP_RETENTION_DAYS = 30
    PRESERVE_MANUAL_OVERRIDES = True
```

---

## API Endpoints

### Audit Dashboard Endpoints

#### GET `/pricing/audit/report`
Get comprehensive audit report for specified period.

```bash
curl "http://localhost:8000/pricing/audit/report?days=30"
```

**Response**:
```json
{
  "report_type": "pricing_audit",
  "generated_at": "2025-11-23T12:00:00",
  "period_days": 30,
  "summary": {
    "total_records": 156,
    "unique_gateways": 7,
    "unique_models": 35,
    "total_anomalies": 8,
    "critical_discrepancies": 0
  },
  "gateway_stats": {
    "alibaba-cloud": {"model_count": 11, "changes_detected": 2},
    "featherless": {"model_count": 4, "changes_detected": 0}
  },
  "worst_anomalies": [
    {
      "model_id": "Qwen2.5-72B-Instruct",
      "gateway_a": "alibaba-cloud",
      "gateway_b": "deepinfra",
      "prompt_variance_pct": 2080.0,
      "completion_variance_pct": 2400.0,
      "variance_severity": "critical"
    }
  ],
  "recommendations": [
    "🔴 CRITICAL: 1 critical anomalies found (>500% variance). Review smart routing immediately.",
    "💰 Cumulative pricing variance: 2080.0%. May impact customer costs significantly."
  ]
}
```

#### GET `/pricing/audit/anomalies`
Get detected pricing anomalies.

```bash
curl "http://localhost:8000/pricing/audit/anomalies?threshold=50&severity=critical"
```

#### GET `/pricing/audit/model/{model_id}`
Get pricing history for a specific model.

```bash
curl "http://localhost:8000/pricing/audit/model/gpt-4?limit=50"
```

#### GET `/pricing/audit/gateway/{gateway}`
Get pricing history for a gateway.

```bash
curl "http://localhost:8000/pricing/audit/gateway/openrouter?limit=100"
```

#### GET `/pricing/audit/comparisons/{model_id}`
Compare model pricing across gateways.

```bash
curl "http://localhost:8000/pricing/audit/comparisons/Qwen2.5-72B-Instruct?threshold=10"
```

**Response**:
```json
{
  "model_id": "Qwen2.5-72B-Instruct",
  "comparison_count": 2,
  "worst_variance_pct": 2080.0,
  "comparisons": [
    {
      "gateway_a": "deepinfra",
      "gateway_b": "alibaba-cloud",
      "prompt_variance_pct": 2080.0,
      "completion_variance_pct": 2400.0,
      "severity": "critical"
    }
  ]
}
```

#### GET `/pricing/audit/cost-impact/{model_id}`
Calculate cost impact of pricing differences.

```bash
curl "http://localhost:8000/pricing/audit/cost-impact/gpt-4?monthly_tokens=1000000000"
```

**Response**:
```json
{
  "model_id": "gpt-4",
  "monthly_token_volume": 1000000000,
  "monthly_cost_comparison": [
    {
      "gateway": "clarifai",
      "monthly_cost": 90000.0,
      "annual_cost": 1080000.0
    }
  ],
  "monthly_savings_opportunity": 0.0,
  "annual_savings_opportunity": 0.0,
  "cheapest_provider": "clarifai",
  "most_expensive_provider": "clarifai"
}
```

#### POST `/pricing/audit/snapshot`
Record pricing snapshot.

```bash
curl -X POST "http://localhost:8000/pricing/audit/snapshot"
```

#### GET `/pricing/audit/export`
Export audit data.

```bash
curl "http://localhost:8000/pricing/audit/export?format=json"
```

#### GET `/pricing/audit/dashboard`
Get complete dashboard data.

```bash
curl "http://localhost:8000/pricing/audit/dashboard?days=30"
```

---

### Provider Audit Endpoints

#### GET `/pricing/audit/providers`
Audit all provider APIs.

```bash
curl "http://localhost:8000/pricing/audit/providers"
```

**Response**:
```json
{
  "audit_type": "provider_api_audit",
  "status": "complete",
  "generated_at": "2025-11-23T12:05:00",
  "summary": {
    "providers_audited": 5,
    "providers_successful": 3,
    "providers_failed": 2,
    "total_discrepancies": 12,
    "critical_discrepancies": 1
  },
  "top_discrepancies": [
    {
      "gateway": "openrouter",
      "model_id": "gpt-4",
      "field": "completion",
      "stored_price": 0.06,
      "api_price": 0.065,
      "difference_pct": 8.33,
      "impact_severity": "moderate"
    }
  ],
  "recommendations": [
    "🟠 MAJOR: 1 major discrepancies in 1 provider(s). Review and update pricing data."
  ]
}
```

#### GET `/pricing/audit/providers/{provider_name}`
Audit specific provider.

```bash
curl "http://localhost:8000/pricing/audit/providers/openrouter"
```

---

### Pricing Sync Endpoints

#### POST `/pricing/sync/dry-run`
Test sync without making changes.

```bash
curl -X POST "http://localhost:8000/pricing/sync/dry-run"
curl -X POST "http://localhost:8000/pricing/sync/dry-run?providers=openrouter,featherless"
```

**Response**:
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
      "dry_run": true,
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

#### POST `/pricing/sync/run`
Execute actual pricing sync.

```bash
# Run immediately
curl -X POST "http://localhost:8000/pricing/sync/run"

# Run in background
curl -X POST "http://localhost:8000/pricing/sync/run?background=true"

# Sync specific providers
curl -X POST "http://localhost:8000/pricing/sync/run?providers=openrouter,featherless"
```

#### POST `/pricing/sync/run/{provider}`
Sync specific provider.

```bash
# Dry-run first
curl -X POST "http://localhost:8000/pricing/sync/run/openrouter?dry_run=true"

# Execute
curl -X POST "http://localhost:8000/pricing/sync/run/openrouter?dry_run=false"

# Run in background
curl -X POST "http://localhost:8000/pricing/sync/run/openrouter?background=true"
```

#### GET `/pricing/sync/history`
Get sync history.

```bash
curl "http://localhost:8000/pricing/sync/history?limit=50"
```

#### GET `/pricing/sync/status`
Get sync status.

```bash
curl "http://localhost:8000/pricing/sync/status"
```

#### POST `/pricing/sync/schedule`
Configure sync schedule.

```bash
curl -X POST "http://localhost:8000/pricing/sync/schedule?interval_hours=24&enabled=true"
```

---

## Usage Examples

### 1. Find Pricing Anomalies

```python
import httpx

async with httpx.AsyncClient() as client:
    # Get anomalies
    response = await client.get(
        "http://localhost:8000/pricing/audit/anomalies",
        params={"threshold": 50, "severity": "critical"}
    )
    anomalies = response.json()

    for anomaly in anomalies["anomalies"]:
        print(f"{anomaly['model_id']}: "
              f"{anomaly['gateway_a']} vs {anomaly['gateway_b']} "
              f"({anomaly['prompt_variance_pct']}% variance)")
```

### 2. Run Provider Audit

```python
import httpx

async with httpx.AsyncClient() as client:
    # Audit all providers
    response = await client.get(
        "http://localhost:8000/pricing/audit/providers"
    )
    audit = response.json()

    print(f"Providers audited: {audit['summary']['providers_audited']}")
    print(f"Discrepancies found: {audit['summary']['total_discrepancies']}")

    for recommendation in audit["recommendations"]:
        print(f"  {recommendation}")
```

### 3. Dry-Run Pricing Sync

```python
import httpx

async with httpx.AsyncClient() as client:
    # Test sync
    response = await client.post(
        "http://localhost:8000/pricing/sync/dry-run"
    )
    sync_plan = response.json()

    if sync_plan["changes_would_be_made"]:
        print(f"Would update {sync_plan['summary']['total_updates']} models")
        for provider, result in sync_plan["results"].items():
            print(f"  {provider}: {result['models_updated']} updates")
```

### 4. Calculate Cost Impact

```python
import httpx

async with httpx.AsyncClient() as client:
    # Get cost impact
    response = await client.get(
        "http://localhost:8000/pricing/audit/cost-impact/gpt-4",
        params={"monthly_tokens": 1_000_000_000}
    )
    analysis = response.json()

    print(f"Cheapest: {analysis['cheapest_provider']}: ${analysis['monthly_cost_comparison'][0]['monthly_cost']}")
    print(f"Annual savings potential: ${analysis['annual_savings_opportunity']}")
```

---

## Configuration

### Enable Audit Service

Edit `src/main.py` to ensure pricing_audit is included:

```python
routes_to_load = [
    # ... other routes ...
    ("pricing_audit", "Pricing Audit Dashboard"),
    ("pricing_sync", "Pricing Sync Service"),
]
```

### Adjust Sync Configuration

Edit `src/services/pricing_sync_service.py`:

```python
class PricingSyncConfig:
    # Which providers to auto-sync
    AUTO_SYNC_PROVIDERS = ["openrouter", "featherless", "nearai"]

    # Max deviation before rejecting changes
    MAX_DEVIATION_PCT = 50.0

    # Minimum price change to update (USD)
    MIN_CHANGE_THRESHOLD = 0.0001

    # Backup retention
    BACKUP_RETENTION_DAYS = 30

    # Preserve manually-set prices
    PRESERVE_MANUAL_OVERRIDES = True
```

---

## Scheduled Syncing

To set up automated scheduling, integrate with a task queue:

### Option 1: APScheduler

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job('cron', hour=0)
async def sync_pricing():
    service = PricingSyncService()
    await service.sync_all_providers(dry_run=False)

scheduler.start()
```

### Option 2: Celery + Redis

```python
from celery import Celery

app = Celery()

@app.task
def sync_pricing_task():
    import asyncio
    asyncio.run(run_scheduled_sync())

# Add to beat schedule
from celery.schedules import crontab
app.conf.beat_schedule = {
    'sync-pricing-daily': {
        'task': 'tasks.sync_pricing_task',
        'schedule': crontab(hour=0, minute=0),
    },
}
```

### Option 3: GitHub Actions / Cron Job

Create `.github/workflows/pricing-sync.yml`:

```yaml
name: Daily Pricing Sync
on:
  schedule:
    - cron: '0 0 * * *'

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Run pricing sync
        run: |
          curl -X POST https://api.gatewayz.ai/pricing/sync/run \
            -H "Authorization: Bearer ${{ secrets.ADMIN_API_KEY }}"
```

---

## Monitoring & Alerts

### Monitor Sync Logs

```python
import json
from pathlib import Path

sync_log_file = Path("/root/repo/src/data/pricing_sync.log")

with open(sync_log_file) as f:
    for line in f:
        log_entry = json.loads(line)
        if log_entry["status"] == "failed":
            print(f"⚠️  Failed: {log_entry['message']}")
```

### Get Anomaly Summary

```python
import httpx

async def get_anomaly_summary():
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "http://localhost:8000/pricing/audit/anomalies",
            params={"threshold": 50}
        )
        data = response.json()

        return {
            "total": data["anomaly_count"],
            "critical": len([a for a in data["anomalies"]
                           if a["severity"] == "critical"]),
            "major": len([a for a in data["anomalies"]
                        if a["severity"] == "major"]),
        }
```

---

## Troubleshooting

### Issue: "Provider does not expose pricing via public API"

**Problem**: Some providers (DeepInfra, Alibaba Cloud) don't have public pricing APIs.

**Solution**:
- Manually verify pricing from provider dashboard
- Use manual_pricing.json for these providers
- Set up alerts when pricing needs manual update

### Issue: "Sync rejected due to MAX_DEVIATION_PCT"

**Problem**: Detected price change exceeds 50% threshold.

**Solution**:
1. Verify in provider dashboard that price actually changed
2. If legitimate, increase `MAX_DEVIATION_PCT` temporarily
3. Run audit first to investigate

### Issue: "No pricing data from API"

**Problem**: Provider API not returning data.

**Solution**:
1. Check provider API status
2. Verify network connectivity
3. Check API authentication/rate limits
4. Run with `dry_run=true` to isolate issue

---

## Files & Directories

```
/root/repo/
├── src/
│   ├── services/
│   │   ├── pricing_audit_service.py       # Audit service
│   │   ├── pricing_provider_auditor.py    # Provider API auditor
│   │   ├── pricing_sync_service.py        # Auto-sync service
│   │   └── pricing_lookup.py              # Manual pricing lookup
│   │
│   ├── routes/
│   │   ├── pricing_audit.py               # Audit endpoints
│   │   └── pricing_sync.py                # Sync endpoints
│   │
│   └── data/
│       ├── manual_pricing.json            # Main pricing file
│       └── pricing_history/
│           ├── pricing_history.jsonl      # Historical records
│           ├── snapshots/                 # Timestamped snapshots
│           └── pricing_backups/           # Auto-backups
│
└── docs/
    ├── PRICING_AUDIT_DETAILED_COMPARISON.md
    ├── PRICING_AUDIT_SYSTEM.md            # This file
    └── PRICING_SYSTEM.md
```

---

## Performance Considerations

- **Audit Service**: Minimal overhead, processes in-memory
- **Provider Auditor**: ~30-60s for full audit (async network calls)
- **Sync Service**: Configurable to run during off-peak hours
- **Data Retention**: Snapshots kept for 30 days by default

---

## Security

- All endpoints can be protected with API key authentication
- Backup files retained for 30 days in case of errors
- Sync operations logged with timestamp and status
- No pricing data is exported externally by default
- Manual overrides prevent accidental overwrites

---

## Future Enhancements

1. **Database-backed Pricing**: Move from JSON to PostgreSQL
2. **Real-time Sync**: WebSocket updates for price changes
3. **ML-based Anomaly Detection**: Predict suspicious pricing changes
4. **Cost Optimization Engine**: Automatic routing to cheapest provider
5. **Scheduled Background Tasks**: Celery/APScheduler integration
6. **Grafana Dashboards**: Visualization of pricing trends
7. **SMS/Email Alerts**: Notify on critical pricing changes
8. **Pricing Forecasting**: Predict future price trends

---

## Support

For issues or questions about the pricing audit system:

1. Check logs in `/root/repo/src/data/pricing_history/`
2. Run dry-run audit first: `POST /pricing/sync/dry-run`
3. Review recommendations in audit reports
4. Check provider API status pages

---

**Last Updated**: 2025-11-23
**Maintained By**: Gatewayz Development Team
