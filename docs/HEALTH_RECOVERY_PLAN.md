# 🏥 Model Health Recovery Implementation Plan

**Issue**: #1094 - Model Health Degradation (79% healthy → Target: >95%)
**Created**: 2026-02-11
**Priority**: HIGH
**Timeline**: 7 days
**Estimated Effort**: ~40 hours

---

## 🔥 Critical Finding

**20 out of 31 gateways (64.5%) are missing API keys in local environment!**

This alone could explain most of the 21% unhealthy rate. If production has similar issues, fixing API keys will improve health dramatically.

---

## Executive Summary

### Current State
- **Model Health**: 79% (304-306 out of 387 models healthy)
- **Gateway Health**: 68% (21 out of 31 gateways healthy)
- **Tracked vs Catalog**: 412 tracked, 387 in catalog (expected - not a bug)
- **Root Cause**: Missing API keys, aggressive timeouts, no health-based routing

### Target State (7 days)
- **Model Health**: >95%
- **Gateway Health**: >90%
- **Auto-failover**: Enabled
- **Alerting**: Configured
- **Dashboard**: Deployed

### Expected Improvements by Phase
| Phase | Timeline | Health Target | Key Changes |
|-------|----------|---------------|-------------|
| Phase 1 | 48hrs | 85-90% | Fix API keys, increase timeouts |
| Phase 2 | 5 days | 92-95% | Health-based routing, dashboard |
| Phase 3 | 7 days | >95% | Alerting, cleanup, monitoring |

---

## Phase 1: Immediate Fixes (24-48 hours)

### Task 1.1: Audit & Fix API Keys ⏱️ 2 hours

**File Created**: `scripts/audit_gateway_keys.py` ✅

**Action Required**:
```bash
# Run audit
python3 scripts/audit_gateway_keys.py

# Add missing keys to .env
# For production, add to Railway/Vercel environment variables
```

**Missing Keys** (20 gateways):
```env
OPENROUTER_API_KEY=your-key-here
FEATHERLESS_API_KEY=your-key-here
CHUTES_API_KEY=your-key-here
GROQ_API_KEY=your-key-here
FIREWORKS_API_KEY=your-key-here
TOGETHER_API_KEY=your-key-here
DEEPINFRA_API_KEY=your-key-here
CEREBRAS_API_KEY=your-key-here
XAI_API_KEY=your-key-here
NEBIUS_API_KEY=your-key-here
NOVITA_API_KEY=your-key-here
HUG_API_KEY=your-key-here
AIMO_API_KEY=your-key-here
NEAR_API_KEY=your-key-here
AIHUBMIX_API_KEY=your-key-here
ANANNAS_API_KEY=your-key-here
ONEROUTER_API_KEY=your-key-here
OPENAI_API_KEY=your-key-here
SIMPLISMART_API_KEY=your-key-here
CANOPYWAVE_API_KEY=your-key-here
```

**Expected Improvement**: +40-50% health if all keys configured

---

### Task 1.2: Increase Health Check Timeouts ⏱️ 30 mins

**File**: `src/services/intelligent_health_monitor.py:122-143`

**Current Values**:
```python
self.tier_config = {
    MonitoringTier.CRITICAL: {"timeout_seconds": 15},
    MonitoringTier.POPULAR: {"timeout_seconds": 20},
    MonitoringTier.STANDARD: {"timeout_seconds": 30},
    MonitoringTier.ON_DEMAND: {"timeout_seconds": 30},
}
```

**Change To**:
```python
self.tier_config = {
    MonitoringTier.CRITICAL: {
        "interval_seconds": 300,  # 5 minutes
        "timeout_seconds": 30,  # CHANGED: 15 → 30 seconds
        "max_tokens": 10,
    },
    MonitoringTier.POPULAR: {
        "interval_seconds": 1800,  # 30 minutes
        "timeout_seconds": 45,  # CHANGED: 20 → 45 seconds
        "max_tokens": 10,
    },
    MonitoringTier.STANDARD: {
        "interval_seconds": 7200,  # 2 hours
        "timeout_seconds": 60,  # CHANGED: 30 → 60 seconds
        "max_tokens": 5,
    },
    MonitoringTier.ON_DEMAND: {
        "interval_seconds": 14400,  # 4 hours
        "timeout_seconds": 60,  # CHANGED: 30 → 60 seconds
        "max_tokens": 5,
    },
}
```

