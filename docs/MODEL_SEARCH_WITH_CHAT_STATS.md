# Model Search with Chat Completion Statistics

This feature combines model searching with chat completion request tracking to provide comprehensive insights into model usage, performance, and cost across different providers.

## Overview

The model search with chat statistics functionality allows you to:

1. **Search for models** with flexible name matching (handles variations like "gpt 4", "gpt-4", "gpt4")
2. **Filter by provider** to find specific models from specific providers
3. **View aggregated statistics** including:
   - Total requests and success rates
   - Token usage (input, output, total)
   - Processing performance metrics
   - Cost analysis based on pricing

## Key Features

### Flexible Search

The search handles various naming conventions automatically:

- **"gpt 4"** matches: `gpt-4`, `gpt4`, `gpt-4o`, `gpt-4-turbo`, `gpt-4-0125-preview`
- **"claude"** matches: `claude-3-opus`, `claude-3-sonnet`, `claude-2`, `claude-instant`
- **"llama 3"** matches: `llama-3`, `llama3`, `llama-3-70b`, `llama-3-8b-instruct`

The search looks across:
- Model name
- Model identifier
- Provider model ID
- Description

### Provider Filtering

You can search across all providers or narrow down to a specific one:

- **All providers**: Search finds all matching models regardless of provider
- **Specific provider**: Filter by provider slug (e.g., `openrouter`) or name (e.g., `OpenRouter`)

### Comprehensive Statistics

For each model found, you get:

#### Model Information
- Model name and identifier
- Provider details
- Pricing (prompt and completion)
- Context length
- Modality (text, multimodal, etc.)
- Capabilities (streaming, function calling, vision)
- Health status

#### Chat Completion Statistics
- **Request Counts**: Total, completed, failed
- **Success Rate**: Percentage of successful completions
- **Token Usage**: Total tokens, average input/output tokens
- **Performance**: Average and total processing time
- **Recent Activity**: Timestamp of last request

## Usage

### API Endpoint

**Endpoint**: `GET /models/catalog/search`

**Parameters**:
- `q` (required): Search query string
- `provider` (optional): Provider slug or name to filter
- `limit` (optional): Max results (default: 100, max: 500)

**Examples**:

```bash
# Search all GPT-4 models across all providers
curl "http://localhost:8000/models/catalog/search?q=gpt%204"

# Search GPT-4 on OpenRouter only
curl "http://localhost:8000/models/catalog/search?q=gpt%204&provider=openrouter"

# Search Claude models
curl "http://localhost:8000/models/catalog/search?q=claude"

# Search Llama models on Together AI
curl "http://localhost:8000/models/catalog/search?q=llama&provider=together"
```

### Python Function

```python
from src.db.chat_completion_requests import search_models_with_chat_stats

# Search all GPT-4 models
results = search_models_with_chat_stats(
    query="gpt 4",
    provider_name=None,
    limit=100
)

# Search GPT-4 on OpenRouter only
results = search_models_with_chat_stats(
    query="gpt 4",
    provider_name="openrouter",
    limit=100
)

# Process results
for model in results:
    print(f"Model: {model['model_name']}")
    print(f"Provider: {model['provider']['name']}")
    print(f"Total Requests: {model['chat_stats']['total_requests']}")
    print(f"Success Rate: {model['chat_stats']['success_rate']}%")
    print(f"Avg Processing Time: {model['chat_stats']['avg_processing_time_ms']}ms")
```

### Test Script

Use the included test script to explore the functionality:

```bash
# Basic search
python scripts/test_model_search_with_stats.py --query "gpt 4"

# Search with provider filter
python scripts/test_model_search_with_stats.py --query "gpt 4" --provider openrouter

# Limit results
python scripts/test_model_search_with_stats.py --query "claude" --limit 10

# JSON output
python scripts/test_model_search_with_stats.py --query "llama" --json

# Verbose logging
python scripts/test_model_search_with_stats.py --query "gpt 4" --verbose
```

## Response Format

