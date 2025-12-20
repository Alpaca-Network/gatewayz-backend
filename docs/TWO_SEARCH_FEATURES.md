# Two Separate Search Features

This system provides **2 distinct search features** with different purposes:

## Feature 1: Model Catalog Search (Provider Discovery)
**Purpose**: Find models and compare providers
**Endpoint**: `GET /models/catalog/search`
**Returns**: Summary statistics (averages)

## Feature 2: Chat Requests Search (Data for Graphing)
**Purpose**: Get raw request data for plotting
**Endpoint**: `GET /models/requests/search`
**Returns**: Individual request records

---

# Feature 1: Model Catalog Search

## Purpose
Discover which providers have a model and compare their average performance.

## Endpoint
```
GET /models/catalog/search
```

## Use Cases
- **"Which providers have GPT-4?"** → Search without provider filter
- **"What's the average cost of Claude on OpenRouter?"** → Search with provider
- **"Compare Llama 3 performance across providers"** → Search llama 3, view summaries
- **"Find the cheapest provider for GPT-4"** → Search gpt 4, compare pricing

## Parameters
```
q          Search query (required) - e.g., "gpt 4", "claude", "llama"
provider   Provider filter (optional) - e.g., "openrouter", "portkey"
limit      Max models to return (default 100, max 500)
```

## Examples

### Search all GPT-4 models across providers
```bash
curl "http://localhost:8000/models/catalog/search?q=gpt%204"
```

### Search GPT-4 on OpenRouter only
```bash
curl "http://localhost:8000/models/catalog/search?q=gpt%204&provider=openrouter"
```

### Find all Claude models
```bash
curl "http://localhost:8000/models/catalog/search?q=claude"
```

## Response Format

```json
{
  "success": true,
  "query": "gpt 4",
  "provider_filter": null,
  "total_results": 3,
  "models": [
    {
      "model_id": 123,
      "model_name": "GPT-4",
      "provider": {
        "id": 1,
        "name": "OpenRouter",
        "slug": "openrouter"
      },
      "pricing_prompt": 0.00003,
      "pricing_completion": 0.00006,
      "context_length": 8192,
      "summary": {
        "total_requests": 1523,
        "avg_input_tokens": 842.5,
        "avg_output_tokens": 371.2,
        "avg_processing_time_ms": 3421.7,
        "success_rate": 98.75,
        "completed_requests": 1504,
        "failed_requests": 19,
        "last_request_at": "2025-12-20T08:00:00Z"
      }
    },
    {
      "model_name": "GPT-4",
      "provider": {
        "name": "Portkey",
        "slug": "portkey"
      },
      "summary": {
        "total_requests": 892,
        "avg_input_tokens": 756.3,
        "avg_output_tokens": 412.8,
        "avg_processing_time_ms": 2987.4,
        "success_rate": 99.21
      }
    }
  ]
}
```

## What You Get

For each model:
- ✅ Model info (name, identifier, description)
- ✅ Provider details (name, slug)
- ✅ Pricing (prompt/completion costs)
- ✅ Capabilities (context, streaming, vision)
- ✅ **Summary statistics**:
  - Total request count
  - **Average** input/output tokens
  - **Average** processing time
  - Success rate percentage
  - Completed/failed breakdown

## When to Use
- Discovering which providers have a model
- Comparing average performance
- Choosing the best provider
- Getting pricing information
- Viewing success rates

---

# Feature 2: Chat Requests Search

## Purpose
Get individual chat completion request records for plotting graphs and analyzing trends.

## Endpoint
```
GET /models/requests/search
```

## Use Cases
- **"Plot GPT-4 token usage over time"** → Get requests, plot created_at vs tokens
- **"See processing time trends for Claude"** → Get requests, plot time series
- **"Compare GPT-4 performance across providers visually"** → Get from multiple, plot
- **"Analyze cost per request over time"** → Get requests, calculate costs, plot

## Parameters
```
q          Search query (required) - e.g., "gpt 4", "claude", "llama"
provider   Provider filter (optional) - e.g., "openrouter", "portkey"
limit      Max requests per model (default 500, max 10000)
offset     Pagination offset (default 0)
```

## Examples

### Get 500 GPT-4 requests across all providers
```bash
curl "http://localhost:8000/models/requests/search?q=gpt%204&limit=500"
```

### Get 1000 GPT-4 requests from OpenRouter
```bash
curl "http://localhost:8000/models/requests/search?q=gpt%204&provider=openrouter&limit=1000"
```

### Get all Claude requests (up to 5000)
```bash
curl "http://localhost:8000/models/requests/search?q=claude&limit=5000"
```

### Paginate through requests
```bash
# First 500
curl "http://localhost:8000/models/requests/search?q=llama&limit=500&offset=0"

# Next 500
curl "http://localhost:8000/models/requests/search?q=llama&limit=500&offset=500"
```

## Response Format

```json
{
  "success": true,
  "query": "gpt 4",
  "provider_filter": "openrouter",
  "limit": 500,
  "offset": 0,
  "total_results": 1,
  "models": [
    {
      "model_id": 123,
      "model_name": "GPT-4",
      "provider": {
        "name": "OpenRouter",
        "slug": "openrouter"
      },
      "pricing_prompt": 0.00003,
      "pricing_completion": 0.00006,
      "total_requests": 1523,
      "returned_requests": 500,
      "requests": [
        {
          "id": "uuid-1",
          "request_id": "req_abc123",
          "input_tokens": 842,
          "output_tokens": 371,
          "total_tokens": 1213,
          "processing_time_ms": 3421,
          "status": "completed",
          "created_at": "2025-12-20T08:15:23Z"
        },
        {
          "id": "uuid-2",
          "request_id": "req_def456",
          "input_tokens": 956,
          "output_tokens": 428,
          "total_tokens": 1384,
          "processing_time_ms": 2987,
          "status": "completed",
          "created_at": "2025-12-20T08:10:15Z"
        }
        // ... 498 more requests
      ]
    }
  ]
}
```