**Rationale**:
- Reduces false timeout failures
- Some models have cold start delays (serverless)
- Network latency can cause premature timeouts

**Expected Improvement**: +5-10% health

---

### Task 1.3: Reduce Batch Check Rate ⏱️ 30 mins

**File**: `src/services/model_health_monitor.py:979-983`

**Current Values**:
```python
health_monitor = ModelHealthMonitor(
    check_interval=300,  # Every 5 minutes
    batch_size=20,  # 20 models at once
    batch_interval=15.0,  # 15 seconds between batches
    fetch_chunk_size=100,
)
```

**Change To**:
```python
health_monitor = ModelHealthMonitor(
    check_interval=300,  # Every 5 minutes
    batch_size=10,  # CHANGED: 20 → 10 (reduce load)
    batch_interval=30.0,  # CHANGED: 15s → 30s (slower checks)
    fetch_chunk_size=100,
)
```

**Rationale**:
- Prevents rate limit triggers (429 errors)
- OpenRouter limits: 4 model switches/minute
- Reduces provider load from health checks

**Expected Improvement**: +3-5% health

---

### Task 1.4: Improve Logging Clarity ⏱️ 15 mins

**File**: `src/services/intelligent_health_monitor.py:889`

**Current**:
```python
logger.info(f"Published health data to Redis cache: {total_models} models ({healthy_models} healthy), ...")
```

**Change To**:
```python
logger.info(
    f"Published health data to Redis cache: {total_models} models in catalog "
    f"({healthy_models} healthy, {unhealthy_models} unhealthy), "
    f"{total_providers} providers, {total_gateways} gateways "
    f"({healthy_gateways} healthy), tracked: {tracked_models} models "
    f"(includes models from all {total_gateways} gateways, not just OpenRouter)"
)
```

**Rationale**: Clarifies why tracked ≠ catalog count

---

## Phase 2: Health-Based Routing (Days 3-5)

### Task 2.1: Create Health Check Helper ⏱️ 1 hour

**New File**: `src/services/health_routing.py`

```python
"""
Health-Based Routing Service

Checks model health before routing and provides failover recommendations.
"""
import logging
from typing import Any

from src.services.simple_health_cache import simple_health_cache

logger = logging.getLogger(__name__)


def is_model_healthy(model_id: str, provider: str) -> tuple[bool, str | None]:
    """
    Check if a model is healthy before routing.

    Args:
        model_id: Model identifier
        provider: Provider/gateway name

    Returns:
        (is_healthy, error_message)
    """
    try:
        models_health = simple_health_cache.get_models_health()

        if not models_health:
            # No health data available - assume healthy
            logger.debug(f"No health data available for {model_id} on {provider}, assuming healthy")
            return True, None

        # Find model health status
        model_health = next(
            (m for m in models_health
             if m.get("model_id") == model_id and m.get("provider") == provider),
            None
        )

        if not model_health:
            # Model not tracked - assume healthy
            return True, None

        status = model_health.get("status", "unknown")

        if status == "unhealthy":
            error_msg = f"Model {model_id} on {provider} is currently unhealthy"
            uptime = model_health.get("uptime_percentage", 0)
            error_count = model_health.get("error_count", 0)

            logger.warning(
                f"Health check failed for {model_id} on {provider}: "
                f"status={status}, uptime={uptime:.1f}%, errors={error_count}"
            )

            return False, error_msg

        return True, None

    except Exception as e:
        # Never block requests due to health check errors
        logger.error(f"Health check error for {model_id} on {provider}: {e}")
        return True, None


def get_healthy_alternative_provider(model_id: str, current_provider: str) -> str | None:
    """
    Find a healthy alternative provider for a model.

    Args:
        model_id: Model identifier
        current_provider: Currently attempted provider

    Returns:
        Alternative provider name or None
    """
    try:
        models_health = simple_health_cache.get_models_health()

        if not models_health:
            return None

        # Find all healthy providers for this model
        healthy_alternatives = [
            m for m in models_health
            if m.get("model_id") == model_id
            and m.get("provider") != current_provider
            and m.get("status") == "healthy"
        ]

        if not healthy_alternatives:
            return None

        # Sort by uptime (best first)
        healthy_alternatives.sort(
            key=lambda m: m.get("uptime_percentage", 0),
            reverse=True
        )

        best_alternative = healthy_alternatives[0]
        alt_provider = best_alternative.get("provider")

        logger.info(
            f"Found healthy alternative for {model_id}: {alt_provider} "
            f"(uptime: {best_alternative.get('uptime_percentage', 0):.1f}%)"
        )

        return alt_provider

    except Exception as e:
        logger.error(f"Error finding alternative provider: {e}")
        return None
```

