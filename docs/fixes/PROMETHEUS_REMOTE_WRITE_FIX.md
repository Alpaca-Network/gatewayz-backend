# Prometheus Remote Write Implementation

## Original Issue

The Prometheus remote write integration was returning a 415 status error:

```
⚠️ Prometheus remote write returned status 415: expected application/x-protobuf as the first (media) part, got text/plain; charset=utf-8 content-type
```

## Root Cause

The `prometheus_remote_write.py` module was attempting to push metrics to Prometheus using the text format exported by `prometheus_client.generate_latest()`. However, Prometheus's remote write endpoint requires the **protobuf format** (specifically Snappy-compressed protobuf messages), not the text format.

The text format is designed for Prometheus **scraping**, not for remote write. The remote write protocol is a completely different API that expects binary protobuf data.

## Solution: Protobuf Implementation

We've now implemented proper protobuf support for Prometheus remote write:

### Changes Made

1. **Added dependencies** to `requirements.txt`:
   - `python-snappy>=0.7.0` - Snappy compression for protobuf data
   - `protobuf>=4.24.0` - Protocol Buffers support

2. **Implemented protobuf serialization** in `src/services/prometheus_remote_write.py`:
   - Created `_serialize_metrics_to_protobuf()` function
   - Uses OpenMetrics format (protobuf-based) from `prometheus_client`
   - Compresses data with Snappy compression
   - Sends with proper headers:
     - `Content-Type: application/x-protobuf`
     - `Content-Encoding: snappy`
     - `X-Prometheus-Remote-Write-Version: 0.1.0`

3. **Updated push_metrics method**:
   - Properly serializes metrics to protobuf format
   - Compresses with Snappy
   - Sends to remote write endpoint via HTTP POST
   - Handles errors and tracks statistics

4. **Enabled remote write by default**:
   - Remote write is now enabled when protobuf dependencies are available
   - Automatically falls back to scraping if protobuf dependencies are missing

## How It Works

The implementation follows the Prometheus remote write protocol:

1. **Metric Collection**: Metrics are collected from the Prometheus registry (`REGISTRY`)
2. **Serialization**: Metrics are serialized to OpenMetrics protobuf format
3. **Compression**: The protobuf data is compressed using Snappy
4. **Transmission**: Compressed data is sent via HTTP POST to the remote write endpoint
5. **Statistics**: Push attempts, successes, and errors are tracked

## Fallback Behavior

If protobuf dependencies are not available:
- Remote write will be automatically disabled
- Metrics remain available via the `/metrics` scraping endpoint
- A warning is logged explaining the missing dependencies

## Alternative Approaches

### Option 1: Remote Write (Current Implementation)
- Implemented with protobuf and Snappy compression
- Metrics are actively pushed to Prometheus
- Best for distributed systems where Prometheus can't scrape all instances

### Option 2: Prometheus Scraping (Fallback)
- Always available via `/metrics` endpoint
- Works without additional dependencies
- Standard Prometheus integration pattern
- Best for environments where Prometheus can directly scrape the application

### Option 3: Agent-Based Metrics Collection
- Deploy Prometheus agent alongside the application
- Agent scrapes `/metrics` and pushes to remote Prometheus
- Keeps application code simpler
- Best for complex network topologies

## Testing

Comprehensive tests ensure:
1. Protobuf serialization works correctly
2. Snappy compression is applied
3. HTTP POST requests are properly formatted with correct headers
4. Error handling for HTTP errors and serialization failures
5. Statistics tracking (push count, errors, success rate)
6. Graceful fallback when protobuf dependencies are unavailable
7. Proper initialization and shutdown behavior

## Impact

- **Positive**:
  - Eliminates the 415 error warnings from logs
  - Enables active metrics pushing to Prometheus
  - Provides better support for distributed systems
- **Neutral**:
  - Metrics continue to be available via scraping (dual support)
- **Dependencies**:
  - Adds `python-snappy` and `protobuf` dependencies

## Configuration

Remote write is enabled by default when dependencies are available:

```env
# Enable Prometheus monitoring
PROMETHEUS_ENABLED=true

# Configure remote write endpoint (optional, has default)
PROMETHEUS_REMOTE_WRITE_URL=http://prometheus:9090/api/v1/write
```

Metrics are available via both:
1. **Remote Write** (push): Metrics are pushed every 30 seconds
2. **Scraping** (pull): Available at `GET /metrics`

## References

- [Prometheus Remote Write Spec](https://prometheus.io/docs/concepts/remote_write_spec/)
- [Prometheus Text Format](https://prometheus.io/docs/instrumenting/exposition_formats/)
- [prometheus-client Python Library](https://github.com/prometheus/client_python)
