# 🧪 Live API Testing Guide - Model Database Management

Complete guide to testing the model management APIs that sync models from providers into your database.

---

## 📋 Prerequisites

### 1. Set Your Admin API Key

```bash
# Check if ADMIN_API_KEY is set
echo $ADMIN_API_KEY

# If not set, add to your .env file
echo "ADMIN_API_KEY=your-secret-admin-key-here" >> .env
```

### 2. Start the API Server

```bash
# Option 1: Development mode (hot reload)
uvicorn src.main:app --reload --port 8000

# Option 2: Direct Python
python src/main.py

# Option 3: Production mode
uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
```

**Expected Output:**
```
✅ Sentry initialized (environment: development)
🌐 CORS Configuration:
   Environment: development
   Allowed Origins: [...]
  📊 Observability middleware enabled
  [OK] Model Sync Service (model_sync)
  [OK] Models Catalog Management (models_catalog_management)
```

### 3. Verify Server is Running

```bash
curl http://localhost:8000/health
```

**Expected Response:**
```json
{"status": "healthy"}
```

---

## 🔑 Authentication

All admin endpoints require the `ADMIN_API_KEY` in the Authorization header:

```bash
# Set your admin key
export ADMIN_KEY="your-admin-api-key-here"

# Test authentication
curl -X GET "http://localhost:8000/admin/model-sync/providers" \
  -H "Authorization: Bearer $ADMIN_KEY"
```

---

## 📦 Model Sync API Endpoints

Base path: `/admin/model-sync`

### 1. List Available Providers

**Get all providers that can be synced:**

```bash
curl -X GET "http://localhost:8000/admin/model-sync/providers" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq
```

**Expected Response:**
```json
{
  "providers": [
    "aihubmix",
    "aimo",
    "alibaba",
    "anannas",
    "cerebras",
    "chutes",
    "deepinfra",
    "fal",
    "featherless",
    "fireworks",
    "google-vertex",
    "groq",
    "helicone",
    "huggingface",
    "near",
    "nebius",
    "novita",
    "openrouter",
    "together",
    "vercel-ai-gateway",
    "xai"
  ],
  "count": 21
}
```

### 2. Sync Single Provider

**Sync models from one provider:**

```bash
# Sync OpenRouter models
curl -X POST "http://localhost:8000/admin/model-sync/provider/openrouter" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq
```

**Response:**
```json
{
  "success": true,
  "message": "Synced 250 models from openrouter. Fetched: 250, Transformed: 250, Skipped: 0",
  "details": {
    "success": true,
    "provider": "openrouter",
    "models_fetched": 250,
    "models_transformed": 250,
    "models_synced": 250,
    "models_skipped": 0,
    "provider_id": 1,
    "timestamp": "2025-11-25T10:30:00Z"
  }
}
```

**Sync Other Providers:**
```bash
# Cerebras (fast, 2-3 models)
curl -X POST "http://localhost:8000/admin/model-sync/provider/cerebras" \
  -H "Authorization: Bearer $ADMIN_KEY"

# Featherless (medium, ~45 models)
curl -X POST "http://localhost:8000/admin/model-sync/provider/featherless" \
  -H "Authorization: Bearer $ADMIN_KEY"

# DeepInfra (large, ~150 models)
curl -X POST "http://localhost:8000/admin/model-sync/provider/deepinfra" \
  -H "Authorization: Bearer $ADMIN_KEY"
```

### 3. Dry Run (Test Without Writing)

**Test sync without writing to database:**

```bash
curl -X POST "http://localhost:8000/admin/model-sync/provider/openrouter?dry_run=true" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq
```

**Response:**
```json
{
  "success": true,
  "message": "[DRY RUN] Synced 250 models from openrouter...",
  "details": {
    "success": true,
    "dry_run": true,
    "models_fetched": 250,
    "models_transformed": 250,
    "models_would_sync": 250
  }
}
```

### 4. Sync All Providers

**Sync models from ALL providers:**

```bash
curl -X POST "http://localhost:8000/admin/model-sync/all" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq
```