---

### Task 2.2: Implement Health Routing in Chat Endpoint ⏱️ 2 hours

**File**: `src/routes/chat.py`

**Find the main chat endpoint** (around line 100-200, look for `@router.post("/chat/completions")`):

```python
# Add import at top of file
from src.services.health_routing import is_model_healthy, get_healthy_alternative_provider

# In the chat completion handler, BEFORE routing to provider:
@router.post("/chat/completions")
async def chat_completions(request: ChatRequest, ...):
    # ... existing code ...

    model = request.model
    provider = determine_provider(model)  # Your existing logic

    # NEW: Check model health BEFORE routing
    is_healthy, health_error = is_model_healthy(model, provider)

    if not is_healthy:
        # Try to find healthy alternative
        alt_provider = get_healthy_alternative_provider(model, provider)

        if alt_provider:
            logger.info(
                f"Routing {model} from unhealthy {provider} to healthy {alt_provider}"
            )
            provider = alt_provider
        else:
            # No healthy alternative - log warning but proceed
            # (May fail, but circuit breaker will handle it)
            logger.warning(
                f"No healthy alternative for {model} on {provider}, "
                f"proceeding anyway (circuit breaker will handle failures)"
            )

    # Continue with normal routing...
    try:
        response = await route_to_provider(provider, model, request)
        return response
    except HTTPException as e:
        # Existing error handling...
```

**Expected Impact**:
- Reduces failed requests by 40-50%
- Improves average response time
- Better user experience

---

### Task 2.3: Add Health Dashboard Endpoint ⏱️ 2 hours

**File**: `src/routes/health.py`

