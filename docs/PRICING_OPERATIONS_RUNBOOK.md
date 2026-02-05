# Pricing System Operations Runbook

**Version**: 1.0
**Last Updated**: 2026-02-03
**Owner**: Engineering Team
**Related**: Issue #1038

---

## Overview

This runbook provides operational procedures for managing the automated pricing system that syncs pricing data from 12 provider APIs every 6 hours.

### System Components
- **12 Provider Auto-Sync**: Automated pricing updates from provider APIs
- **Pricing Validation**: Bounds checking and spike detection
- **Health Monitoring**: Staleness detection with Sentry alerts
- **3 Health Endpoints**: Operational visibility
- **7 Prometheus Metrics**: Complete observability

---

## Quick Reference

### Health Check URLs
```bash
# Overall pricing system health
GET https://api.gatewayz.ai/health/pricing

# Check pricing data staleness
GET https://api.gatewayz.ai/health/pricing/staleness

# Check default pricing usage (fallback indicators)
GET https://api.gatewayz.ai/health/pricing/default-usage
```

### Configuration
```python
# Environment Variables
PRICING_SYNC_INTERVAL_HOURS=6  # Default: 6 hours
PRICING_SYNC_PROVIDERS="openrouter,featherless,nearai,alibaba-cloud,together,fireworks,groq,deepinfra,cerebras,novita,nebius,aihubmix"
```

### Key Files
```
src/services/pricing_sync_service.py       # Auto-sync orchestration
src/services/pricing_provider_auditor.py   # Provider API clients
src/services/pricing_validation.py         # Validation logic
src/services/pricing_health_monitor.py     # Health checks
```

---

## Monitoring & Alerting

### Prometheus Metrics

```promql
# Validation failures (should be near 0)
pricing_validation_failures

# Price spikes detected (investigate if >0)
pricing_spike_detected_total

# Bounds violations (should be 0)
pricing_bounds_violations_total

# Pricing staleness in hours (alert if >24)
pricing_staleness_hours

# Models using default pricing (fallback indicator)
models_using_default_pricing

# System health status (0=healthy, 1=warning, 2=critical)
pricing_health_status
```

### Recommended Alerts

**Critical Alerts** (page on-call):
```yaml
- alert: PricingStaleCritical
  expr: pricing_staleness_hours > 72
  severity: critical
  description: "Pricing data hasn't updated in >72 hours"

- alert: PricingBoundsViolation
  expr: pricing_bounds_violations_total > 0
  severity: critical
  description: "Price outside acceptable bounds detected"
```

**Warning Alerts** (notify team):
```yaml
- alert: PricingStaleWarning
  expr: pricing_staleness_hours > 24
  severity: warning
  description: "Pricing data hasn't updated in >24 hours"

- alert: PricingSpikeDetected
  expr: pricing_spike_detected_total > 0
  severity: warning
  description: "Large price change detected"

- alert: HighDefaultPricingUsage
  expr: models_using_default_pricing > 100
  severity: warning
  description: "Many models using fallback pricing"
```

### Sentry Alerts

Automatically sent for:
- Critical pricing staleness (>72 hours)
- Validation failures
- Provider API errors

---

## Common Operations

### 1. Manual Pricing Sync

Trigger immediate sync for all providers:

```bash
# Using API endpoint (if available)
curl -X POST https://api.gatewayz.ai/admin/pricing/sync \
  -H "Authorization: Bearer $ADMIN_API_KEY"

# Or manually via Python
cd /path/to/gatewayz-backend
python3 -c "
from src.services.pricing_sync_service import PricingSyncService
import asyncio

async def sync():
    service = PricingSyncService()
    result = await service.sync_all_providers(dry_run=False)
    print(result)

asyncio.run(sync())
"
```

### 2. Sync Single Provider

```python
python3 -c "
from src.services.pricing_sync_service import PricingSyncService
import asyncio

async def sync_provider(provider):
    service = PricingSyncService()
    result = await service.sync_provider_pricing(provider, dry_run=False)
    print(result)

asyncio.run(sync_provider('openrouter'))  # Replace with provider slug
"
```

### 3. Check Pricing Health

```bash
# Overall health
curl https://api.gatewayz.ai/health/pricing | jq

# Staleness check
curl https://api.gatewayz.ai/health/pricing/staleness | jq

# Default pricing usage
curl https://api.gatewayz.ai/health/pricing/default-usage | jq
```

