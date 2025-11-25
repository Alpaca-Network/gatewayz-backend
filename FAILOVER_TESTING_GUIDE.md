# Gatewayz Provider Failover - Testing Guide

## 🎯 What You Have

Your database architecture **DOES support failover** with these components:

### ✅ Database Schema (Complete)

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| **providers** | Store all AI providers | `slug`, `health_status`, `average_response_time_ms`, `is_active` |
| **models** | Store models per provider | `model_id` (canonical), `provider_model_id` (provider-specific), `pricing_*`, `health_status` |
| **model_health_tracking** | Real-time health tracking | `provider`, `model`, `success_count`, `error_count`, `average_response_time_ms` |
| **model_health_history** | Historical health data | `health_status`, `response_time_ms`, `checked_at` |

### ✅ Critical Features

- **Model Aliases**: `model_id` (canonical) vs `provider_model_id` (provider-specific)
  - Example: `gpt-4` → `openai/gpt-4` on OpenRouter, `gpt-4` on Featherless
- **Health Tracking**: Success rates, response times, error counts
- **Pricing Data**: Per-provider pricing for cost optimization
- **Foreign Key Relationships**: Proper joins between providers and models
- **Indexes**: Optimized for failover queries

---

## 🧪 Testing Your Database

### Step 1: Populate the Database

First, sync models from providers into your database:

```bash
# Sync all providers
python scripts/sync_models.py

# Or sync specific providers
python scripts/sync_models.py --providers openrouter cerebras featherless
```

**Expected Output:**
```
✓ OpenRouter: 250 models synced
✓ Cerebras: 3 models synced
✓ Featherless: 45 models synced
```

### Step 2: Run Database Tests

Run the comprehensive test suite:

```bash
python scripts/test_failover_database.py
```

**This tests:**
1. ✅ Database connection
2. ✅ Schema completeness (tables, columns)
3. ✅ Provider data (are providers populated?)
4. ✅ Model data (are models populated?)
5. ✅ Failover queries (can we find alternative providers?)
6. ✅ Sorting logic (are providers prioritized correctly?)
7. ✅ Model aliases (provider-specific IDs)
8. ✅ Health tracking
9. ✅ Failover simulation

**Expected Output:**
```
===========================================================
TEST SUMMARY
===========================================================

  [PASS] Database Connection
  [PASS] Schema Tables
  [PASS] Schema Columns
  [PASS] Providers Data
  [PASS] Models Data
  [PASS] Failover Query - Basic
  [PASS] Failover Query - Sorting
  [PASS] Model Aliases
  [PASS] Health Tracking
  [PASS] Failover Simulation

===========================================================
Total: 10 tests, 10 passed, 0 failed
===========================================================

✓ All tests passed! Database is ready for failover system.
```

### Step 3: Test Failover Logic

Test the failover service manually:

```python
# test_failover_live.py
import asyncio
from src.services.failover_service import explain_failover_for_model

# Check which providers have GPT-4
info = explain_failover_for_model("gpt-4")
print(info)

# Output:
# {
#   "model": "gpt-4",
#   "providers_available": 3,
#   "failover_order": [
#     {"priority": 1, "provider": "openrouter", "health": "healthy"},
#     {"priority": 2, "provider": "featherless", "health": "healthy"},
#     {"priority": 3, "provider": "portkey", "health": "degraded"}
#   ],
#   "recommendation": "Primary: openrouter, Fallback: featherless"
# }
```

---

## 🔍 Live Testing Scenarios

### Scenario 1: Basic Failover Query

Test if database can find alternative providers:

```python
from src.db.failover_db import get_providers_for_model

# Find all providers with GPT-4
providers = get_providers_for_model("gpt-4", active_only=True)

print(f"Found {len(providers)} providers for GPT-4:")
for p in providers:
    print(f"  - {p['provider_slug']}: {p['provider_health_status']}, "
          f"{p['provider_response_time_ms']}ms, ${p['pricing_prompt']:.6f}/1M")
```

**Expected Output:**
```
Found 3 providers for GPT-4:
  - openrouter: healthy, 150ms, $0.000030/1M
  - featherless: healthy, 200ms, $0.000028/1M
  - portkey: degraded, 500ms, $0.000035/1M
```

### Scenario 2: Model Alias Resolution

Test if provider-specific model IDs are tracked:

```python
from src.db.failover_db import get_provider_model_id

# OpenRouter uses "openai/gpt-4", Featherless uses "gpt-4"
openrouter_id = get_provider_model_id("gpt-4", "openrouter")
featherless_id = get_provider_model_id("gpt-4", "featherless")

print(f"OpenRouter: {openrouter_id}")  # openai/gpt-4
print(f"Featherless: {featherless_id}")  # gpt-4
```

### Scenario 3: Health-Based Routing

Test if unhealthy providers are deprioritized:

```python
from src.db.failover_db import get_providers_for_model

# Get providers, sorted by health
providers = get_providers_for_model("llama-3-70b-instruct")

print("Failover order (healthy first):")
for i, p in enumerate(providers, 1):
    print(f"{i}. {p['provider_slug']}: {p['provider_health_status']}")
```

**Expected Output:**
```
Failover order (healthy first):
1. together: healthy
2. fireworks: healthy
3. openrouter: degraded
```