**Response:**
```json
{
  "success": true,
  "message": "Synced 1,234 models from 21 providers",
  "details": {
    "total_providers": 21,
    "successful_providers": 20,
    "failed_providers": 1,
    "total_models_synced": 1234,
    "results": {
      "openrouter": {"success": true, "models": 250},
      "cerebras": {"success": true, "models": 3},
      "featherless": {"success": true, "models": 45},
      ...
    }
  }
}
```

### 5. Sync Specific Providers Only

**Sync just a few providers:**

```bash
curl -X POST "http://localhost:8000/admin/model-sync/all?providers=openrouter&providers=cerebras&providers=featherless" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq
```

---

## 📊 Model Catalog Query API

Base path: `/models`

### 1. List All Models

**Get all models with pagination:**

```bash
curl -X GET "http://localhost:8000/models/?limit=10&offset=0" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq
```

**Response:**
```json
[
  {
    "id": 1,
    "model_id": "gpt-4",
    "model_name": "GPT-4",
    "provider_model_id": "openai/gpt-4",
    "description": "Most capable GPT-4 model",
    "context_length": 8192,
    "modality": "text->text",
    "pricing_prompt": 0.00003,
    "pricing_completion": 0.00006,
    "health_status": "healthy",
    "is_active": true,
    "providers": {
      "id": 1,
      "slug": "openrouter",
      "name": "OpenRouter",
      "health_status": "healthy"
    }
  },
  ...
]
```

### 2. Filter by Provider

**Get models from a specific provider:**

```bash
# By provider slug
curl -X GET "http://localhost:8000/models/?provider_slug=cerebras&limit=50" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq

# By provider ID
curl -X GET "http://localhost:8000/models/?provider_id=1&limit=50" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq
```

### 3. Search Models

**Search by name or model ID:**

```bash
# Search for GPT models
curl -X GET "http://localhost:8000/models/search?q=gpt-4" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq

# Search for Claude models
curl -X GET "http://localhost:8000/models/search?q=claude" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq

# Search for Llama models
curl -X GET "http://localhost:8000/models/search?q=llama" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq
```

**Response:**
```json
[
  {
    "id": 1,
    "model_id": "gpt-4",
    "model_name": "GPT-4",
    "provider_model_id": "openai/gpt-4",
    "providers": {
      "slug": "openrouter",
      "name": "OpenRouter"
    }
  },
  {
    "id": 45,
    "model_id": "gpt-4",
    "model_name": "GPT-4",
    "provider_model_id": "gpt-4",
    "providers": {
      "slug": "featherless",
      "name": "Featherless"
    }
  }
]
```

### 4. Get Model Statistics

**Get overall statistics:**

```bash
curl -X GET "http://localhost:8000/models/stats" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq
```

**Response:**
```json
{
  "total_models": 1234,
  "active_models": 1200,
  "inactive_models": 34,
  "by_health_status": {
    "healthy": 1100,
    "degraded": 80,
    "down": 20,
    "unknown": 34
  },
  "by_modality": {
    "text->text": 1150,
    "text->image": 50,
    "text->audio": 20,
    "multimodal": 14
  },
  "by_provider": {
    "openrouter": 250,
    "deepinfra": 150,
    "cerebras": 3,
    ...
  }
}
```

### 5. Filter by Health Status

**Get only healthy models:**

```bash
curl -X GET "http://localhost:8000/models/health/healthy?limit=100" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq
```

### 6. Get Specific Model

**Get model by database ID:**

```bash
curl -X GET "http://localhost:8000/models/1" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq
```

**Get model by provider + model ID:**

```bash
curl -X GET "http://localhost:8000/models/provider/openrouter/model/openai%2Fgpt-4" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq
```

---

## 🧪 Testing Failover Queries

**Test the failover database functions directly:**

### 1. Find All Providers for a Model

```bash
# Create a test script
cat > test_failover_query.py << 'EOF'
from src.db.failover_db import get_providers_for_model

# Find all providers that have GPT-4
providers = get_providers_for_model("gpt-4", active_only=True)

print(f"\n✅ Found {len(providers)} providers for GPT-4:\n")
for p in providers:
    print(f"  {p['provider_slug']:20} | "
          f"Health: {p['provider_health_status']:10} | "
          f"Latency: {str(p['provider_response_time_ms']) + 'ms':8} | "
          f"Price: ${p['pricing_prompt']:.6f}/1M tokens")
EOF

python test_failover_query.py
```