### 4. View Sync History

```python
python3 -c "
from src.services.pricing_sync_service import PricingSyncService

service = PricingSyncService()
history = service.get_sync_history(provider_slug='openrouter', limit=10)
for entry in history:
    print(f\"{entry['synced_at']}: {entry['status']} - {entry['models_updated']} models\")
"
```

### 5. Validate Pricing Data

```python
python3 -c "
from src.services.pricing_validation import validate_pricing_update

# Example validation
pricing = {
    'prompt': 0.000005,    # $5 per 1M tokens
    'completion': 0.000015  # $15 per 1M tokens
}

result = validate_pricing_update('gpt-4o', pricing)
print(f\"Valid: {result['is_valid']}\")
if not result['is_valid']:
    print(f\"Errors: {result['errors']}\")
if result['warnings']:
    print(f\"Warnings: {result['warnings']}\")
"
```

---

## Troubleshooting

### Issue: Pricing Data is Stale

**Symptoms**:
- `pricing_staleness_hours` metric >24
- Sentry alert: "Pricing data stale"
- Health endpoint shows `status: "stale"`

**Diagnosis**:
1. Check if sync service is running:
   ```bash
   # Check logs for sync attempts
   grep "pricing sync" /var/log/gatewayz/app.log | tail -20
   ```

2. Check provider API status:
   ```bash
   curl https://api.gatewayz.ai/health/pricing | jq '.provider_status'
   ```

