# Unique Models API Endpoint

## Overview

The `/models/unique` endpoint provides a deduplicated view of models with provider comparison information. Instead of showing "GPT-4" multiple times (once per provider), it shows each unique model once with an array of all providers offering it.

## Endpoint

```
GET /models/unique
```

## Use Cases

1. **Find models available from multiple providers** - Discover which models have the most provider options
2. **Compare pricing across providers** - See which provider offers the best price for a specific model
3. **Identify provider alternatives** - Find backup providers for critical models
4. **Price optimization** - Automatically route to the cheapest provider for each model
5. **Provider diversity analysis** - Understand the market landscape and model availability

## Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 100 | Number of results to return (max: 1000) |
| `offset` | integer | 0 | Pagination offset |
| `min_providers` | integer | null | Minimum number of providers (e.g., 2 for multi-provider models) |
| `sort_by` | string | "provider_count" | Sort field: `provider_count`, `name`, or `cheapest_price` |
| `order` | string | "desc" | Sort order: `asc` or `desc` |
| `include_inactive` | boolean | false | Include inactive models |

## Response Format

```json
{
  "models": [
    {
      "id": "gpt-4",
      "name": "GPT-4",
      "provider_count": 3,
      "providers": [
        {
          "slug": "openrouter",
          "name": "OpenRouter",
          "pricing": {
            "prompt": "0.03",
            "completion": "0.06",
            "image": "0",
            "request": "0"
          },
          "context_length": 8192,
          "health_status": "healthy",
          "average_response_time_ms": 1200,
          "modality": "text->text",
          "supports_streaming": true,
          "supports_function_calling": true,
          "supports_vision": false,
          "model_id": "openai/gpt-4"
        },
        {
          "slug": "groq",
          "name": "Groq",
          "pricing": {
            "prompt": "0.025",
            "completion": "0.05",
            "image": "0",
            "request": "0"
          },
          "context_length": 8192,
          "health_status": "healthy",
          "average_response_time_ms": 950,
          "modality": "text->text",
          "supports_streaming": true,
          "supports_function_calling": true,
          "supports_vision": false,
          "model_id": "groq/gpt-4"
        }
      ],
      "cheapest_provider": "groq",
      "fastest_provider": "groq",
      "cheapest_prompt_price": 0.025,
      "fastest_response_time": 950
    }
  ],
  "total": 234,
  "limit": 100,
  "offset": 0,
  "filters": {
    "min_providers": null,
    "include_inactive": false
  },
  "sort": {
    "by": "provider_count",
    "order": "desc"
  }
}
```

## Example Queries

### 1. Most widely available models
Get models offered by the most providers:

```bash
curl "https://api.gatewayz.ai/models/unique?sort_by=provider_count&order=desc&limit=20"
```

**Use case**: Find models with the best provider diversity for reliability and failover.

### 2. Models with multiple providers
Get only models available from 3+ providers:

```bash
curl "https://api.gatewayz.ai/models/unique?min_providers=3&sort_by=provider_count&order=desc"
```

**Use case**: Ensure you have multiple provider options for critical production workloads.

### 3. Cheapest unique models
Find the most cost-effective models:

```bash
curl "https://api.gatewayz.ai/models/unique?sort_by=cheapest_price&order=asc&limit=50"
```

**Use case**: Optimize costs by identifying the cheapest models and providers.

### 4. Alphabetical model list
Browse all unique models alphabetically:

```bash
curl "https://api.gatewayz.ai/models/unique?sort_by=name&order=asc&limit=100"
```

**Use case**: Explore the full catalog in a readable format.

### 5. Pagination
Get the second page of results:

```bash
curl "https://api.gatewayz.ai/models/unique?limit=100&offset=100"
```

## Response Fields Explained

### Model Level

- `id` - Normalized model identifier (e.g., "gpt-4")
- `name` - Display name of the model
- `provider_count` - Total number of providers offering this model
- `providers` - Array of provider objects (sorted by price, cheapest first)
- `cheapest_provider` - Slug of the provider with the lowest prompt pricing
- `fastest_provider` - Slug of the provider with the best response time
- `cheapest_prompt_price` - Lowest prompt price across all providers
- `fastest_response_time` - Fastest response time in milliseconds

### Provider Level (within `providers` array)

- `slug` - Provider identifier (e.g., "openrouter", "groq")
- `name` - Provider display name
- `model_id` - Provider-specific model ID (e.g., "openai/gpt-4")
- `pricing` - Pricing information
  - `prompt` - Price per token (input)
  - `completion` - Price per token (output)
  - `image` - Price per image (for image models)
  - `request` - Price per request
- `context_length` - Maximum context window in tokens
- `health_status` - Current health status: "healthy", "degraded", "down", "unknown"
- `average_response_time_ms` - Average response time in milliseconds
- `modality` - Model modality (e.g., "text->text", "text->image")
- `supports_streaming` - Whether the model supports streaming responses
- `supports_function_calling` - Whether the model supports function calling
- `supports_vision` - Whether the model supports vision/image inputs

