# Chat Requests Plot Data Endpoint

## Overview

New optimized endpoint for fetching chat completion request data specifically designed for plotting and display.

**Endpoint:** `GET /api/monitoring/chat-requests/plot-data`

## Why This Endpoint?

**Problem:** Fetching full request objects for plotting is inefficient:
- Sends unnecessary data (request_id, error_message, etc.)
- Large payload size (each request = 500+ bytes)
- Slow to transfer and parse

**Solution:** This endpoint returns:
- Last 10 full requests for display
- ALL requests but compressed (only tokens + latency arrays)
- **90% smaller payload** for the same data!

## Request Parameters

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `model_id` | integer | Filter by specific model ID | `?model_id=123` |
| `provider_id` | integer | Filter by provider ID | `?provider_id=5` |
| `start_date` | string | Filter from date (ISO format) | `?start_date=2026-01-01` |
| `end_date` | string | Filter to date (ISO format) | `?end_date=2026-01-31` |

## Response Format

```json
{
  "success": true,
  "recent_requests": [
    {
      "id": 12345,
      "request_id": "req_abc123",
      "model_id": 1,
      "input_tokens": 150,
      "output_tokens": 200,
      "total_tokens": 350,
      "processing_time_ms": 1234,
      "status": "completed",
      "error_message": null,
      "created_at": "2026-01-04T10:30:00Z",
      "models": {
        "id": 1,
        "model_id": "gpt-4",
        "model_name": "GPT-4",
        "provider_model_id": "openai/gpt-4",
        "providers": {
          "id": 1,
          "name": "OpenRouter",
          "slug": "openrouter"
        }
      }
    }
    // ... 9 more recent requests
  ],
  "plot_data": {
    "tokens": [350, 420, 180, 500, 320, ...],
    "latency": [1234, 890, 567, 2100, 450, ...],
    "timestamps": [
      "2026-01-01T10:00:00Z",
      "2026-01-01T10:05:00Z",
      "2026-01-01T10:10:00Z",
      ...
    ]
  },
  "metadata": {
    "recent_count": 10,
    "total_count": 5432,
    "timestamp": "2026-01-04T12:00:00Z",
    "compression": "arrays",
    "format_version": "1.0"
  }
}
```

## Data Structure Breakdown

### 1. `recent_requests` (Array of Objects)

Last 10 requests with full details for display in a table or list.

**Fields:**
- `id` - Database ID
- `request_id` - Unique request identifier
- `model_id` - Model database ID
- `input_tokens` - Tokens in the prompt
- `output_tokens` - Tokens in the response
- `total_tokens` - Sum of input + output tokens
- `processing_time_ms` - Request latency in milliseconds
- `status` - "completed", "failed", or "partial"
- `error_message` - Error details if failed (null if success)
- `created_at` - ISO timestamp
- `models` - Nested model and provider information

### 2. `plot_data` (Compressed Arrays)

ALL requests but only the minimal data needed for plotting.

**Structure:**
```typescript
{
  tokens: number[],      // Array of total_tokens for each request
  latency: number[],     // Array of processing_time_ms for each request
  timestamps: string[]   // Array of ISO timestamps (for x-axis)
}
```

**Why arrays?**
- Much smaller payload (90% reduction)
- Easy to use with charting libraries
- Same index across all arrays (data[i] corresponds to same request)

**Example:**
```javascript
// Request 0: 350 tokens, 1234ms latency, at 10:00:00
// Request 1: 420 tokens, 890ms latency, at 10:05:00
// Request 2: 180 tokens, 567ms latency, at 10:10:00

plot_data.tokens[0]      // 350
plot_data.latency[0]     // 1234
plot_data.timestamps[0]  // "2026-01-01T10:00:00Z"
```

### 3. `metadata` (Object)

Information about the response.

**Fields:**
- `recent_count` - Number of recent requests returned (max 10)
- `total_count` - Total number of requests in plot_data
- `timestamp` - When this data was generated
- `compression` - Always "arrays"
- `format_version` - API version (currently "1.0")

## Frontend Usage Examples

### Example 1: Display Recent Requests Table

```typescript
interface RecentRequest {
  id: number;
  request_id: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  processing_time_ms: number;
  status: string;
  created_at: string;
  models: {
    model_name: string;
    providers: {
      name: string;
      slug: string;
    };
  };
}

// Fetch data
const response = await fetch('/api/monitoring/chat-requests/plot-data?model_id=1');
const data = await response.json();

// Use recent requests for table
const recentRequests: RecentRequest[] = data.recent_requests;

// Render table
recentRequests.forEach(req => {
  console.log(`${req.models.model_name}: ${req.total_tokens} tokens, ${req.processing_time_ms}ms`);
});
```

### Example 2: Plot Tokens vs Latency Scatter Chart

```typescript
import { Scatter } from 'react-chartjs-2';

// Fetch data
const response = await fetch('/api/monitoring/chat-requests/plot-data?model_id=1');
const { plot_data } = await response.json();

// Create scatter plot data
const scatterData = {
  datasets: [{
    label: 'Tokens vs Latency',
    data: plot_data.tokens.map((tokens, index) => ({
      x: tokens,                    // X-axis: Total tokens
      y: plot_data.latency[index]   // Y-axis: Latency
    })),
    backgroundColor: 'rgba(75, 192, 192, 0.6)',
  }]
};

// Render chart
<Scatter data={scatterData} options={{
  scales: {
    x: { title: { display: true, text: 'Total Tokens' } },
    y: { title: { display: true, text: 'Latency (ms)' } }
  }
}} />
```

### Example 3: Plot Latency Over Time

