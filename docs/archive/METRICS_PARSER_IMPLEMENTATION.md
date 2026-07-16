# Prometheus Metrics Parser Implementation

## Overview

A new Prometheus metrics parser service has been implemented to read from the `/metrics` endpoint and return metrics in a structured JSON format. This enables easy consumption of Prometheus metrics by frontend dashboards and monitoring systems.

## Files Created

### 1. `src/services/metrics_parser.py`
Main service module containing the `PrometheusMetricsParser` class.

**Key Components:**

- **PrometheusMetricsParser class**: Parses Prometheus exposition format metrics
  - `fetch_metrics()`: Fetches raw metrics from the `/metrics` endpoint
  - `parse_metrics()`: Parses Prometheus text format and extracts structured data
  - `_compute_latency_metrics()`: Calculates percentiles and averages from histogram data
  - `_calculate_percentile()`: Computes percentile values using linear interpolation
  - `get_metrics()`: Convenience method combining fetch and parse

- **Global parser instance**: `get_metrics_parser()` function provides singleton access

## API Endpoint

### GET `/api/metrics/parsed`

Returns parsed Prometheus metrics in structured JSON format.

**Response Format:**
```json
{
  "latency": {
    "/endpoint": {
      "avg": 0.123,
      "p50": 0.1,
      "p95": 0.25,
      "p99": 0.5
    }
  },
  "requests": {
    "/endpoint": {
      "GET": 123,
      "POST": 10
    }
  },
  "errors": {
    "/endpoint": {
      "GET": 2,
      "POST": 0
    }
  }
}
```

## Metrics Extracted

### Latency Metrics
- **Source**: `http_request_latency_seconds_*` histogram metrics
- **Computed Values**:
  - `avg`: Average latency = sum / count
  - `p50`: 50th percentile (median)
  - `p95`: 95th percentile
  - `p99`: 99th percentile

**Calculation Method:**
- Uses histogram buckets to compute percentiles
- Applies linear interpolation between bucket boundaries
- Handles edge cases (empty buckets, zero counts)

### Request Counts
- **Source**: `http_requests_total` counter metrics
- **Format**: Grouped by endpoint and HTTP method
- **Example**: `{"/api/users": {"GET": 100, "POST": 50}}`

### Error Counts
- **Source**: `http_request_errors_total` counter metrics
- **Format**: Grouped by endpoint and HTTP method
- **Example**: `{"/api/users": {"GET": 5, "POST": 2}}`

## Implementation Details

### Prometheus Exposition Format Parsing

The parser uses regex patterns to extract metrics from Prometheus text format:

```
http_request_latency_seconds_bucket{endpoint="/api/test",le="0.1"} 50
http_request_latency_seconds_sum{endpoint="/api/test"} 25.5
http_request_latency_seconds_count{endpoint="/api/test"} 100
http_requests_total{endpoint="/api/test",method="GET"} 100
http_request_errors_total{endpoint="/api/test",method="GET"} 5
```

### Percentile Calculation Algorithm

1. Collects all histogram buckets sorted by boundary value
2. Calculates target count: `target_count = percentile * total_count`
3. Finds the bucket containing the target count
4. Uses linear interpolation between bucket boundaries:
   ```
   percentile_value = prev_boundary + fraction * (boundary - prev_boundary)
   where fraction = (target_count - prev_count) / (count - prev_count)
   ```

### Error Handling

- **Fetch failures**: Returns empty metrics structure with graceful degradation
- **Parse failures**: Skips malformed lines and continues parsing
- **Missing data**: Returns `None` for unavailable metrics
- **Zero counts**: Handles edge cases in percentile calculation

## Usage Examples

### Direct Python Usage

```python
from src.services.metrics_parser import get_metrics_parser

# Get parser instance
parser = get_metrics_parser("http://localhost:8000/metrics")

# Fetch and parse metrics
metrics = await parser.get_metrics()

# Access latency metrics
latency = metrics["latency"]["/api/test"]
print(f"Average latency: {latency['avg']}s")
print(f"P95 latency: {latency['p95']}s")
```

### HTTP API Usage

```bash
# Fetch parsed metrics
curl http://localhost:8000/api/metrics/parsed

# Response:
{
  "latency": {
    "/v1/chat/completions": {
      "avg": 0.234,
      "p50": 0.15,
      "p95": 0.45,
      "p99": 0.89
    }
  },
  "requests": {
    "/v1/chat/completions": {"POST": 1234}
  },
  "errors": {
    "/v1/chat/completions": {"POST": 12}
  }
}
```

## Testing

Unit tests are provided in `tests/test_metrics_parser.py`:

- `test_parse_latency_metrics`: Verifies latency metric parsing
- `test_parse_request_counts`: Verifies request count parsing
- `test_parse_error_counts`: Verifies error count parsing
- `test_parse_combined_metrics`: Tests all metric types together
- `test_percentile_calculation`: Validates percentile computation
- `test_multiple_endpoints`: Tests multi-endpoint parsing

Run tests with:
```bash
pytest tests/test_metrics_parser.py -v
```

## Integration with Main Application

The endpoint is registered in `src/main.py`:

```python
@app.get("/api/metrics/parsed", tags=["monitoring"], include_in_schema=False)
async def get_parsed_metrics():
    from src.services.metrics_parser import get_metrics_parser
    parser = get_metrics_parser("http://localhost:8000/metrics")
    metrics = await parser.get_metrics()
    return metrics
```

## Configuration

The parser uses the following defaults:
- **Metrics URL**: `http://localhost:8000/metrics` (configurable)
- **Timeout**: 10 seconds for HTTP requests
- **Metric Types**: Only extracts HTTP request metrics (latency, counts, errors)

## Performance Considerations

- **Caching**: Parser instance is cached globally via `get_metrics_parser()`
- **Async I/O**: Uses `httpx.AsyncClient` for non-blocking HTTP requests
- **Memory**: Metrics are parsed on-demand without persistent storage
- **Latency**: Typical response time < 100ms for standard workloads

## Future Enhancements

Potential improvements:
1. Add caching layer with TTL for parsed metrics
2. Support additional metric types (database, cache, provider health)
3. Add filtering by endpoint or method
4. Implement metric aggregation (sum, average across endpoints)
5. Add time-series data for trend analysis
6. Support for custom Prometheus queries

## Dependencies

- `httpx>=0.27.0`: Async HTTP client (already in requirements.txt)
- `prometheus_client`: Prometheus metrics library (already in requirements.txt)

## Troubleshooting

### Metrics endpoint not responding
- Verify `/metrics` endpoint is accessible at configured URL
- Check network connectivity and firewall rules
- Ensure Prometheus metrics are being collected

### Empty results
- Confirm metrics are being recorded by checking `/metrics` directly
- Verify endpoint labels match expected format
- Check for typos in metric names

### Percentile calculation issues
- Ensure histogram buckets are properly configured
- Verify bucket boundaries are in ascending order
- Check that bucket counts are monotonically increasing