### Scenario 4: Check Model Availability

Test if a model exists on a specific provider:

```python
from src.db.failover_db import check_model_available_on_provider

available = check_model_available_on_provider("gpt-4", "openrouter")
print(f"GPT-4 on OpenRouter: {available}")  # True

available = check_model_available_on_provider("gpt-4", "cerebras")
print(f"GPT-4 on Cerebras: {available}")  # False (Cerebras doesn't have GPT-4)
```

---

## 🚀 Integration with Chat API

### Current Flow (No Failover)

```
User Request → Chat Endpoint → Single Provider → Response or Error
```

### With Failover (New)

```
User Request → Chat Endpoint → Failover Service
                                      ↓
                              1. Query database for providers
                              2. Try provider 1 → Fail
                              3. Try provider 2 → Success
                                      ↓
                                  Response
```

### Implementation Example

Modify your chat endpoint to use failover:

```python
# src/routes/chat.py

from src.services.failover_service import route_with_failover, ProviderFailoverError

@router.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    """OpenAI-compatible chat with automatic failover"""

    try:
        # Use failover system
        response = await route_with_failover(
            model=request.model,
            request_data={
                "messages": request.messages,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "stream": request.stream
            },
            user_preferences={
                "preferred_provider": user.preferred_provider
            }
        )

        # Extract metadata
        metadata = response.pop("_gatewayz_metadata")

        # Log which provider was used
        logger.info(f"Request routed to {metadata['provider_used']} "
                   f"(attempt {metadata['attempt_number']})")

        # Deduct credits based on actual provider pricing
        await deduct_credits(
            user=user,
            tokens=response["usage"]["total_tokens"],
            pricing=metadata["pricing"]
        )

        return response

    except ProviderFailoverError as e:
        logger.error(f"All providers failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": "All providers unavailable for this model",
                    "type": "service_unavailable",
                    "attempts": e.attempts
                }
            }
        )
```

---

## 📊 Monitoring Failover Health

### Dashboard Endpoint

Create an endpoint to show failover status:

```python
@router.get("/admin/failover/status")
async def get_failover_status():
    """Get failover configuration for all models"""

    from src.db.failover_db import get_providers_for_model
    from collections import defaultdict

    # Get all unique models
    supabase = get_supabase_client()
    result = supabase.table("models").select("model_id").execute()

    unique_models = set(row["model_id"] for row in result.data)

    failover_status = []
    for model_id in list(unique_models)[:50]:  # Limit for performance
        providers = get_providers_for_model(model_id)

        failover_status.append({
            "model": model_id,
            "providers_count": len(providers),
            "primary": providers[0]["provider_slug"] if providers else None,
            "fallback": providers[1]["provider_slug"] if len(providers) > 1 else None,
            "has_failover": len(providers) > 1
        })

    return {
        "total_models": len(unique_models),
        "models_with_failover": sum(1 for m in failover_status if m["has_failover"]),
        "failover_status": failover_status
    }
```

### Health Check Endpoint

```python
@router.get("/admin/failover/model/{model_id}")
async def get_model_failover_info(model_id: str):
    """Get detailed failover info for a specific model"""

    from src.services.failover_service import explain_failover_for_model

    return explain_failover_for_model(model_id)
```

---

## ✅ What Your Database Supports

| Feature | Supported | Notes |
|---------|-----------|-------|
| **Multiple providers per model** | ✅ Yes | Query: `get_providers_for_model()` |
| **Provider-specific model IDs** | ✅ Yes | `model_id` vs `provider_model_id` columns |
| **Health-based routing** | ✅ Yes | `health_status` in providers + models tables |
| **Response time tracking** | ✅ Yes | `average_response_time_ms` column |
| **Success rate tracking** | ✅ Yes | `model_health_tracking` table |
| **Cost optimization** | ✅ Yes | `pricing_*` columns for choosing cheapest |
| **Historical health data** | ✅ Yes | `model_health_history` table |
| **Real-time updates** | ✅ Yes | Update health on every API call |
| **Multi-instance support** | ✅ Yes | Database shared across all instances |

---

## 🎯 Final Answer

### Does your database support failover? **YES!**

Your database has:
- ✅ Model-to-provider mapping (providers + models tables with FK)
- ✅ Model aliases (provider-specific IDs)
- ✅ Health tracking (real-time + historical)
- ✅ Performance metrics (response times, success rates)
- ✅ Pricing data (for cost optimization)
- ✅ Proper indexes for fast queries

### What you need to do:

1. **Populate the database:**
   ```bash
   python scripts/sync_models.py
   ```

2. **Test the database:**
   ```bash
   python scripts/test_failover_database.py
   ```

3. **Integrate with chat API:**
   - Use `src/services/failover_service.py`
   - Modify chat endpoint to use `route_with_failover()`

4. **Monitor health:**
   - Health updates automatically on each API call
   - View failover status via admin endpoints

Your architecture is **ready for production failover** with just the integration step remaining!

---

## 📚 Next Steps

1. Run the database test script
2. Check if models are populated (if not, sync them)
3. Review failover service implementation
4. Integrate with your chat endpoint
5. Test live failover with real requests
6. Add monitoring dashboards

**Questions?** Check the test output for specific issues or guidance.