## What You Get

For each model:
- ✅ Model and provider info
- ✅ Pricing information
- ✅ **Individual request records**:
  - Exact token counts (not averages)
  - Exact processing times
  - Status (completed/failed)
  - **Timestamps** (for plotting)
  - Error messages if failed
- ✅ Total count vs returned count

## When to Use
- Plotting graphs and visualizations
- Analyzing trends over time
- Identifying performance patterns
- Comparing providers visually
- Cost analysis per request
- Finding outliers and anomalies

---

# Comparison Table

| Feature | Catalog Search | Requests Search |
|---------|----------------|-----------------|
| **Endpoint** | `/models/catalog/search` | `/models/requests/search` |
| **Purpose** | Provider discovery | Graphing/analysis |
| **Returns** | Summary stats (averages) | Individual records |
| **Default limit** | 100 models | 500 requests/model |
| **Max limit** | 500 models | 10000 requests/model |
| **Pagination** | No | Yes (offset) |
| **Response size** | Small (~10KB) | Large (~250KB-5MB) |
| **Speed** | Fast | Moderate |
| **Best for** | Comparisons | Visualization |

---

# Usage Patterns

## Pattern 1: Discovery then Deep Dive

```bash
# Step 1: Find which providers have GPT-4 (Catalog Search)
curl "http://localhost:8000/models/catalog/search?q=gpt%204"

# Response shows OpenRouter has good average performance

# Step 2: Get detailed request data for graphing (Requests Search)
curl "http://localhost:8000/models/requests/search?q=gpt%204&provider=openrouter&limit=1000"

# Now plot the data to see trends
```

## Pattern 2: Provider Comparison

```bash
# Get summary stats for all providers (Catalog Search)
curl "http://localhost:8000/models/catalog/search?q=claude"

# Response shows:
# - OpenRouter: avg 3421ms
# - Portkey: avg 2987ms
# - Together: avg 4123ms

# Get actual requests to plot visual comparison (Requests Search)
curl "http://localhost:8000/models/requests/search?q=claude&limit=500"

# Plot processing time over time for each provider
```

## Pattern 3: Cost Analysis

```bash
# Get pricing and averages (Catalog Search)
curl "http://localhost:8000/models/catalog/search?q=llama"

# Get individual requests (Requests Search)
curl "http://localhost:8000/models/requests/search?q=llama&limit=1000"

# Calculate exact cost per request:
# cost = (input_tokens * pricing_prompt) + (output_tokens * pricing_completion)

# Plot cost trend over time
```

---

# Python Examples

## Feature 1: Catalog Search

```python
from src.db.chat_completion_requests import search_models_with_chat_summary

# Find all GPT-4 models with summary stats
results = search_models_with_chat_summary(query="gpt 4")

for model in results:
    print(f"{model['model_name']} ({model['provider']['name']})")
    print(f"  Average processing: {model['summary']['avg_processing_time_ms']}ms")
    print(f"  Success rate: {model['summary']['success_rate']}%")
    print(f"  Total requests: {model['summary']['total_requests']}")
```

## Feature 2: Requests Search

```python
from src.db.models_requests_search import search_chat_requests
import matplotlib.pyplot as plt
import pandas as pd

# Get individual requests
results = search_chat_requests(query="gpt 4", provider_name="openrouter", requests_limit=1000)
model = results[0]

# Convert to DataFrame
df = pd.DataFrame(model['requests'])
df['created_at'] = pd.to_datetime(df['created_at'])

# Plot processing time over time
plt.figure(figsize=(12, 6))
plt.plot(df['created_at'], df['processing_time_ms'])
plt.xlabel('Time')
plt.ylabel('Processing Time (ms)')
plt.title(f"{model['model_name']} - Processing Time Trend")
plt.show()
```

---

# Which One Should I Use?

## Use **Catalog Search** when you want to:
- ✅ Find which providers have a model
- ✅ Compare average performance
- ✅ Check pricing across providers
- ✅ See success rates
- ✅ Get a quick overview

## Use **Requests Search** when you want to:
- ✅ Plot graphs
- ✅ Analyze trends over time
- ✅ See individual data points
- ✅ Identify patterns or outliers
- ✅ Calculate per-request costs
- ✅ Create visualizations

---

# Bonus: Get Requests by Model ID

If you already know the model ID:

```
GET /models/{model_id}/requests
```

**Examples:**
```bash
# Get 500 requests for model 123
curl "http://localhost:8000/models/123/requests"

# Get only failed requests
curl "http://localhost:8000/models/123/requests?status=failed"

# Paginate
curl "http://localhost:8000/models/123/requests?limit=500&offset=500"
```

**Use this when:**
- You already have the model_id
- You want requests from one specific model
- You need to filter by status

---

# Summary

🎯 **Two Features, Two Purposes:**

1. **Catalog Search** = Provider discovery with summary stats
2. **Requests Search** = Raw data for plotting graphs

Both use the same flexible search (e.g., "gpt 4" matches variations), but return different data:
- Catalog: Averages and summaries
- Requests: Individual records with timestamps

Choose based on what you need! 📊
