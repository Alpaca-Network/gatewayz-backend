# Latency Measurement Guide

## Overview

The backend now returns detailed timing information in every response, allowing clients to calculate full round-trip latency.

## Response Format

Every API response now includes a `gateway_usage` object with timing information:

```json
{
  "id": "chatcmpl-xxx",
  "choices": [...],
  "usage": {...},
  "gateway_usage": {
    "tokens_charged": 150,
    "backend_processing_ms": 1250,
    "backend_received_at": 1702934567890,
    "backend_responded_at": 1702934569140,
    "cost_usd": 0.0025,
    "request_ms": 1250  // Legacy field (same as backend_processing_ms)
  }
}
```

### Fields Explained

- **`backend_processing_ms`**: How long the backend took to process your request (in milliseconds)
- **`backend_received_at`**: Unix timestamp (ms) when backend received the request
- **`backend_responded_at`**: Unix timestamp (ms) when backend finished processing
- **`tokens_charged`**: Total tokens used
- **`cost_usd`**: Cost in USD (only for paid users)
- **`request_ms`**: (Legacy) Same as `backend_processing_ms`

## Client-Side Implementation

### JavaScript/TypeScript

```javascript
// Measure full round-trip latency
const start = Date.now();

const response = await fetch('https://your-api.com/v1/chat/completions', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer YOUR_API_KEY',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    model: 'gpt-4',
    messages: [{role: 'user', content: 'Hello'}]
  })
});

const end = Date.now();
const data = await response.json();

// Calculate latencies
const totalLatency = end - start;
const backendProcessing = data.gateway_usage.backend_processing_ms;
const networkLatency = totalLatency - backendProcessing;

console.log({
  total_latency_ms: totalLatency,
  backend_processing_ms: backendProcessing,
  network_latency_ms: networkLatency,
  network_percentage: ((networkLatency / totalLatency) * 100).toFixed(1) + '%'
});

// Example output:
// {
//   total_latency_ms: 1450,
//   backend_processing_ms: 1250,
//   network_latency_ms: 200,
//   network_percentage: '13.8%'
// }
```

### Python

```python
import time
import requests

# Measure full round-trip latency
start = time.time()

response = requests.post(
    'https://your-api.com/v1/chat/completions',
    headers={'Authorization': 'Bearer YOUR_API_KEY'},
    json={
        'model': 'gpt-4',
        'messages': [{'role': 'user', 'content': 'Hello'}]
    }
)

end = time.time()
data = response.json()

# Calculate latencies
total_latency = (end - start) * 1000  # Convert to ms
backend_processing = data['gateway_usage']['backend_processing_ms']
network_latency = total_latency - backend_processing

print(f"""
Total latency: {total_latency:.2f}ms
Backend processing: {backend_processing}ms
Network latency: {network_latency:.2f}ms
Network percentage: {(network_latency / total_latency * 100):.1f}%
""")
```

### cURL + jq

```bash
#!/bin/bash

START=$(date +%s%3N)

RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST https://your-api.com/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello"}]
  }')

END=$(date +%s%3N)

# Parse response
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

TOTAL_LATENCY=$((END - START))
BACKEND_MS=$(echo "$BODY" | jq -r '.gateway_usage.backend_processing_ms')
NETWORK_LATENCY=$((TOTAL_LATENCY - BACKEND_MS))

echo "Total latency: ${TOTAL_LATENCY}ms"
echo "Backend processing: ${BACKEND_MS}ms"
echo "Network latency: ${NETWORK_LATENCY}ms"
```

## Advanced: Time-Based Verification

You can verify the backend timestamps match your measurements:

```javascript
const clientSent = Date.now();
const response = await fetch(...);
const clientReceived = Date.now();
const data = await response.json();

const backendReceived = data.gateway_usage.backend_received_at;
const backendResponded = data.gateway_usage.backend_responded_at;

console.log({
  // Client perspective
  client_total: clientReceived - clientSent,

  // Backend perspective
  backend_processing: backendResponded - backendReceived,

  // Network latencies (approximate)
  network_inbound: backendReceived - clientSent,
  network_outbound: clientReceived - backendResponded,

  // Total network latency
  network_total: (clientReceived - clientSent) - (backendResponded - backendReceived)
});
```