```typescript
import { Line } from 'react-chartjs-2';

// Fetch data
const response = await fetch('/api/monitoring/chat-requests/plot-data?model_id=1');
const { plot_data } = await response.json();

// Create line chart data
const lineData = {
  labels: plot_data.timestamps.map(ts => new Date(ts).toLocaleTimeString()),
  datasets: [{
    label: 'Latency Over Time',
    data: plot_data.latency,
    borderColor: 'rgb(75, 192, 192)',
    tension: 0.1
  }]
};

// Render chart
<Line data={lineData} options={{
  scales: {
    x: { title: { display: true, text: 'Time' } },
    y: { title: { display: true, text: 'Latency (ms)' } }
  }
}} />
```

### Example 4: Plot Token Distribution

```typescript
import { Histogram } from 'react-chartjs-2';

// Fetch data
const response = await fetch('/api/monitoring/chat-requests/plot-data?model_id=1');
const { plot_data } = await response.json();

// Create histogram data
const histogramData = {
  labels: ['0-100', '100-500', '500-1000', '1000-5000', '5000+'],
  datasets: [{
    label: 'Token Distribution',
    data: [
      plot_data.tokens.filter(t => t < 100).length,
      plot_data.tokens.filter(t => t >= 100 && t < 500).length,
      plot_data.tokens.filter(t => t >= 500 && t < 1000).length,
      plot_data.tokens.filter(t => t >= 1000 && t < 5000).length,
      plot_data.tokens.filter(t => t >= 5000).length,
    ],
    backgroundColor: 'rgba(153, 102, 255, 0.6)',
  }]
};
```

### Example 5: Calculate Statistics

```typescript
// Fetch data
const response = await fetch('/api/monitoring/chat-requests/plot-data?model_id=1');
const { plot_data, metadata } = await response.json();

// Calculate stats
const avgTokens = plot_data.tokens.reduce((a, b) => a + b, 0) / plot_data.tokens.length;
const avgLatency = plot_data.latency.reduce((a, b) => a + b, 0) / plot_data.latency.length;
const maxTokens = Math.max(...plot_data.tokens);
const maxLatency = Math.max(...plot_data.latency);

console.log(`Total Requests: ${metadata.total_count}`);
console.log(`Average Tokens: ${avgTokens.toFixed(2)}`);
console.log(`Average Latency: ${avgLatency.toFixed(2)}ms`);
console.log(`Max Tokens: ${maxTokens}`);
console.log(`Max Latency: ${maxLatency}ms`);
```

## Performance Comparison

### Old Approach (fetching full requests)

```json
// Each request = ~500 bytes
{
  "id": 12345,
  "request_id": "req_abc123",
  "model_id": 1,
  "user_id": 456,
  "input_tokens": 150,
  "output_tokens": 200,
  "processing_time_ms": 1234,
  "status": "completed",
  "error_message": null,
  "created_at": "2026-01-04T10:30:00Z",
  "models": { ... full model object ... }
}
```

**For 10,000 requests:**
- Payload size: ~5 MB
- Transfer time: 5-10 seconds (slow connection)
- Parse time: 1-2 seconds

### New Approach (compressed arrays)

```json
// Total = ~50 bytes per request
{
  "tokens": [350, 420, 180, ...],      // ~4 bytes per number
  "latency": [1234, 890, 567, ...],    // ~4 bytes per number
  "timestamps": ["2026-01-04...", ...] // ~25 bytes per timestamp
}
```

**For 10,000 requests:**
- Payload size: ~500 KB
- Transfer time: 0.5-1 second
- Parse time: 0.1 seconds

**Improvement: 90% smaller, 10x faster!** 🚀

## Migration Guide

### Before (Old Endpoint)

```typescript
// Fetching full requests
const response = await fetch('/api/monitoring/chat-requests?model_id=1&limit=10000');
const { data } = await response.json();

// Plotting
const plotData = data.map(req => ({
  x: req.input_tokens + req.output_tokens,
  y: req.processing_time_ms
}));
```

### After (New Endpoint)

```typescript
// Fetching optimized data
const response = await fetch('/api/monitoring/chat-requests/plot-data?model_id=1');
const { recent_requests, plot_data } = await response.json();

// Display recent requests table
renderTable(recent_requests);

// Plotting (90% smaller payload!)
const plotData = plot_data.tokens.map((tokens, i) => ({
  x: tokens,
  y: plot_data.latency[i]
}));
```

## Notes

1. **Arrays are aligned by index** - `plot_data.tokens[i]`, `plot_data.latency[i]`, and `plot_data.timestamps[i]` all refer to the same request.

2. **Chronological order** - Data is sorted by `created_at` ascending (oldest first) for time-series plotting.

3. **Recent requests are separate** - Last 10 requests are in `recent_requests` with full details for display.

4. **No pagination on plot_data** - ALL filtered requests are returned in the compressed arrays (efficient enough to send all at once).

5. **Filters apply to both** - `model_id`, `provider_id`, `start_date`, and `end_date` filters apply to both recent requests and plot data.

## API Endpoints Summary

| Endpoint | Use Case | Data Returned |
|----------|----------|---------------|
| `/api/monitoring/chat-requests` | Full request details with pagination | Full objects, paginated |
| `/api/monitoring/chat-requests/plot-data` | **Plotting + Recent display** | Recent 10 full + ALL compressed |
| `/api/monitoring/chat-requests/models` | Model statistics | Aggregated stats only |

## Questions?

For issues or questions:
1. Check the response `metadata.format_version` to ensure compatibility
2. Verify arrays have the same length
3. Check `metadata.total_count` to see how many data points you're plotting
