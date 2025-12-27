# Graphing Model Chat Completion Data

This guide shows you how to use the individual chat completion request records to create visual graphs and analyze model performance over time.

## Overview

The search endpoint now returns **individual chat completion request records** instead of just averages, allowing you to:

- Plot token usage trends over time
- Visualize processing time curves
- Track success/failure patterns
- Analyze request frequency
- Compare providers visually

## Quick Start

### 1. Get Data via API

```bash
# Get GPT-4 models with individual request records
curl "http://localhost:8000/models/catalog/search?q=gpt%204&requests_limit=1000"
```

**Response:**
```json
{
  "models": [
    {
      "model_name": "GPT-4",
      "provider": {...},
      "requests": [
        {
          "id": "uuid",
          "request_id": "req_123",
          "input_tokens": 842,
          "output_tokens": 371,
          "total_tokens": 1213,
          "processing_time_ms": 3421,
          "status": "completed",
          "created_at": "2025-12-20T08:00:00Z"
        },
        // ... more individual requests
      ],
      "total_requests": 1523
    }
  ]
}
```

### 2. Create Graphs with Python

```python
from src.db.chat_completion_requests import search_models_with_chat_stats
import matplotlib.pyplot as plt
import pandas as pd

# Get data
results = search_models_with_chat_stats(query="gpt 4", requests_limit=1000)
model = results[0]
requests = model['requests']

# Convert to DataFrame
df = pd.DataFrame(requests)
df['created_at'] = pd.to_datetime(df['created_at'])

# Plot token usage over time
plt.figure(figsize=(12, 6))
plt.plot(df['created_at'], df['input_tokens'], label='Input')
plt.plot(df['created_at'], df['output_tokens'], label='Output')
plt.xlabel('Time')
plt.ylabel('Tokens')
plt.title(f"{model['model_name']} - Token Usage")
plt.legend()
plt.show()
```

### 3. Use the Plotting Script

```bash
# Install dependencies
pip install matplotlib pandas

# Create all graphs for GPT-4 models
python examples/plot_model_stats.py --query "gpt 4"

# Save to files
python examples/plot_model_stats.py --query "gpt 4" --output charts/

# Compare providers
python examples/plot_model_stats.py --query "gpt 4" --compare

# Filter by provider
python examples/plot_model_stats.py --query "gpt 4" --provider openrouter
```

## Available Graphs

The plotting script creates these visualizations:

### 1. Token Usage Over Time
- Line chart showing input/output tokens per request
- Cumulative token usage curve
- **Use case**: Track how token usage changes over time

### 2. Processing Time Analysis
- Scatter plot of processing time per request
- 5-request moving average
- Distribution histogram with mean/median
- **Use case**: Identify latency trends and outliers

### 3. Success Rate Analysis
- Timeline showing success/failure for each request
- 10-request rolling success rate
- Overall success rate line
- **Use case**: Monitor reliability and spot issues

### 4. Request Frequency
- Bar chart of requests per hour
- **Use case**: Understand usage patterns

### 5. Provider Comparison (with --compare flag)
- Side-by-side comparison of same model across providers
- Processing time, tokens, success rate, cost
- **Use case**: Choose the best provider

## API Parameters

```
GET /models/catalog/search

Parameters:
  q               Search query (required)
  provider        Filter by provider (optional)
  limit           Max models to return (1-500, default 100)
  include_requests   Include request records (default true)
  requests_limit  Max requests per model (1-10000, default 1000)
```

**Examples:**

```bash
# Get 500 most recent requests per model
curl "http://localhost:8000/models/catalog/search?q=claude&requests_limit=500"

# Get models without request data (faster)
curl "http://localhost:8000/models/catalog/search?q=gpt&include_requests=false"

# Get specific provider with all data
curl "http://localhost:8000/models/catalog/search?q=llama&provider=together&requests_limit=5000"
```

## Data Structure

Each request record contains:

```json
{
  "id": "uuid",                    // Database ID
  "request_id": "req_abc123",      // Request identifier
  "input_tokens": 842,             // Input token count
  "output_tokens": 371,            // Output token count
  "total_tokens": 1213,            // Total (auto-calculated)
  "processing_time_ms": 3421,      // Processing time in ms
  "status": "completed",           // completed, failed, partial
  "error_message": null,           // Error if failed
  "user_id": "uuid",               // User ID (optional)
  "created_at": "2025-12-20T08:00:00Z"  // Timestamp
}
```

## Visualization Examples

### Example 1: Time Series Analysis

```python
import pandas as pd
import matplotlib.pyplot as plt

results = search_models_with_chat_stats(query="gpt 4")
df = pd.DataFrame(results[0]['requests'])
df['created_at'] = pd.to_datetime(df['created_at'])

# Resample by hour and get mean processing time
hourly = df.set_index('created_at').resample('H')['processing_time_ms'].mean()

plt.plot(hourly)
plt.title('Average Processing Time by Hour')
plt.ylabel('Processing Time (ms)')
plt.show()
```

### Example 2: Cost Analysis