## Monitoring & Analytics

### Track Latency Over Time

```javascript
// Store latency data for monitoring
function recordLatency(data, totalLatency) {
  const metrics = {
    timestamp: Date.now(),
    model: data.model,
    total_latency_ms: totalLatency,
    backend_processing_ms: data.gateway_usage.backend_processing_ms,
    network_latency_ms: totalLatency - data.gateway_usage.backend_processing_ms,
    tokens: data.gateway_usage.tokens_charged,
    cost: data.gateway_usage.cost_usd || 0
  };

  // Send to your analytics service
  analytics.track('api_request', metrics);

  // Or store in local database
  db.latency_metrics.insert(metrics);
}
```

### Alert on High Latency

```javascript
function checkLatency(totalLatency, backendLatency) {
  const networkLatency = totalLatency - backendLatency;

  // Alert if total latency > 5 seconds
  if (totalLatency > 5000) {
    alert(`High total latency: ${totalLatency}ms`);
  }

  // Alert if network latency > 2 seconds (may indicate connectivity issues)
  if (networkLatency > 2000) {
    alert(`High network latency: ${networkLatency}ms - Check connection`);
  }

  // Alert if backend processing > 10 seconds (may indicate model slowdown)
  if (backendLatency > 10000) {
    alert(`High backend latency: ${backendLatency}ms - Model may be overloaded`);
  }
}
```

## Streaming Responses

For streaming responses, timing works differently as chunks arrive incrementally. The `gateway_usage` object is included in the final `[DONE]` event or can be tracked via the stream normalizer.

## Best Practices

1. **Measure on the client side**: Always measure total latency from the client for accurate results
2. **Account for clock skew**: Don't rely on absolute timestamp comparisons between client and server
3. **Use relative timing**: Compare `backend_processing_ms` vs total latency for network overhead
4. **Monitor trends**: Track latency over time to identify degradation
5. **Set alerts**: Configure alerts for abnormal latency spikes
6. **Separate concerns**: Distinguish between network issues vs backend performance

## Example: Full Integration

```javascript
class APIClient {
  constructor(apiKey) {
    this.apiKey = apiKey;
    this.baseURL = 'https://your-api.com';
  }

  async chat(messages, model = 'gpt-4') {
    const start = performance.now();

    try {
      const response = await fetch(`${this.baseURL}/v1/chat/completions`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ model, messages })
      });

      const end = performance.now();
      const data = await response.json();

      // Add timing metadata
      data.client_timing = {
        total_latency_ms: Math.round(end - start),
        backend_processing_ms: data.gateway_usage.backend_processing_ms,
        network_latency_ms: Math.round((end - start) - data.gateway_usage.backend_processing_ms),
        measured_at: new Date().toISOString()
      };

      // Log metrics
      this.logMetrics(data.client_timing);

      return data;

    } catch (error) {
      const end = performance.now();
      console.error('Request failed:', {
        error: error.message,
        latency_ms: Math.round(end - start)
      });
      throw error;
    }
  }

  logMetrics(timing) {
    console.log('[API Metrics]', {
      total: `${timing.total_latency_ms}ms`,
      backend: `${timing.backend_processing_ms}ms`,
      network: `${timing.network_latency_ms}ms`,
      network_pct: `${((timing.network_latency_ms / timing.total_latency_ms) * 100).toFixed(1)}%`
    });
  }
}

// Usage
const client = new APIClient('your-api-key');
const response = await client.chat([
  {role: 'user', content: 'Hello'}
]);

console.log('Response:', response.choices[0].message.content);
console.log('Timing:', response.client_timing);
```

## Troubleshooting

### High Network Latency
- Check client's internet connection
- Try from different geographic location
- Check if using VPN/proxy
- Verify DNS resolution speed

### High Backend Processing Time
- Check model size (larger models take longer)
- Verify request complexity (longer prompts = more processing)
- Check for provider-side rate limits
- Monitor provider status pages

### Timestamps Don't Match
- This is normal due to clock skew between client and server
- Always use relative timing (`backend_processing_ms`) instead of absolute timestamps
- Use timestamps only for debugging, not for latency calculation
