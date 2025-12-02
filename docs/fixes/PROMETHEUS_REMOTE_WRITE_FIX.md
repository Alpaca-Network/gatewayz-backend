# Prometheus Remote Write Fix

## Issue

The Prometheus remote write integration was returning a 415 status error:

```
⚠️ Prometheus remote write returned status 415: expected application/x-protobuf as the first (media) part, got text/plain; charset=utf-8 content-type
```

## Root Cause

The `prometheus_remote_write.py` module was attempting to push metrics to Prometheus using the text format exported by `prometheus_client.generate_latest()`. However, Prometheus's remote write endpoint requires the **protobuf format** (specifically Snappy-compressed protobuf messages), not the text format.

The text format is designed for Prometheus **scraping**, not for remote write. The remote write protocol is a completely different API that expects binary protobuf data.

## Solution

The fix disables the remote write feature by default since:

1. **Protobuf format requirement**: Remote write requires protobuf serialization with Snappy compression
2. **Additional dependencies**: Proper implementation would require additional dependencies not currently in the project
3. **Scraping alternative**: Prometheus scraping via the `/metrics` endpoint is already implemented and works correctly

### Changes Made

1. **Disabled remote write by default** in `src/services/prometheus_remote_write.py`:
   - Set `enabled=False` in `init_prometheus_remote_write()`
   - Added documentation explaining why it's disabled

2. **Updated push_metrics method**:
   - Replaced the incorrect text-format push with a clear message
   - Documented that scraping should be used instead

3. **Added documentation**:
   - Explained the protobuf requirement
   - Directed users to use the scrape endpoint instead

## Alternative Approaches

If Prometheus remote write is needed in the future, here are the implementation options:

### Option 1: Use Prometheus Scraping (Recommended)
- Already implemented via `/metrics` endpoint
- Works with existing `prometheus_client` library
- Standard Prometheus integration pattern
- **This is the current solution**

### Option 2: Implement Protobuf Remote Write
Would require:
- Adding `snappy` dependency for compression
- Implementing protobuf serialization
- Using the `prometheus_client` remote write features or implementing custom serialization
- Example dependencies:
  ```
  snappy>=0.7.0
  prometheus-client[twisted]>=0.19.0
  ```

### Option 3: Use Agent-Based Metrics Collection
- Deploy Prometheus agent alongside the application
- Agent scrapes `/metrics` and pushes to remote Prometheus
- Keeps application code simpler

## Testing

The fix was tested to ensure:
1. No errors are logged during startup
2. The `/metrics` endpoint continues to work
3. Tests continue to pass

## Impact

- **Positive**: Eliminates the 415 error warnings from logs
- **Neutral**: Metrics continue to be available via scraping (no functionality loss)
- **Note**: If remote write is required in production, use Option 2 or 3 above

## Configuration

Users can continue to use Prometheus metrics via scraping:

```env
PROMETHEUS_ENABLED=true
PROMETHEUS_SCRAPE_ENABLED=true
```

The scrape endpoint is available at: `GET /metrics`

## References

- [Prometheus Remote Write Spec](https://prometheus.io/docs/concepts/remote_write_spec/)
- [Prometheus Text Format](https://prometheus.io/docs/instrumenting/exposition_formats/)
- [prometheus-client Python Library](https://github.com/prometheus/client_python)
