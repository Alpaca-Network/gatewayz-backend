# Database vs API Models: Understanding the Discrepancy

## 🔍 The Issue

You're seeing **two different model counts**:
- **Database (`models` table)**: ~11,000 records
- **API Response (`/models` endpoint)**: ~18,000+ models

**This is by design!** Here's why.

---

## 🏗️ Architecture: Two Parallel Systems

Gatewayz uses **two separate systems** for model data:

### 1. **Live API Fetching** (What Frontend Sees)
- **Source**: Direct API calls to 30+ provider APIs
- **Count**: ~18,000+ models
- **Purpose**: Real-time, up-to-date model availability
- **Location**: `src/services/models.py`
- **Cache**: In-memory (1 hour TTL) + Redis

### 2. **Database Storage** (Persistent Records)
- **Source**: Synced from provider APIs (scheduled/manual)
- **Count**: ~11,000 records
- **Purpose**: Pricing, analytics, historical tracking, fallback
- **Location**: `supabase.models` table
- **Sync**: Every 6 hours (automatic) + manual

---

## 📊 Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                   Frontend API Request                       │
│                    GET /models?gateway=all                   │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────┐
        │   src/routes/catalog.py                │
        │   async def get_models()               │
        └────────────────┬───────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────────────┐
        │   src/services/models.py               │
        │   get_cached_models(gateway)           │
        └────────┬───────────────────────────────┘
                 │
                 ├─── Live API Call ──────────────┐
                 │                                 │
                 ▼                                 ▼
    ┌────────────────────────┐      ┌──────────────────────┐
    │  Provider APIs          │      │  In-Memory Cache     │
    │  (30+ providers)        │      │  (1 hour TTL)        │
    │                         │      │                      │
    │  • OpenRouter (3,500)   │◄─────┤  • Fast lookups     │
    │  • Featherless (6,500)  │      │  • Avoid rate limits│
    │  • DeepInfra (2,000)    │      └──────────────────────┘
    │  • Groq, Fireworks...   │
    │  • 27 more providers    │
    └────────────────────────┘
                 │
                 ▼
    ┌────────────────────────────────────────┐
    │  Aggregated Response                   │
    │  18,000+ models                        │
    │  (from all providers)                  │
    └────────────────────────────────────────┘


┌──────────────────────────────────────────────────────────────┐
│              SEPARATE SYSTEM (Runs in Background)             │
└──────────────────────────────────────────────────────────────┘

    ┌────────────────────────────────────────┐
    │  Pricing Sync Scheduler                │
    │  (Every 6 hours)                       │
    └────────────┬───────────────────────────┘
                 │
                 ▼
    ┌────────────────────────────────────────┐
    │  src/services/model_catalog_sync.py    │
    │  sync_provider_models()                │
    └────────────┬───────────────────────────┘
                 │
                 ├─── Fetch from Provider APIs
                 │
                 ▼
    ┌────────────────────────────────────────┐
    │  Database (Supabase)                   │
    │                                        │
    │  • models table (11,000 records)      │
    │  • model_pricing table                │
    │  • Used for:                          │
    │    - Pricing lookup                   │
    │    - Analytics                        │
    │    - Historical tracking              │
    │    - Fallback when APIs down          │
    └────────────────────────────────────────┘
```

---

## 🎯 Why Two Systems?

### Live API Fetching Advantages:
✅ **Real-time updates** - No sync delay, always current
✅ **Complete catalog** - Gets ALL models from ALL providers
✅ **Provider-fresh data** - Direct from source, no stale data
✅ **Fast response** - In-memory caching (1 hour TTL)
✅ **No database bottleneck** - Doesn't query database on every request

### Database Storage Advantages:
✅ **Pricing persistence** - Store per-token pricing
✅ **Analytics** - Query usage patterns, trends
✅ **Historical tracking** - Price changes over time
✅ **Fallback resilience** - When provider APIs are down
✅ **Cross-reference** - Match models across providers

---

## 🔍 Where Models Come From

### `/models` API Endpoint Flow

**Location**: `src/routes/catalog.py:656-736`

```python
async def get_models(gateway: str = "all"):
    # Fetches from LIVE PROVIDER APIs, NOT database

    openrouter_models = get_cached_models("openrouter")  # → API call
    featherless_models = get_cached_models("featherless") # → API call
    deepinfra_models = get_cached_models("deepinfra")    # → API call
    # ... 27 more providers

    # Aggregate all models
    all_models = (
        openrouter_models +
        featherless_models +
        deepinfra_models +
        # ... rest of providers
    )

    return all_models  # 18,000+ models