```json
{
  "success": true,
  "query": "gpt 4",
  "provider_filter": "openrouter",
  "total_results": 5,
  "models": [
    {
      "model_id": 123,
      "model_name": "GPT-4",
      "model_identifier": "gpt-4",
      "provider_model_id": "openai/gpt-4",
      "provider": {
        "id": 1,
        "name": "OpenRouter",
        "slug": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        ...
      },
      "description": "Most capable GPT-4 model",
      "pricing_prompt": 0.00003,
      "pricing_completion": 0.00006,
      "context_length": 8192,
      "modality": "text->text",
      "health_status": "healthy",
      "supports_streaming": true,
      "supports_function_calling": true,
      "supports_vision": false,
      "is_active": true,
      "chat_stats": {
        "total_requests": 1523,
        "total_tokens": 1847392,
        "avg_input_tokens": 842.5,
        "avg_output_tokens": 371.2,
        "avg_processing_time_ms": 3421.7,
        "total_processing_time_ms": 5211229,
        "success_rate": 98.75,
        "completed_requests": 1504,
        "failed_requests": 19,
        "last_request_at": "2025-12-20T08:00:00Z"
      }
    },
    ...
  ],
  "timestamp": "2025-12-20T08:00:00Z"
}
```

## Use Cases

### 1. Compare Model Performance Across Providers

Search for the same model across different providers to compare performance:

```python
results = search_models_with_chat_stats(query="gpt-4")

for model in results:
    provider = model['provider']['name']
    stats = model['chat_stats']
    print(f"{provider}: {stats['avg_processing_time_ms']}ms avg, {stats['success_rate']}% success")
```

### 2. Analyze Model Usage Patterns

Find your most-used models and their statistics:

```python
# Results are sorted by total_requests (descending)
results = search_models_with_chat_stats(query="gpt")

most_used = results[0]
print(f"Most used: {most_used['model_name']}")
print(f"Total requests: {most_used['chat_stats']['total_requests']}")
```

### 3. Cost Analysis

Calculate costs based on usage:

```python
results = search_models_with_chat_stats(query="gpt 4", provider_name="openrouter")

for model in results:
    stats = model['chat_stats']
    prompt_cost = stats['avg_input_tokens'] * model['pricing_prompt']
    completion_cost = stats['avg_output_tokens'] * model['pricing_completion']
    total_cost_per_request = prompt_cost + completion_cost

    print(f"Model: {model['model_name']}")
    print(f"Avg cost per request: ${total_cost_per_request:.4f}")
    print(f"Total spent: ${total_cost_per_request * stats['total_requests']:.2f}")
```

### 4. Find Best Performing Models

Identify models with high success rates and low latency:

```python
results = search_models_with_chat_stats(query="claude")

# Filter by performance criteria
high_performers = [
    m for m in results
    if m['chat_stats']['success_rate'] > 95
    and m['chat_stats']['avg_processing_time_ms'] < 3000
    and m['chat_stats']['total_requests'] > 100  # Minimum sample size
]

for model in high_performers:
    print(f"{model['model_name']} - {model['provider']['name']}")
    print(f"  Success: {model['chat_stats']['success_rate']}%")
    print(f"  Speed: {model['chat_stats']['avg_processing_time_ms']}ms")
```

## Database Function

For improved performance with large datasets, a PostgreSQL function is available:

```sql
-- Get aggregated stats for a specific model
SELECT * FROM get_model_chat_stats(123);
```

This function is automatically used when available (falls back to Python aggregation if not).

## Performance Considerations

1. **Indexing**: The `chat_completion_requests` table has indexes on:
   - `model_id` (for joining with models)
   - `created_at` (for recent requests)
   - Combined `(model_id, created_at)` for efficient queries

2. **Caching**: Consider implementing caching for frequently searched queries

3. **Pagination**: Use the `limit` parameter to control response size

4. **Database Function**: The PostgreSQL aggregation function is more efficient than Python-side aggregation for large datasets

## Migration

To apply the database function for better performance:

```bash
# Apply the migration
supabase db push

# Or apply manually
psql -f supabase/migrations/20251220080000_create_model_chat_stats_function.sql
```

## Related Documentation

- [Chat Completion Request Tracking](./CHAT_COMPLETION_REQUEST_TRACKING.md)
- [Model Search and Filtering](./MODEL_SEARCH_AND_FILTERING.md)
- [Canonical Models Schema](./CANONICAL_MODELS_SCHEMA.md)