## Implementation Details

### Database Tables

This endpoint utilizes two key tables:

1. **`unique_models`** - Stores deduplicated model information
   - `id` - Primary key
   - `model_name` - Normalized model name
   - `model_count` - Number of provider instances
   - `sample_model_id` - Sample model ID for reference

2. **`unique_models_provider`** - Junction table linking unique models to providers
   - `unique_model_id` - Foreign key to `unique_models`
   - Joined with `models` table (provider-specific model data)
   - Joined with `providers` table (provider information)

### Performance

- **Query optimization**: Uses the optimized N+1 query fix (2 queries instead of 500+)
- **Response time**: Sub-second for full catalog (<1s for all unique models)
- **Pagination**: Efficient in-memory pagination after database fetch
- **Caching**: Results can be cached at the application or CDN level

## Integration Examples

### Python

```python
import requests

# Get models with 2+ providers, sorted by provider count
response = requests.get(
    "https://api.gatewayz.ai/models/unique",
    params={
        "min_providers": 2,
        "sort_by": "provider_count",
        "order": "desc",
        "limit": 50
    }
)

models = response.json()["models"]

# Find cheapest provider for GPT-4
for model in models:
    if model["name"] == "GPT-4":
        cheapest = model["cheapest_provider"]
        price = model["cheapest_prompt_price"]
        print(f"Cheapest GPT-4 provider: {cheapest} at ${price} per token")
```

### JavaScript/TypeScript

```typescript
// Fetch unique models
const response = await fetch(
  'https://api.gatewayz.ai/models/unique?min_providers=2&sort_by=provider_count&order=desc'
);

const data = await response.json();

// Build provider comparison table
data.models.forEach(model => {
  console.log(`${model.name}: ${model.provider_count} providers`);
  console.log(`  Cheapest: ${model.cheapest_provider} ($${model.cheapest_prompt_price})`);
  console.log(`  Fastest: ${model.fastest_provider} (${model.fastest_response_time}ms)`);
});
```

### cURL

```bash
# Get top 10 models by provider count
curl -X GET "https://api.gatewayz.ai/models/unique?sort_by=provider_count&order=desc&limit=10" \
  -H "Accept: application/json" | jq '.models[] | {name: .name, providers: .provider_count}'
```

## Common Use Cases

### 1. Price Comparison Dashboard

Display a table showing each model with pricing from all providers:

```python
for model in models:
    print(f"\n{model['name']}")
    for provider in model['providers']:
        print(f"  {provider['name']}: ${provider['pricing']['prompt']} input, ${provider['pricing']['completion']} output")
```

### 2. Automatic Provider Selection

Route requests to the cheapest available provider:

```python
def get_cheapest_provider(model_name):
    # Find the model in unique models list
    for model in models:
        if model['name'].lower() == model_name.lower():
            return model['cheapest_provider']
    return None

provider = get_cheapest_provider("GPT-4")
# Use the provider for routing
```

### 3. Provider Health Monitoring

Check which providers have healthy status for each model:

```python
def get_healthy_providers(model_name):
    for model in models:
        if model['name'] == model_name:
            return [p['slug'] for p in model['providers'] if p['health_status'] == 'healthy']
    return []

healthy_providers = get_healthy_providers("Claude 3")
```

### 4. Model Discovery

Find models available from multiple providers for redundancy:

```bash
# Get models with 5+ providers
curl "https://api.gatewayz.ai/models/unique?min_providers=5&limit=20"
```

## Rate Limiting

This endpoint respects the standard API rate limits:
- **Authenticated**: 1000 requests/minute
- **Unauthenticated**: 100 requests/minute

## Error Handling

### HTTP 500 - Internal Server Error

```json
{
  "detail": "Failed to fetch unique models: <error message>"
}
```

**Causes**:
- Database connection issues
- Data transformation errors

**Solution**: Retry after a few seconds. If persistent, contact support.

## Future Enhancements

Planned features:
- [ ] Search/filter by model name
- [ ] Filter by modality (text, image, audio)
- [ ] Filter by capabilities (streaming, function calling, vision)
- [ ] Price range filters
- [ ] Context length filters
- [ ] Real-time caching with Redis
- [ ] GraphQL endpoint variant
- [ ] Webhook notifications for price changes

## Related Endpoints

- `GET /models` - Get all models (flat list, may include duplicates)
- `GET /models?gateway=all&unique_models=true` - Alternative way to get unique models
- `GET /providers` - Get all providers
- `GET /gateways` - Get gateway configurations

## Support

For questions or issues:
- GitHub Issues: https://github.com/Alpaca-Network/gatewayz-backend/issues
- Documentation: https://docs.gatewayz.ai
- API Reference: https://api.gatewayz.ai/docs