**Expected Output:**
```
✅ Found 3 providers for GPT-4:

  openrouter           | Health: healthy    | Latency: 150ms   | Price: $0.000030/1M tokens
  featherless          | Health: healthy    | Latency: 200ms   | Price: $0.000028/1M tokens
  portkey              | Health: degraded   | Latency: 500ms   | Price: $0.000035/1M tokens
```

### 2. Test Model Alias Resolution

```bash
cat > test_aliases.py << 'EOF'
from src.db.failover_db import get_provider_model_id

# Test different providers' IDs for the same model
openrouter_id = get_provider_model_id("gpt-4", "openrouter")
featherless_id = get_provider_model_id("gpt-4", "featherless")

print(f"Canonical: gpt-4")
print(f"  → OpenRouter:   {openrouter_id}")
print(f"  → Featherless:  {featherless_id}")
EOF

python test_aliases.py
```

**Expected Output:**
```
Canonical: gpt-4
  → OpenRouter:   openai/gpt-4
  → Featherless:  gpt-4
```

---

## 🔍 Monitoring & Debugging

### 1. Check Sync Progress

**Watch logs while syncing:**

```bash
# In terminal 1: Start server with verbose logging
export LOG_LEVEL=DEBUG
python src/main.py

# In terminal 2: Trigger sync
curl -X POST "http://localhost:8000/admin/model-sync/provider/openrouter" \
  -H "Authorization: Bearer $ADMIN_KEY"
```

### 2. Verify Database Contents

**Direct database query:**

```bash
cat > check_database.py << 'EOF'
from src.config.supabase_config import get_supabase_client

supabase = get_supabase_client()

# Count providers
providers_result = supabase.table("providers").select("count").execute()
print(f"✅ Providers: {len(providers_result.data)}")

# Count models
models_result = supabase.table("models").select("count").execute()
print(f"✅ Models: {len(models_result.data)}")

# Count by provider
by_provider = supabase.rpc("get_models_by_provider_count").execute()
for row in by_provider.data[:5]:
    print(f"  - {row['provider']}: {row['count']} models")
EOF

python check_database.py
```

### 3. Check for Duplicate Models

**Find models available on multiple providers:**

```bash
cat > find_duplicates.py << 'EOF'
from collections import Counter
from src.config.supabase_config import get_supabase_client

supabase = get_supabase_client()
result = supabase.table("models").select("model_id").execute()

model_ids = [row["model_id"] for row in result.data]
counts = Counter(model_ids)

print("\n🔍 Models available on multiple providers:\n")
for model_id, count in counts.most_common(10):
    if count > 1:
        print(f"  {model_id:40} → {count} providers")
EOF

python find_duplicates.py
```

**Expected Output:**
```
🔍 Models available on multiple providers:

  gpt-4                                    → 5 providers
  gpt-3.5-turbo                           → 4 providers
  claude-3-5-sonnet-20241022              → 3 providers
  llama-3-70b-instruct                    → 6 providers
```

---

## 📝 Common Testing Workflows

### Workflow 1: Initial Setup

```bash
# 1. List available providers
curl -X GET "http://localhost:8000/admin/model-sync/providers" \
  -H "Authorization: Bearer $ADMIN_KEY"

# 2. Dry run to test (no database writes)
curl -X POST "http://localhost:8000/admin/model-sync/provider/cerebras?dry_run=true" \
  -H "Authorization: Bearer $ADMIN_KEY"

# 3. Sync a small provider first (Cerebras has ~3 models)
curl -X POST "http://localhost:8000/admin/model-sync/provider/cerebras" \
  -H "Authorization: Bearer $ADMIN_KEY"

# 4. Verify models were inserted
curl -X GET "http://localhost:8000/models/?provider_slug=cerebras" \
  -H "Authorization: Bearer $ADMIN_KEY"

# 5. Sync all providers
curl -X POST "http://localhost:8000/admin/model-sync/all" \
  -H "Authorization: Bearer $ADMIN_KEY"
```