```python
results = search_models_with_chat_stats(query="gpt 4", provider_name="openrouter")
model = results[0]
df = pd.DataFrame(model['requests'])

# Calculate cost per request
df['cost'] = (
    df['input_tokens'] * model['pricing_prompt'] +
    df['output_tokens'] * model['pricing_completion']
)

# Plot cost distribution
df['cost'].hist(bins=30)
plt.xlabel('Cost per Request ($)')
plt.ylabel('Frequency')
plt.title(f"{model['model_name']} - Cost Distribution")
plt.show()

print(f"Total cost: ${df['cost'].sum():.2f}")
print(f"Average cost: ${df['cost'].mean():.4f}")
```

### Example 3: Compare Providers

```python
# Get GPT-4 from multiple providers
all_results = search_models_with_chat_stats(query="gpt 4")

fig, ax = plt.subplots(figsize=(12, 6))

for model in all_results[:3]:  # Top 3 providers
    df = pd.DataFrame(model['requests'])
    df['created_at'] = pd.to_datetime(df['created_at'])

    # Plot processing time
    ax.plot(df['created_at'], df['processing_time_ms'],
           label=f"{model['provider']['name']}", alpha=0.7)

ax.set_xlabel('Time')
ax.set_ylabel('Processing Time (ms)')
ax.set_title('GPT-4 Processing Time - Provider Comparison')
ax.legend()
ax.grid(True, alpha=0.3)
plt.show()
```

### Example 4: Success Rate Trend

```python
results = search_models_with_chat_stats(query="claude")
df = pd.DataFrame(results[0]['requests'])
df['created_at'] = pd.to_datetime(df['created_at'])
df['success'] = df['status'] == 'completed'

# Calculate rolling success rate (window of 20 requests)
rolling_success = df.set_index('created_at')['success'].rolling(20).mean() * 100

plt.figure(figsize=(12, 6))
plt.plot(rolling_success, linewidth=2)
plt.axhline(y=95, color='r', linestyle='--', label='95% threshold')
plt.xlabel('Time')
plt.ylabel('Success Rate (%)')
plt.title('20-Request Rolling Success Rate')
plt.ylim([0, 105])
plt.legend()
plt.grid(True, alpha=0.3)
plt.show()
```

## Tips for Effective Graphing

### 1. Filter Relevant Time Periods

```python
# Last 24 hours only
from datetime import datetime, timedelta

df = pd.DataFrame(requests)
df['created_at'] = pd.to_datetime(df['created_at'])
cutoff = datetime.now() - timedelta(days=1)
recent = df[df['created_at'] > cutoff]
```

### 2. Handle Outliers

```python
# Remove outliers using IQR method
Q1 = df['processing_time_ms'].quantile(0.25)
Q3 = df['processing_time_ms'].quantile(0.75)
IQR = Q3 - Q1
filtered = df[
    (df['processing_time_ms'] >= Q1 - 1.5*IQR) &
    (df['processing_time_ms'] <= Q3 + 1.5*IQR)
]
```

### 3. Aggregate by Time Periods

```python
# Daily averages
daily = df.set_index('created_at').resample('D').agg({
    'input_tokens': 'mean',
    'output_tokens': 'mean',
    'processing_time_ms': 'mean',
    'status': lambda x: (x == 'completed').mean() * 100
})
```

### 4. Compare Multiple Metrics

```python
# Create subplots for different metrics
fig, axes = plt.subplots(2, 2, figsize=(14, 10))

axes[0, 0].plot(df['created_at'], df['input_tokens'])
axes[0, 0].set_title('Input Tokens')

axes[0, 1].plot(df['created_at'], df['output_tokens'])
axes[0, 1].set_title('Output Tokens')

axes[1, 0].plot(df['created_at'], df['processing_time_ms'])
axes[1, 0].set_title('Processing Time')

axes[1, 1].plot(df['created_at'], df['total_tokens'])
axes[1, 1].set_title('Total Tokens')

plt.tight_layout()
plt.show()
```

## Performance Considerations

### Request Limits

- Default: 1000 requests per model (good balance)
- Large datasets: Use lower limit for faster response
- Detailed analysis: Increase up to 10,000

```bash
# Fast overview (100 requests)
curl "http://localhost:8000/models/catalog/search?q=gpt&requests_limit=100"

# Detailed analysis (5000 requests)
curl "http://localhost:8000/models/catalog/search?q=gpt&requests_limit=5000"
```

### Pagination for Large Datasets

If you have >10,000 requests, fetch in batches:

```python
def get_all_requests(model_id, batch_size=5000):
    all_requests = []
    offset = 0

    while True:
        batch = get_chat_completion_stats(
            model_id=model_id,
            limit=batch_size
        )

        if not batch:
            break

        all_requests.extend(batch)
        offset += batch_size

        if len(batch) < batch_size:
            break

    return all_requests
```

## Troubleshooting

**Issue**: No graphs appear
**Fix**: Install matplotlib: `pip install matplotlib pandas`

**Issue**: Graphs look cluttered
**Fix**: Reduce `requests_limit` or filter by date range

**Issue**: Missing data points
**Fix**: Check that requests are being tracked (see CHAT_COMPLETION_REQUEST_TRACKING.md)

**Issue**: Slow API response
**Fix**: Reduce `requests_limit` or set `include_requests=false`

## Related Documentation

- [Model Search How It Works](./MODEL_SEARCH_HOW_IT_WORKS.md)
- [Chat Completion Request Tracking](./CHAT_COMPLETION_REQUEST_TRACKING.md)
- [Implementation Summary](./IMPLEMENTATION_SUMMARY_MODEL_SEARCH_WITH_STATS.md)
