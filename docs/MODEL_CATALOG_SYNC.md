# Model Catalog Sync

## Overview

Automatically fetches and syncs AI models from 20+ providers into a relational database, mapping each model to its provider with full metadata including API names, pricing, and capabilities.

**Key Features:**
- Syncs models from 20+ AI providers (OpenRouter, DeepInfra, Groq, etc.)
- Maps models to providers with foreign key relationships
- Stores pricing, context length, capabilities, and health status
- Provides **API names** (`provider_model_id`) for making actual API calls
- Admin-only access with `ADMIN_API_KEY` authentication

## Database Schema

### Tables

**`providers`**
- Stores provider metadata (name, slug, base_url, API key env var, etc.)
- Primary key: `id`
- Unique constraint: `slug`

**`models`**
- Stores all models with provider relationships
- Primary key: `id`
- Foreign key: `provider_id` → `providers.id`
- Unique constraint: `(provider_id, provider_model_id)`

### Key Fields

| Field | Description | Example |
|-------|-------------|---------|
| `models.provider_id` | Foreign key to providers table | `1` |
| `models.model_name` | Human-readable display name | `"OpenAI: GPT-4 Turbo"` |
| `models.provider_model_id` | **API name for requests** | `"gpt-4-turbo"` |
| `models.pricing_prompt` | Cost per input token | `0.00001` |
| `models.pricing_completion` | Cost per output token | `0.00003` |
| `models.context_length` | Maximum token window | `128000` |
| `models.modality` | Model type | `"text->text"` |
| `models.supports_streaming` | Streaming capability | `true` |
| `models.is_active` | Whether model is enabled | `true` |

## Authentication

**All API endpoints require admin authentication.**

Set the `ADMIN_API_KEY` in your `.env` file:

```bash
# Generate a secure admin key
ADMIN_API_KEY=$(openssl rand -hex 32)

# Or set manually
ADMIN_API_KEY=your-secret-admin-key-here
```

This key is required for all model sync operations and protects your database from unauthorized modifications.

## Usage

### Method 1: Python Script (Recommended)

The script bypasses API authentication and directly accesses the database.

```bash
# Sync a single provider
python3 scripts/sync_models.py --providers openrouter

# Sync multiple specific providers
python3 scripts/sync_models.py --providers openrouter deepinfra groq

# Sync all providers (20+)
python3 scripts/sync_models.py

# Dry run (test without writing to DB)
python3 scripts/sync_models.py --dry-run

# Verbose logging
python3 scripts/sync_models.py --verbose
```

### Method 2: API Endpoints

**Authentication Required:** Include `Authorization: Bearer <ADMIN_API_KEY>` header.

```bash
# Set your admin API key
export ADMIN_API_KEY="your-admin-key"

# Sync a single provider
curl -X POST "http://localhost:8000/admin/model-sync/provider/openrouter" \
  -H "Authorization: Bearer $ADMIN_API_KEY"

# Sync all providers
curl -X POST "http://localhost:8000/admin/model-sync/all" \
  -H "Authorization: Bearer $ADMIN_API_KEY"

# Dry run (fetch but don't write to DB)
curl -X POST "http://localhost:8000/admin/model-sync/provider/openrouter?dry_run=true" \
  -H "Authorization: Bearer $ADMIN_API_KEY"

# List available providers
curl "http://localhost:8000/admin/model-sync/providers" \
  -H "Authorization: Bearer $ADMIN_API_KEY"

# Get sync status and statistics
curl "http://localhost:8000/admin/model-sync/status" \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

**Response Example:**
```json
{
  "success": true,
  "message": "Synced 342 models from openrouter. Fetched: 342, Transformed: 342, Skipped: 0",
  "details": {
    "provider": "openrouter",
    "provider_id": 1,
    "models_fetched": 342,
    "models_transformed": 342,
    "models_synced": 342
  }
}
```

## Query Models

Once synced, you can query the database directly:

### Get All Models with Provider Info

```sql
SELECT
    p.slug as provider,
    m.model_name,
    m.provider_model_id as api_name,
    m.context_length,
    ROUND(m.pricing_prompt::numeric * 1000000, 2) as price_per_1m_input_tokens,
    ROUND(m.pricing_completion::numeric * 1000000, 2) as price_per_1m_output_tokens