```python
@router.get("/dashboard", response_model=dict)
async def health_dashboard():
    """
    Comprehensive health dashboard with detailed breakdown.

    Returns unhealthy models grouped by provider, gateway health,
    and actionable recommendations.
    """
    try:
        # Get all health data
        system_health = simple_health_cache.get_system_health()
        models_health = simple_health_cache.get_models_health()
        providers_health = simple_health_cache.get_providers_health()
        gateways_health = simple_health_cache.get_gateways_health()

        if not system_health:
            return {
                "status": "unknown",
                "message": "Health data not yet available",
                "recommendation": "Wait for first health check cycle (5 minutes)"
            }

        # Group unhealthy models by provider
        unhealthy_models = [
            m for m in (models_health or [])
            if m.get("status") == "unhealthy"
        ]

        unhealthy_by_provider = {}
        for model in unhealthy_models:
            provider = model.get("provider", "unknown")
            if provider not in unhealthy_by_provider:
                unhealthy_by_provider[provider] = []
            unhealthy_by_provider[provider].append({
                "model_id": model.get("model_id"),
                "error_count": model.get("error_count", 0),
                "uptime": model.get("uptime_percentage", 0),
                "last_checked": model.get("last_checked"),
            })

        # Find providers with high failure rates
        problematic_providers = []
        for provider_data in (providers_health or []):
            total = provider_data.get("total_models", 0)
            unhealthy = provider_data.get("unhealthy_models", 0)

            if total > 0 and (unhealthy / total) > 0.3:  # >30% failure rate
                problematic_providers.append({
                    "provider": provider_data.get("provider"),
                    "gateway": provider_data.get("gateway"),
                    "total_models": total,
                    "unhealthy_models": unhealthy,
                    "failure_rate": f"{(unhealthy/total)*100:.1f}%",
                    "status": provider_data.get("status"),
                })

        # Find unconfigured gateways
        unconfigured_gateways = []
        for gw_name, gw_data in (gateways_health or {}).items():
            if gw_data.get("status") == "unconfigured":
                unconfigured_gateways.append({
                    "gateway": gw_name,
                    "error": gw_data.get("error"),
                })

        # Generate recommendations
        recommendations = []

        if unconfigured_gateways:
            recommendations.append({
                "priority": "HIGH",
                "action": "Configure missing API keys",
                "details": f"{len(unconfigured_gateways)} gateways need API keys",
                "gateways": [g["gateway"] for g in unconfigured_gateways],
            })

        if problematic_providers:
            recommendations.append({
                "priority": "MEDIUM",
                "action": "Investigate failing providers",
                "details": f"{len(problematic_providers)} providers have >30% failure rate",
                "providers": [p["provider"] for p in problematic_providers],
            })

        overall_health = system_health.get("healthy_models", 0)
        total_models = system_health.get("total_models", 1)
        health_pct = (overall_health / total_models) * 100 if total_models > 0 else 0

        if health_pct < 90:
            recommendations.append({
                "priority": "HIGH",
                "action": "Overall health below 90% threshold",
                "details": f"Current: {health_pct:.1f}%, Target: >95%",
                "next_steps": [
                    "Review unhealthy models list",
                    "Check provider status pages",
                    "Verify API key validity",
                ]
            })

        return {
            "timestamp": system_health.get("last_updated"),
            "overall_health": {
                "status": system_health.get("overall_status"),
                "health_percentage": f"{health_pct:.1f}%",
                "healthy_models": system_health.get("healthy_models"),
                "unhealthy_models": system_health.get("unhealthy_models"),
                "total_models": total_models,
            },
            "gateways": {
                "total": system_health.get("total_gateways"),
                "healthy": system_health.get("healthy_gateways"),
                "unconfigured": len(unconfigured_gateways),
            },
            "unhealthy_models_by_provider": unhealthy_by_provider,
            "problematic_providers": problematic_providers,
            "unconfigured_gateways": unconfigured_gateways,
            "recommendations": recommendations,
        }

    except Exception as e:
        logger.error(f"Dashboard error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
```

---

## Phase 3: Monitoring & Alerting (Days 6-7)

### Task 3.1: Add Health Degradation Alerting ⏱️ 1 hour

**File**: `src/services/intelligent_health_monitor.py`

**In `_publish_health_to_cache()` method, after line 889, add**:

```python
# After publishing health data, check for degradation
await self._check_health_threshold_and_alert(
    total_models=total_models,
    healthy_models=healthy_models,
    unhealthy_models=unhealthy_models,
    system_uptime=system_uptime,
)
```

**Add new method**:

```python
async def _check_health_threshold_and_alert(
    self,
    total_models: int,
    healthy_models: int,
    unhealthy_models: int,
    system_uptime: float,
):
    """
    Check if health has degraded below threshold and send alerts.

    Alerts when:
    - Overall health drops below 90%
    - Health drops >10% in last hour
    - Critical models are unhealthy
    """
    try:
        # Calculate health percentage
        if total_models == 0:
            return

        health_pct = (healthy_models / total_models) * 100

        # Check threshold
        HEALTH_THRESHOLD = 90.0  # Alert if below 90%

        if health_pct < HEALTH_THRESHOLD:
            error_message = (
                f"🚨 HEALTH ALERT: Model health degraded to {health_pct:.1f}% "
                f"(threshold: {HEALTH_THRESHOLD}%)\n"
                f"Healthy: {healthy_models}/{total_models} models\n"
                f"Unhealthy: {unhealthy_models} models\n"
                f"System Uptime: {system_uptime:.1f}%"
            )

            logger.error(error_message)

            # Send to Sentry
            try:
                import sentry_sdk
                sentry_sdk.capture_message(
                    error_message,
                    level="error",
                    extras={
                        "health_percentage": health_pct,
                        "healthy_models": healthy_models,
                        "unhealthy_models": unhealthy_models,
                        "total_models": total_models,
                        "system_uptime": system_uptime,
                    }
                )
                logger.info("Sent health degradation alert to Sentry")
            except Exception as sentry_error:
                logger.warning(f"Failed to send Sentry alert: {sentry_error}")

            # Could also send to Slack, email, etc.

    except Exception as e:
        logger.error(f"Error checking health threshold: {e}")
```

---

### Task 3.2: Add Cleanup Job for Deprecated Models ⏱️ 1 hour

**New File**: `scripts/cleanup_deprecated_models.py`

```python
#!/usr/bin/env python3
"""
Cleanup deprecated/non-existent models from health tracking.

Removes models that:
- Have 100% error rate over 7 days
- Haven't been checked in 30+ days
- No longer exist in any gateway catalog
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.supabase_config import supabase
from src.db.model_health import get_all_model_health


def cleanup_deprecated_models(dry_run: bool = True):
    """Remove deprecated models from health tracking."""

    print("=" * 80)
    print("DEPRECATED MODELS CLEANUP")
    print("=" * 80)
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print()

    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    # Get all tracked models
    all_models = get_all_model_health(limit=10000)

    to_remove = []

    for model in all_models:
        provider = model["provider"]
        model_id = model["model"]
        call_count = model["call_count"]
        error_count = model["error_count"]
        last_called_at = model.get("last_called_at")

        # Parse timestamp
        if last_called_at:
            last_called = datetime.fromisoformat(last_called_at.replace("Z", "+00:00"))
        else:
            last_called = None

        # Criteria for removal
        should_remove = False
        reason = ""

        # 1. Never checked (stale entry)
        if not last_called:
            should_remove = True
            reason = "Never checked"

        # 2. Not checked in 30+ days
        elif last_called < thirty_days_ago:
            should_remove = True
            reason = f"Not checked in {(now - last_called).days} days"

        # 3. 100% error rate with 10+ attempts
        elif call_count >= 10 and error_count == call_count:
            should_remove = True
            reason = f"100% error rate ({error_count}/{call_count} calls)"

        if should_remove:
            to_remove.append({
                "provider": provider,
                "model": model_id,
                "reason": reason,
                "last_called": last_called.isoformat() if last_called else "Never",
                "call_count": call_count,
                "error_count": error_count,
            })

    print(f"Found {len(to_remove)} models to remove:")
    print()

    for item in to_remove[:20]:  # Show first 20
        print(f"  {item['provider']:15} | {item['model']:40} | {item['reason']}")

    if len(to_remove) > 20:
        print(f"  ... and {len(to_remove) - 20} more")

    print()

    if not dry_run and to_remove:
        print("Removing models from database...")
        removed_count = 0

        for item in to_remove:
            try:
                supabase.table("model_health_tracking").delete().eq(
                    "provider", item["provider"]
                ).eq("model", item["model"]).execute()
                removed_count += 1
            except Exception as e:
                print(f"  Error removing {item['model']}: {e}")

        print(f"✅ Removed {removed_count} deprecated models")

    elif dry_run and to_remove:
        print("To actually remove these models, run with --live flag:")
        print(f"  python3 scripts/cleanup_deprecated_models.py --live")

    else:
        print("✅ No deprecated models found!")

    print()
    return len(to_remove)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Cleanup deprecated models")
    parser.add_argument("--live", action="store_true", help="Actually remove models (default is dry-run)")
    args = parser.parse_args()

    cleanup_deprecated_models(dry_run=not args.live)
```

---

## Implementation Checklist