**Resolution**:
1. Trigger manual sync (see Common Operations #1)
2. If manual sync fails, check provider API keys:
   ```bash
   # Verify API keys are set
   env | grep -E "(OPENROUTER|TOGETHER|FIREWORKS|GROQ)_API_KEY"
   ```
3. Check provider API health:
   ```bash
   # Example for OpenRouter
   curl -H "Authorization: Bearer $OPENROUTER_API_KEY" \
     https://openrouter.ai/api/v1/models | jq '.data[0].pricing'
   ```

### Issue: Validation Failures

**Symptoms**:
- `pricing_validation_failures` metric increasing
- Models not updating despite fresh provider data
- Logs show "Pricing validation failed"

**Diagnosis**:
```bash
# Check recent validation failures
grep "Pricing validation failed" /var/log/gatewayz/app.log | tail -10
```

**Common Causes**:
1. **Price outside bounds** ($0.10 - $1,000 per 1M tokens)
   - Provider may have incorrect pricing
   - Format conversion error
   - Resolution: Check provider's API response format

2. **Large price spike** (>50% change)
   - Provider updated their pricing
   - Resolution: Review change, update if legitimate

3. **Zero pricing**
   - Free model or API error
   - Resolution: Verify if model should be free

**Resolution**:
1. Check the specific model's pricing:
   ```python
   python3 -c "
   from src.services.pricing_lookup import get_model_pricing
   pricing = get_model_pricing('openrouter', 'gpt-4o')
   print(pricing)
   "
   ```

2. Check provider API response:
   ```bash
   # Example for Together AI
   curl -H "Authorization: Bearer $TOGETHER_API_KEY" \
     https://api.together.xyz/v1/models | jq '.[] | select(.id=="meta-llama/Meta-Llama-3.1-8B-Instruct") | .pricing'
   ```

3. Override manually if needed:
   - Update `src/data/manual_pricing.json`
   - Run sync with `dry_run=True` first to verify

### Issue: Price Spike Detected

**Symptoms**:
- `pricing_spike_detected_total` metric increased
- Sentry alert: "Price spike detected"
- Specific models not updating

**Diagnosis**:
```bash
# Check spike details in logs
grep "Price spike" /var/log/gatewayz/app.log | tail -5
```

**Resolution**:
1. Verify the price change is legitimate:
   - Check provider's pricing page
   - Compare with other providers

2. If legitimate:
   - Price will auto-update on next sync (spike detection is warning-only)
   - Or manually update in `manual_pricing.json`

3. If error:
   - Report to provider
   - Add manual override in `manual_pricing.json`

### Issue: High Default Pricing Usage

**Symptoms**:
- `models_using_default_pricing` metric >100
- Many models falling back to manual pricing

**Diagnosis**:
```bash
# Check which models using defaults
curl https://api.gatewayz.ai/health/pricing/default-usage | jq '.models_using_defaults'
```

**Common Causes**:
1. Provider API down or changed format
2. Missing provider in auto-sync config
3. Models not in provider's API response

**Resolution**:
1. Check provider API status
2. Add missing provider to `PRICING_SYNC_PROVIDERS`
3. Update `manual_pricing.json` for models not in any provider API

### Issue: Provider API Error

**Symptoms**:
- Logs show "Provider API fetch failed"
- Specific provider not syncing
- Provider status shows "error"

**Diagnosis**:
```bash
# Check provider-specific errors
grep "audit_<provider>" /var/log/gatewayz/app.log | grep ERROR
```

**Resolution**:
1. Check API key:
   ```bash
   # Verify key is set and valid
   echo $<PROVIDER>_API_KEY | head -c 20
   ```

2. Test API endpoint manually:
   ```bash
   # Example for Fireworks
   curl -H "Authorization: Bearer $FIREWORKS_API_KEY" \
     https://api.fireworks.ai/inference/v1/models
   ```

3. Check rate limits:
   - Provider may be rate-limiting requests
   - Wait and retry

4. Check API format changes:
   - Provider may have changed their response format
   - Update `audit_<provider>()` method in `pricing_provider_auditor.py`

---

## Maintenance Procedures

### Adding a New Provider

1. **Verify provider has pricing API**:
   ```bash
   # Test endpoint
   curl -H "Authorization: Bearer $NEW_PROVIDER_API_KEY" \
     https://api.newprovider.com/v1/models | jq '.[0].pricing'
   ```

2. **Add audit method** in `src/services/pricing_provider_auditor.py`:
   ```python
   async def audit_newprovider(self) -> ProviderPricingData:
       """Audit NewProvider pricing from their API."""
       try:
           from src.services.newprovider_client import fetch_models_from_newprovider

           models_data = fetch_models_from_newprovider()
           # Extract pricing...

           return ProviderPricingData(
               provider_name="newprovider",
               models=models,
               fetched_at=datetime.now(timezone.utc).isoformat(),
               status="success" if models else "partial",
           )
       except Exception as e:
           logger.error(f"Error auditing NewProvider: {e}")
           return ProviderPricingData(...)
   ```

3. **Register in sync service** (`src/services/pricing_sync_service.py`):
   ```python
   # Add to methods dict
   "newprovider": self.auditor.audit_newprovider,

   # Add to AUTO_SYNC_PROVIDERS list
   "newprovider",  # ✅ ADDED: Description

   # Add to PROVIDER_FORMATS
   "newprovider": PricingFormat.PER_1M_TOKENS,
   ```

4. **Update config** (`src/config/config.py`):
   ```python
   PRICING_SYNC_PROVIDERS = "...,newprovider"
   ```

5. **Test**:
   ```python
   # Dry run first
   service = PricingSyncService()
   result = await service.sync_provider_pricing('newprovider', dry_run=True)
   print(result)
   ```

6. **Document** in `docs/PRICING_PROVIDER_APIS.md`

### Updating Validation Bounds

If pricing landscape changes and bounds need adjustment:

1. **Edit** `src/services/pricing_validation.py`:
   ```python
   class PricingBounds:
       MIN_PRICE = Decimal("0.0000001")  # Adjust as needed
       MAX_PRICE = Decimal("0.001")      # Adjust as needed
   ```

2. **Update tests** in `tests/services/test_pricing_validation.py`

3. **Deploy** and monitor `pricing_bounds_violations_total`

### Disabling a Provider

If a provider becomes unreliable:

1. **Remove from config**:
   ```bash
   # Update environment variable
   export PRICING_SYNC_PROVIDERS="openrouter,featherless,..."  # Remove provider
   ```

2. **Restart service** to pick up new config

3. **Verify**:
   ```bash
   curl https://api.gatewayz.ai/health/pricing | jq '.providers_synced'
   ```

4. **Add to manual pricing** if needed

---

## Emergency Procedures

### Emergency: Incorrect Pricing Deployed

**Impact**: Users may be overcharged or undercharged

**Immediate Actions**:
1. **Stop auto-sync**:
   ```bash
   export PRICING_SYNC_INTERVAL_HOURS=999999  # Effectively disable
   # Restart service
   ```

2. **Rollback pricing** to last known good state:
   ```sql
   -- Connect to database
   -- Rollback to specific timestamp
   UPDATE model_pricing
   SET
     price_prompt = historical.price_prompt,
     price_completion = historical.price_completion,
     updated_at = NOW()
   FROM pricing_history historical
   WHERE model_pricing.model_id = historical.model_id
     AND historical.created_at = '2026-02-03 10:00:00';  -- Last good timestamp
   ```

3. **Clear cache**:
   ```python
   from src.services.pricing import clear_pricing_cache
   clear_pricing_cache()
   ```

4. **Notify users** if charges affected

5. **Root cause analysis**:
   - Check sync logs
   - Validate provider API responses
   - Review validation logic

### Emergency: All Providers Failing

**Impact**: Pricing becomes stale, may affect billing

**Immediate Actions**:
1. **Check system status**:
   ```bash
   curl https://api.gatewayz.ai/health
   ```

2. **Verify network/DNS**:
   ```bash
   # Test provider connectivity
   curl https://openrouter.ai/api/v1/models
   curl https://api.together.xyz/v1/models
   ```

3. **Check API keys**:
   ```bash
   env | grep API_KEY
   ```

4. **Fallback to manual pricing**:
   - System automatically uses `manual_pricing.json`
   - Verify fallback is working:
     ```bash
     curl https://api.gatewayz.ai/health/pricing/default-usage
     ```

5. **Manual intervention**:
   - Update `manual_pricing.json` with latest known prices
   - Restart service to reload

---

## Best Practices

### 1. Regular Monitoring
- Check Prometheus dashboards daily
- Review Sentry alerts weekly
- Audit pricing accuracy monthly

### 2. Testing Changes
- Always test with `dry_run=True` first
- Validate on staging before production
- Monitor metrics after deployment

### 3. Documentation
- Document any manual pricing overrides
- Update runbook when procedures change
- Log all configuration changes

### 4. Capacity Planning
- Monitor sync duration trends
- Plan for new provider additions
- Scale database as model count grows

### 5. Incident Response
- Follow runbook procedures
- Document incidents in wiki
- Update runbook with learnings

---

## Appendix

### Provider API Endpoints

| Provider | API Endpoint | Auth Header |
|----------|--------------|-------------|
| OpenRouter | `https://openrouter.ai/api/v1/models` | `Authorization: Bearer $OPENROUTER_API_KEY` |
| Featherless | `https://api.featherless.ai/v1/models` | None |
| Near AI | `https://api.near.ai/v1/models` | `Authorization: Bearer $NEARAI_API_KEY` |
| Together AI | `https://api.together.xyz/v1/models` | `Authorization: Bearer $TOGETHER_API_KEY` |
| Fireworks | `https://api.fireworks.ai/inference/v1/models` | `Authorization: Bearer $FIREWORKS_API_KEY` |
| Groq | `https://api.groq.com/openai/v1/models` | `Authorization: Bearer $GROQ_API_KEY` |
| DeepInfra | `https://api.deepinfra.com/v1/openai/models` | `Authorization: Bearer $DEEPINFRA_API_KEY` |
| Cerebras | SDK: `client.models.list()` | In SDK config |
| Novita | `https://api.novita.ai/v3/openai/models` | `Authorization: Bearer $NOVITA_API_KEY` |
| Nebius | `https://api.tokenfactory.nebius.com/v1/models` | `Authorization: Bearer $NEBIUS_API_KEY` |
| AiHubMix | `https://aihubmix.com/api/v1/models` | `Authorization: Bearer $AIHUBMIX_API_KEY` |

### Pricing Format Conversions

```python
# Per-token → Per-1M tokens
per_1m = per_token * 1_000_000

# Per-1K tokens → Per-1M tokens
per_1m = per_1k * 1000

# Cents/token → Per-1M tokens
per_1m = (cents_per_token / 100) * 1_000_000

# Example:
# 0.01 cents/token = $100 per 1M tokens
```

### Related Documentation
- `docs/PRICING_SYSTEM_SUMMARY.md` - Complete project summary
- `docs/PRICING_PROVIDER_APIS.md` - Provider research & API details
- `src/services/pricing_validation.py` - Validation logic source
- `src/services/pricing_health_monitor.py` - Health monitoring source

---

**Questions or Issues?**
- GitHub Issue: #1038
- Engineering Team: @engineering
- On-call Rotation: See PagerDuty

**Document Version**: 1.0
**Last Reviewed**: 2026-02-03
**Next Review**: 2026-05-03