### Workflow 2: Test Failover

```bash
# 1. Sync multiple providers
curl -X POST "http://localhost:8000/admin/model-sync/all?providers=openrouter&providers=featherless&providers=portkey" \
  -H "Authorization: Bearer $ADMIN_KEY"

# 2. Search for a model
curl -X GET "http://localhost:8000/models/search?q=gpt-4" \
  -H "Authorization: Bearer $ADMIN_KEY"

# 3. Test failover query
python -c "
from src.db.failover_db import get_providers_for_model
providers = get_providers_for_model('gpt-4')
print(f'GPT-4 available on {len(providers)} providers')
"

# 4. Verify aliases
python -c "
from src.db.failover_db import get_provider_model_id
print('OpenRouter:', get_provider_model_id('gpt-4', 'openrouter'))
print('Featherless:', get_provider_model_id('gpt-4', 'featherless'))
"
```

### Workflow 3: Production Sync

```bash
# 1. Sync all providers (takes 2-5 minutes)
time curl -X POST "http://localhost:8000/admin/model-sync/all" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq

# 2. Get statistics
curl -X GET "http://localhost:8000/models/stats" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq

# 3. Verify health
curl -X GET "http://localhost:8000/models/health/healthy" \
  -H "Authorization: Bearer $ADMIN_KEY" | jq -r 'length'

# 4. Test failover
python scripts/test_failover_database.py
```

---

## 🐛 Troubleshooting

### Issue 1: "Connection Refused"

**Problem:** Cannot connect to database

**Solution:**
```bash
# Check if DATABASE_URL / SUPABASE_URL is set
echo $SUPABASE_URL

# Test direct connection
python -c "from src.config.supabase_config import get_supabase_client; print(get_supabase_client())"
```

### Issue 2: "Invalid admin API key"

**Problem:** Authentication failing

**Solution:**
```bash
# Make sure ADMIN_API_KEY is in .env
grep ADMIN_API_KEY .env

# Or set temporarily
export ADMIN_KEY="your-key"
```

### Issue 3: "Provider not found"

**Problem:** Trying to sync invalid provider

**Solution:**
```bash
# Get list of valid providers
curl -X GET "http://localhost:8000/admin/model-sync/providers" \
  -H "Authorization: Bearer $ADMIN_KEY"
```

### Issue 4: Empty Model List

**Problem:** Models query returns []

**Solution:**
```bash
# 1. Check if sync ran successfully
curl -X GET "http://localhost:8000/models/stats" -H "Authorization: Bearer $ADMIN_KEY"

# 2. If total_models is 0, run sync
curl -X POST "http://localhost:8000/admin/model-sync/all" -H "Authorization: Bearer $ADMIN_KEY"
```

---

## ✅ Success Criteria

Your API is working correctly if:

1. ✅ `GET /admin/model-sync/providers` returns 20+ providers
2. ✅ `POST /admin/model-sync/provider/cerebras` returns `"success": true`
3. ✅ `GET /models/stats` shows `total_models > 0`
4. ✅ `GET /models/search?q=gpt-4` returns multiple results
5. ✅ Failover query finds multiple providers for same model
6. ✅ `python scripts/test_failover_database.py` passes all tests

---

## 🚀 Next Steps

Once your database is populated:

1. **Integrate failover** - Modify chat endpoint to use `route_with_failover()`
2. **Monitor health** - Track success/error rates in `model_health_tracking`
3. **Schedule syncs** - Set up cron job or background task to sync every 6-12 hours
4. **Add dashboard** - Build admin UI to visualize provider health and model availability

---

## 📚 Related Files

- API Routes: `src/routes/model_sync.py`, `src/routes/models_catalog_management.py`
- Database Layer: `src/db/failover_db.py`, `src/db/models_catalog_db.py`
- Sync Service: `src/services/model_catalog_sync.py`
- Test Suite: `scripts/test_failover_database.py`
- Guide: `FAILOVER_TESTING_GUIDE.md`