### Phase 1: Immediate (48 hours) ⏱️ 8 hours
- [ ] Run `scripts/audit_gateway_keys.py`
- [ ] Add missing API keys to .env / production env
- [ ] Edit `intelligent_health_monitor.py` - increase timeouts
- [ ] Edit `model_health_monitor.py` - reduce batch rate
- [ ] Edit `intelligent_health_monitor.py` - improve logging
- [ ] Deploy changes
- [ ] Monitor health metrics for 24 hours
- [ ] **Expected Result**: 79% → 85-90%

### Phase 2: Health Routing (Days 3-5) ⏱️ 8 hours
- [ ] Create `src/services/health_routing.py`
- [ ] Implement health check in `routes/chat.py`
- [ ] Implement health check in `routes/messages.py`
- [ ] Add `/health/dashboard` endpoint
- [ ] Test failover with unhealthy models
- [ ] Deploy changes
- [ ] **Expected Result**: 85-90% → 92-95%

### Phase 3: Monitoring (Days 6-7) ⏱️ 4 hours
- [ ] Add alerting to `intelligent_health_monitor.py`
- [ ] Create `scripts/cleanup_deprecated_models.py`
- [ ] Run cleanup script (dry-run first)
- [ ] Configure Sentry alerts
- [ ] Monitor for 24 hours
- [ ] **Expected Result**: 92-95% → >95%

### Documentation ⏱️ 2 hours
- [ ] Update README with health monitoring info
- [ ] Document new endpoints in API docs
- [ ] Add troubleshooting guide
- [ ] Update deployment docs

---

## Testing Strategy

### Unit Tests
```bash
# Test health routing logic
pytest tests/services/test_health_routing.py -v

# Test health dashboard
pytest tests/routes/test_health.py::test_dashboard -v
```

### Integration Tests
```bash
# Test end-to-end with unhealthy models
python scripts/test_health_failover.py
```

### Manual Testing
1. **Simulate unhealthy model**: Remove API key for one gateway
2. **Verify failover**: Make request to affected model
3. **Check dashboard**: Visit `/health/dashboard`
4. **Verify alerts**: Check Sentry for degradation alerts

---

## Monitoring & Success Metrics

### Key Metrics to Track

| Metric | Baseline | Target (7d) | Current |
|--------|----------|-------------|---------|
| Model Health % | 79% | >95% | ___ |
| Gateway Health % | 68% | >90% | ___ |
| Failed Requests/hr | Unknown | <5% | ___ |
| Avg Response Time | Unknown | -30% | ___ |
| False Timeouts | Unknown | <5% | ___ |
| Circuit Breakers Open | Unknown | <3 | ___ |

### Daily Health Check
```bash
# Run daily to track progress
curl https://api.gatewayz.ai/health/dashboard | jq '.overall_health'
```

### Alerting Thresholds
- 🚨 **CRITICAL**: Health < 80%
- ⚠️ **WARNING**: Health < 90%
- ✅ **OK**: Health ≥ 95%

---

## Rollback Plan

If health worsens after deployment:

1. **Immediate**: Revert timeout changes
   ```bash
   git revert <commit-hash>
   git push
   ```

2. **Quick Fix**: Disable health-based routing
   ```python
   # In routes/chat.py, comment out health check
   # is_healthy, health_error = is_model_healthy(model, provider)
   ```

3. **Emergency**: Restart health monitoring service
   ```bash
   # Kill existing process
   # Restart with default config
   ```

---

## Next Steps After Completion

1. **Week 2**: Implement predictive health monitoring
2. **Week 3**: Add provider SLA tracking
3. **Week 4**: Optimize health check scheduling
4. **Month 2**: ML-based anomaly detection

---

## Questions & Support

**Issue**: #1094
**Documentation**: `docs/HEALTH_MONITORING.md`
**Contact**: @Armin2708

**Related Issues**:
- #1089 - OpenRouter Circuit Breaker
- Related to overall platform reliability

---

**Last Updated**: 2026-02-11
**Version**: 1.0
**Status**: READY FOR IMPLEMENTATION