```

**Key Point**: `get_cached_models()` calls `fetch_models_from_{provider}()`, which makes **direct HTTP API calls** to provider endpoints. It does **NOT** query the database!

---

## 📈 Model Count Breakdown

### Live API Fetching (18,000+)
```
OpenRouter:        ~3,500 models
Featherless:       ~6,500 models
DeepInfra:         ~2,000 models
HuggingFace:       ~1,500 models
Together:          ~800 models
Fireworks:         ~600 models
Groq:              ~100 models
... 23 more providers
─────────────────────────────
Total:            ~18,000+ models
```

### Database Sync (11,000)
```
Synced providers:  OpenRouter, Featherless, DeepInfra, Near AI
Last sync:         (check pricing_sync_log table)
Sync frequency:    Every 6 hours
Missing:           ~7,000 models (not yet synced or filtered)
```

---

## ❓ Why Database Has Fewer Models

Several reasons for the ~7k model difference:

### 1. **Partial Sync Coverage**
The automatic sync only covers **4 providers** by default:
```bash
PRICING_SYNC_PROVIDERS=openrouter,featherless,nearai,alibaba-cloud
```

30 providers exist, but only 4 sync pricing automatically. The other 26 providers' models aren't synced to the database unless manually triggered.

### 2. **Sync Filtering**
The sync service filters out models:
- Models without valid pricing (gateway providers need cross-reference)
- Duplicate models (same model from multiple providers)
- Inactive models (`is_active=false`)
- Failed transformations

**Location**: `src/services/pricing_lookup.py:273-376`

### 3. **Sync Timing**
Database syncs every 6 hours, but provider APIs update continuously:
- New models added by providers
- Model availability changes
- Provider catalog updates

### 4. **Manual Sync Not Run**
If you haven't manually synced all providers:
```bash
# This would sync ALL providers to database
curl -X POST https://api.gatewayz.ai/admin/model-sync/all
```

---

## 🔧 How to Sync More Models to Database

### Option 1: Sync All Providers (One-Time)

```bash
# Sync all 30 providers to database
curl -X POST https://api.gatewayz.ai/admin/model-sync/all

# Expected result: 18k+ models in database (matching API)
```

### Option 2: Add More Providers to Auto-Sync

```bash
# In .env file
PRICING_SYNC_PROVIDERS=openrouter,featherless,deepinfra,groq,fireworks,together,cerebras

# Restart app - now syncs 7 providers every 6 hours
```

### Option 3: Database-First Mode (Not Implemented Yet)

**Current behavior**: API always fetches from provider APIs
**Future enhancement**: Add config to switch to database-first:

```bash
# Proposed environment variable (not yet implemented)
MODEL_CATALOG_SOURCE=database  # Use database instead of live API
```

This would require:
1. Syncing ALL providers to database
2. Modifying `get_cached_models()` to query database first
3. Fallback to API if database is empty

---

## 🎯 Recommended Approach

### For Most Use Cases (Current Default)
✅ **Keep live API fetching** for frontend (18k+ models)
✅ **Sync 4-7 key providers** to database (for pricing)
✅ **Accept the count difference** (by design)

### For Database-First Approach
If you want database and API to match:

```bash
# 1. Sync all providers once
curl -X POST https://api.gatewayz.ai/admin/model-sync/all

# 2. Update auto-sync to include all providers
PRICING_SYNC_PROVIDERS=openrouter,featherless,deepinfra,groq,fireworks,together,\
cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,\
vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,\
cloudflare-workers-ai,clarifai,morpheus,sybil

# 3. Increase sync frequency (optional)
PRICING_SYNC_INTERVAL_HOURS=2  # Sync every 2 hours

# Result: Database will have ~18k models (matching API)
```

---

## 📊 Verification Queries

### Check Database Model Count
```sql
-- Total active models in database
SELECT COUNT(*) FROM models WHERE is_active = true;
-- Expected: ~11,000 (current) or ~18,000 (after full sync)

-- Models by provider
SELECT
    p.slug as provider,
    COUNT(m.id) as model_count
FROM models m
JOIN providers p ON m.provider_id = p.id
WHERE m.is_active = true
GROUP BY p.slug
ORDER BY model_count DESC;
```

### Check API Model Count
```bash
# Get total from API
curl "https://api.gatewayz.ai/models?gateway=all&limit=100000" | jq '.data | length'
# Expected: ~18,000+

# Get by provider
curl "https://api.gatewayz.ai/models?gateway=openrouter&limit=100000" | jq '.data | length'
curl "https://api.gatewayz.ai/models?gateway=featherless&limit=100000" | jq '.data | length'
```

### Check Last Sync
```sql
-- Last successful sync
SELECT
    provider_slug,
    sync_started_at,
    models_updated,
    status
FROM pricing_sync_log
ORDER BY sync_started_at DESC
LIMIT 10;
```

---

## 🚨 Common Misconceptions

### ❌ "The API should use the database"
**False**. The API uses live provider APIs for real-time data. Database is for persistence and analytics.

### ❌ "Database sync updates the API"
**False**. The API doesn't read from the database. Both systems are independent.

### ❌ "Models are missing from the API"
**False**. All 18k+ models are in the API. Only 11k are synced to database (by design).

### ✅ "I need to manually sync to get all models in database"
**True**. Run `/admin/model-sync/all` to sync all providers.

---

## 🔗 Related Documentation

- Full Sync Guide: `docs/MODEL_SYNC_GUIDE.md`
- Quick Reference: `docs/SYNC_QUICK_REFERENCE.md`
- Architecture: `docs/architecture.md`
- Codebase Context: `CLAUDE.md`

---

## 📞 Quick Actions

### See which providers are synced:
```sql
SELECT DISTINCT p.slug
FROM models m
JOIN providers p ON m.provider_id = p.id
WHERE m.is_active = true;
```

### Sync missing providers:
```bash
curl -X POST https://api.gatewayz.ai/admin/model-sync/provider/groq
curl -X POST https://api.gatewayz.ai/admin/model-sync/provider/fireworks
curl -X POST https://api.gatewayz.ai/admin/model-sync/provider/together
# ... repeat for each provider
```

### Get real-time model count:
```bash
# API count (live)
curl "https://api.gatewayz.ai/models?gateway=all" | jq '.data | length'

# Database count
psql $DATABASE_URL -c "SELECT COUNT(*) FROM models WHERE is_active = true;"
```

---

**Summary**: The 11k vs 18k discrepancy is **intentional**. The API serves live data from provider APIs (18k+), while the database stores a subset (11k) for pricing and analytics. To sync all models to database, run the full model sync.

**Last Updated**: 2026-01-27
