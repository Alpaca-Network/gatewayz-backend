# Model Catalog Pagination

## Overview

The `/models` and `/v1/models` endpoints support pagination to efficiently handle large model catalogs (10,000+ models) without overwhelming clients or servers.

## How Pagination Works

### Default Behavior

- **Default limit**: 100 models per page
- **Default offset**: 0 (start from first model)
- **Max recommended limit**: 1000 models per page

### Request Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `gateway` | string | `"openrouter"` | Gateway to fetch models from (`"all"`, `"openrouter"`, `"groq"`, etc.) |
| `limit` | integer | `100` | Maximum number of models to return per page |
| `offset` | integer | `0` | Number of models to skip (for pagination) |
| `provider` | string | `null` | Filter by specific provider |
| `is_private` | boolean | `null` | Filter by private models |
| `include_huggingface` | boolean | `false` | Include HuggingFace metrics (slower) |

### Response Format

```json
{
  "data": [...],              // Array of model objects
  "total": 12543,             // Total number of models available
  "returned": 100,            // Number of models in this response
  "offset": 0,                // Current offset
  "limit": 100,               // Current limit
  "has_more": true,           // Whether more pages exist
  "next_offset": 100,         // Offset for next page (null if no more pages)
  "gateway": "all",
  "include_huggingface": false,
  "note": "Combined catalog...",
  "timestamp": "2025-01-27T..."
}
```

## Usage Examples

### Example 1: Get First Page (Default)

```bash
curl "https://api.gatewayz.ai/models?gateway=all"
# Returns: First 100 models (offset=0, limit=100)
```

### Example 2: Get Second Page

```bash
curl "https://api.gatewayz.ai/models?gateway=all&offset=100&limit=100"
# Returns: Models 101-200
```

### Example 3: Get Larger Pages

```bash
curl "https://api.gatewayz.ai/models?gateway=all&limit=500"
# Returns: First 500 models (faster if you need more models per request)
```

### Example 4: Paginate Through All Models

```python
import requests

base_url = "https://api.gatewayz.ai/models"
all_models = []
offset = 0
limit = 100

while True:
    response = requests.get(base_url, params={
        "gateway": "all",
        "limit": limit,
        "offset": offset
    })
    data = response.json()

    all_models.extend(data["data"])

    if not data["has_more"]:
        break

    offset = data["next_offset"]

print(f"Fetched {len(all_models)} models total")
```

### Example 5: Frontend Pagination Component

```typescript
// React example
const ModelsTable = () => {
  const [page, setPage] = useState(0);
  const [pageSize] = useState(100);
  const [models, setModels] = useState([]);
  const [totalModels, setTotalModels] = useState(0);

  useEffect(() => {
    fetch(`https://api.gatewayz.ai/models?gateway=all&limit=${pageSize}&offset=${page * pageSize}`)
      .then(res => res.json())
      .then(data => {
        setModels(data.data);
        setTotalModels(data.total);
      });
  }, [page, pageSize]);

  const totalPages = Math.ceil(totalModels / pageSize);

  return (
    <div>
      <ModelsGrid models={models} />
      <Pagination
        currentPage={page}
        totalPages={totalPages}
        onPageChange={setPage}
      />
      <p>Showing {models.length} of {totalModels} models</p>
    </div>
  );
};
```

## Performance Tips

### 1. Choose Appropriate Page Size

- **Small pages (50-100)**: Best for UI responsiveness, faster initial load
- **Medium pages (200-500)**: Good balance for data processing
- **Large pages (500-1000)**: Use when you need bulk data, but may be slower

### 2. Cache Responses

The endpoint returns `Cache-Control: public, max-age=300` headers (5 minute cache).

```javascript
// Browser will automatically cache for 5 minutes
fetch('https://api.gatewayz.ai/models?gateway=all&limit=100&offset=0');
```

### 3. Parallel Fetching

For faster bulk fetching, request multiple pages in parallel:

```python
import asyncio
import aiohttp

async def fetch_page(session, offset, limit):
    url = f"https://api.gatewayz.ai/models?gateway=all&offset={offset}&limit={limit}"
    async with session.get(url) as response:
        return await response.json()