FROM models m
JOIN providers p ON m.provider_id = p.id
WHERE p.slug = 'openrouter'
  AND m.is_active = true
ORDER BY m.model_name
LIMIT 10;
```

### Find Specific Models

```sql
-- Search by name
SELECT model_name, provider_model_id as api_name
FROM models
WHERE model_name ILIKE '%gpt-4%'
  AND is_active = true;

-- Get models by capability
SELECT model_name, provider_model_id as api_name
FROM models
WHERE supports_vision = true
  AND is_active = true;
```

### Get Provider Statistics

```sql
SELECT
    p.name as provider,
    COUNT(m.id) as total_models,
    COUNT(CASE WHEN m.is_active THEN 1 END) as active_models
FROM providers p
LEFT JOIN models m ON p.id = m.provider_id
GROUP BY p.id, p.name
ORDER BY total_models DESC;
```

### Using the API Name

The `provider_model_id` column contains the **actual API name** to use when making requests:

```python
# Example: Using the synced data in your code
from src.db.models_catalog_db import get_models_by_provider_slug

# Get OpenRouter models
models = get_models_by_provider_slug("openrouter")

# Use the API name from provider_model_id
for model in models:
    api_name = model["provider_model_id"]  # e.g., "openai/gpt-4-turbo"
    # Make API call with this name
    response = client.chat.completions.create(model=api_name, ...)
```

## Supported Providers

**Text Models:** OpenRouter, DeepInfra, Featherless, Groq, Fireworks, Together AI, HuggingFace, XAI, Cerebras, Google Vertex, Nebius, Novita, Helicone, AiHubMix, Anannas, AIMO, Near AI, Chutes, Alibaba

**Image Models:** Fal.ai, Vercel AI Gateway

**Total:** 20+ providers with automatic model discovery and sync

## How It Works

1. **Fetch:** API requests to each provider's model endpoint
2. **Transform:** Normalize provider-specific data to unified schema
3. **Upsert:** Insert or update models in database (keyed by `provider_id` + `provider_model_id`)
4. **Result:** Complete catalog with pricing, capabilities, and API names

## Security

- **Admin-only access:** All sync endpoints require `ADMIN_API_KEY`
- **Environment variable:** Store admin key in `.env`, never commit to git
- **Constant-time comparison:** Prevents timing attacks on key validation
- **Separate from user API keys:** Admin key is distinct from user authentication

## Best Practices

- **Run syncs periodically:** Schedule via cron or GitHub Actions to keep models updated
- **Use dry-run first:** Test sync with `--dry-run` flag before writing to production DB
- **Monitor sync status:** Use `/admin/model-sync/status` to check catalog health
- **Filter by provider:** Sync specific providers during development to reduce API calls
- **Check logs:** Review verbose output to catch transformation errors

## Troubleshooting

### Sync fails with 401 Unauthorized

Ensure `ADMIN_API_KEY` is set in `.env` and matches the header value:

```bash
# Check if key is set
echo $ADMIN_API_KEY

# Regenerate if needed
openssl rand -hex 32
```

### No models synced (0 models)

Provider API may be down or API key missing. Check:

1. Provider API key environment variable (e.g., `OPENROUTER_API_KEY`)
2. Network connectivity to provider
3. Verbose logs with `--verbose` flag

### Models not appearing in queries

Check if models are marked as active:

```sql
SELECT COUNT(*) FROM models WHERE is_active = false;
```

Activate models if needed:

```sql
UPDATE models SET is_active = true WHERE provider_id = 1;
```
