# Model Search & Filtering Guide

Quick reference for searching and filtering models in the Gatewayz catalog.

## Quick Start

```bash
# Search for any model
GET /catalog/models-db/search?q=gpt 4

# List models with filters
GET /catalog/models-db/?provider_slug=openai&health_status=healthy

# Get models by provider
GET /catalog/models-db/provider/anthropic
```

---

## 1. Search Models (Flexible Text Search)

**Endpoint**: `GET /catalog/models-db/search`

### Parameters
- `q` (required) - Search query (searches model name, ID, and description)
- `provider_id` (optional) - Filter by provider ID

### Flexible Matching
The search automatically handles different naming variations:

| You Search | Matches |
|------------|---------|
| `gpt 4` | gpt-4, gpt4, gpt_4, gpt-4-turbo, gpt-4o |
| `claude 3` | claude-3, claude3, claude-3-opus, claude-3.5-sonnet |
| `llama-3` | llama 3, llama3, llama-3-70b |

### Examples
```bash
# Find all GPT-4 variants
curl "localhost:8000/catalog/models-db/search?q=gpt%204"

# Find Claude models
curl "localhost:8000/catalog/models-db/search?q=claude"

# Search with provider filter
curl "localhost:8000/catalog/models-db/search?q=turbo&provider_id=1"
```

---

## 2. List & Filter Models

**Endpoint**: `GET /catalog/models-db/`

### Available Filters

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `provider_slug` | string | Provider slug | `openai`, `anthropic` |
| `provider_id` | integer | Provider ID | `1`, `2` |
| `is_active_only` | boolean | Only active models | `true` (default), `false` |
| `health_status` | string | Health status | `healthy`, `degraded`, `down`, `unknown` |
| `modality` | string | Model modality | `text->text`, `text->image` |
| `limit` | integer | Results per page (1-1000) | `50` (default: 100) |
| `offset` | integer | Pagination offset | `0`, `100`, `200` |

### Examples
```bash
# Get healthy OpenAI models
curl "localhost:8000/catalog/models-db/?provider_slug=openai&health_status=healthy"

# Get all vision models
curl "localhost:8000/catalog/models-db/?modality=text->image"

# Get first 20 active models
curl "localhost:8000/catalog/models-db/?is_active_only=true&limit=20"

# Paginate (skip first 100, get next 50)
curl "localhost:8000/catalog/models-db/?limit=50&offset=100"

# Combine multiple filters
curl "localhost:8000/catalog/models-db/?provider_slug=anthropic&health_status=healthy&limit=10"
```

---

## 3. Quick Filter Endpoints

### By Provider
```bash
GET /catalog/models-db/provider/{provider_slug}

# Examples
GET /catalog/models-db/provider/openai
GET /catalog/models-db/provider/anthropic
GET /catalog/models-db/provider/together-ai
```

### By Health Status
```bash
GET /catalog/models-db/health/{status}

# Examples
GET /catalog/models-db/health/healthy
GET /catalog/models-db/health/degraded
GET /catalog/models-db/health/down
```

### Get Statistics
```bash
# Overall stats
GET /catalog/models-db/stats

# Stats for specific provider
GET /catalog/models-db/stats?provider_id=1
```

**Returns:**
```json
{
  "total": 500,
  "active": 450,
  "inactive": 50,
  "by_health_status": {
    "healthy": 400,
    "degraded": 30,
    "down": 20,
    "unknown": 50
  },
  "by_modality": {
    "text->text": 400,
    "text->image": 80,
    "text+image->text": 20
  }
}
```

---

## 4. Response Format

All endpoints return models with this structure:

```json
{
  "id": 1,
  "model_id": "openai/gpt-4-turbo",
  "model_name": "GPT-4 Turbo",
  "provider_model_id": "gpt-4-turbo-preview",
  "description": "Most capable GPT-4 model...",
  "context_length": 128000,
  "modality": "text->text",

  "pricing_prompt": 0.00001,
  "pricing_completion": 0.00003,

  "supports_streaming": true,
  "supports_function_calling": true,
  "supports_vision": true,

  "health_status": "healthy",
  "average_response_time_ms": 1200,
  "success_rate": 99.5,

  "is_active": true,
  "providers": {
    "id": 1,
    "name": "OpenAI",
    "slug": "openai",
    "website": "https://openai.com"
  }
}
```

---

## 5. Common Use Cases

### Find GPT-4 models across all providers
```bash
GET /catalog/models-db/search?q=gpt-4
```

### Get all healthy, active models
```bash
GET /catalog/models-db/?is_active_only=true&health_status=healthy
```

### Find vision-capable models
```bash
GET /catalog/models-db/search?q=vision
# or search for modality that supports vision
```

### Get fastest models (with good health)
```bash
# Get healthy models, then sort by response time in your app
GET /catalog/models-db/?health_status=healthy
```

### Find cheapest models
```bash
# Get all models, sort by pricing_prompt in your app
GET /catalog/models-db/?is_active_only=true&limit=1000
```

---

## 6. Performance Notes

- All queries are **indexed** for fast performance
- Search handles **500+ models** efficiently
- Pagination recommended for large result sets
- Use `provider_slug` instead of `provider_id` when possible (more readable)

---

## 7. Tips

1. **Search is flexible** - Don't worry about exact spacing or hyphens
2. **Combine filters** - Stack multiple parameters for precise results
3. **Use pagination** - For large datasets, use `limit` and `offset`
4. **Check health** - Filter by `health_status=healthy` for reliable models
5. **Provider slugs** - Use readable slugs like `openai` instead of IDs

---

## Need Help?

- **Database Schema**: See `supabase/migrations/20251216024941_create_model_catalog_tables.sql`
- **Search Implementation**: See `src/db/models_catalog_db.py:393`
- **API Routes**: See `src/routes/models_catalog_management.py`