async def fetch_all_models():
    async with aiohttp.ClientSession() as session:
        # First, get total count
        first_page = await fetch_page(session, 0, 100)
        total = first_page["total"]

        # Calculate number of pages needed
        page_size = 500
        num_pages = (total + page_size - 1) // page_size

        # Fetch all pages in parallel
        tasks = [
            fetch_page(session, i * page_size, page_size)
            for i in range(num_pages)
        ]
        pages = await asyncio.gather(*tasks)

        # Combine all models
        all_models = []
        for page in pages:
            all_models.extend(page["data"])

        return all_models

# Usage
models = asyncio.run(fetch_all_models())
print(f"Fetched {len(models)} models")
```

### 4. Use Filters to Reduce Dataset

If you don't need all models, use filters:

```bash
# Only OpenRouter models
curl "https://api.gatewayz.ai/models?gateway=openrouter&limit=100"

# Only specific provider
curl "https://api.gatewayz.ai/models?gateway=all&provider=anthropic&limit=50"

# Only private models
curl "https://api.gatewayz.ai/models?gateway=all&is_private=true&limit=100"
```

## Pagination Metadata

### `has_more`

Boolean indicating if more pages exist:
- `true`: More models available, fetch next page
- `false`: This is the last page

### `next_offset`

The offset value to use for the next page:
- If `has_more=true`: Contains the next offset value
- If `has_more=false`: Will be `null`

Example:
```json
{
  "offset": 0,
  "limit": 100,
  "returned": 100,
  "total": 12543,
  "has_more": true,
  "next_offset": 100  // Use this for next request
}
```

## Gateway-Specific Behavior

### `gateway=all`

Returns models from all supported gateways merged together:
- OpenRouter, Groq, Fireworks, Together, DeepInfra, Chutes, Featherless
- Google Vertex AI, Cerebras, Nebius, xAI, Novita, HuggingFace
- AIMO, Near AI, Fal.ai, Helicone, Anannas, AiHubMix, Infron AI
- Vercel AI Gateway, Simplismart, OpenAI, Anthropic, Clarifai, Sybil, Morpheus

Total count can exceed 10,000 models.

### Specific Gateway

Returns only models from that gateway:

```bash
# OpenRouter only (~300-500 models)
curl "https://api.gatewayz.ai/models?gateway=openrouter"

# Groq only (~50-100 models)
curl "https://api.gatewayz.ai/models?gateway=groq"

# HuggingFace (~thousands of models)
curl "https://api.gatewayz.ai/models?gateway=hug&limit=200"
```

## Error Handling

### Empty Results

If a gateway has no models available:
```json
{
  "data": [],
  "total": 0,
  "returned": 0,
  "has_more": false,
  "next_offset": null
}
```

### Invalid Parameters

- Negative offset → Returns 400 error
- Limit too large (>10000) → May timeout or be rejected
- Invalid gateway → Returns empty array

## Best Practices

1. **Start Small**: Use default `limit=100` for initial requests
2. **Check Total**: Use the `total` field to calculate total pages needed
3. **Use `has_more`**: Check this flag before fetching next page
4. **Handle Errors**: Gateway failures return empty arrays, not errors
5. **Cache Wisely**: Respect the 5-minute cache headers
6. **Parallel Fetch**: For bulk operations, fetch multiple pages in parallel
7. **Filter First**: Use filters to reduce the dataset before pagination

## OpenAI SDK Compatibility

The `/v1/models` endpoint is OpenAI-compatible:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://api.gatewayz.ai/v1",
    api_key="your-api-key"
)

# List models (paginated)
models = client.models.list()
print(f"Total models: {len(models.data)}")
```

Note: OpenAI SDK may not expose pagination parameters directly, use raw HTTP for full pagination control.

## Testing

Run the included test script:

```bash
python test_pagination.py
```

This will verify pagination is working correctly across different parameters.

## Related Endpoints

- `GET /models` - Main catalog endpoint (documented here)
- `GET /v1/models` - OpenAI-compatible endpoint (same pagination)
- `GET /health/catalog/models` - Health-checked models with pagination
- `GET /availability/models` - Availability-filtered models (no pagination limit)

## Support

For issues or questions about pagination:
- GitHub Issues: https://github.com/anthropics/gatewayz-backend/issues
- Documentation: https://docs.gatewayz.ai
