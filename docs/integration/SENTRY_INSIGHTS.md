# Sentry Insights Backend Integration

This document describes the Sentry Insights instrumentation for the Gatewayz API, enabling advanced observability features in Sentry's Performance Monitoring dashboard.

## Overview

Gatewayz integrates with three Sentry Insights features:

1. **Queries Insights** - Monitor database query performance
2. **Cache Insights** - Track cache operations with hit/miss rates
3. **Queue Monitoring** - Observe message queue producers and consumers

## Quick Start

The instrumentation is already integrated with existing services. To use the utilities directly:

```python
from src.utils.sentry_insights import (
    trace_database_query,
    trace_supabase_query,
    trace_cache_operation,
    trace_queue_publish,
    trace_queue_process,
    CacheSpanTracker,
    QueueTracker,
)
```

## Database Query Insights

### Automatic Instrumentation

The existing `track_database_query` context manager in `prometheus_metrics.py` now automatically creates Sentry database spans:

```python
from src.services.prometheus_metrics import track_database_query

# Automatically creates both Prometheus metrics and Sentry spans
with track_database_query(table="users", operation="select"):
    result = client.table("users").select("*").execute()
```

### Manual Instrumentation

For direct control over database spans:

```python
from src.utils.sentry_insights import trace_database_query, trace_supabase_query

# Generic database query
with trace_database_query(
    "SELECT * FROM users WHERE email = ?",
    db_system="postgresql",
    db_name="mydb",
    table="users",
    operation="select"
):
    result = execute_query(query, [email])

# Supabase-specific (convenience wrapper)
with trace_supabase_query("users", "select", filters={"email": email}):
    result = supabase.table("users").select("*").eq("email", email).execute()
```

### Required Span Attributes

| Attribute | Type | Description | Required |
|-----------|------|-------------|----------|
| `db.system` | string | Database system (postgresql, mysql, etc.) | Yes |
| `db.name` | string | Database name | No |
| `db.sql.table` | string | Table being queried | No |
| `db.operation` | string | Operation type (select, insert, update, delete) | No |

## Cache Insights

### Automatic Instrumentation

Response cache operations in `response_cache.py` automatically create Sentry cache spans.

### Manual Instrumentation

```python
from src.utils.sentry_insights import trace_cache_operation, CacheSpanTracker

# Basic cache operation
with trace_cache_operation(
    "cache.get",
    "user:123",
    cache_hit=True,
    item_size=256,
    ttl=3600,
    cache_system="redis"
):
    pass  # Your cache logic here

# Using CacheSpanTracker for automatic hit detection
tracker = CacheSpanTracker(cache_system="redis", host="localhost", port=6379)

with tracker.get("user:123") as wrapper:
    value = redis.get("user:123")
    wrapper.set_result(value)  # Automatically sets cache_hit based on value
    if value:
        wrapper.set_size(len(value))

# Cache put
with tracker.put("user:123", ttl=3600, item_size=256):
    redis.setex("user:123", 3600, serialized_data)
```

### Required Span Attributes

| Attribute | Type | Description | Required for |
|-----------|------|-------------|--------------|
| `cache.key` | string[] | Cache key(s) | All operations |
| `cache.hit` | boolean | Whether cache was hit | cache.get |
| `cache.item_size` | int | Item size in bytes | Optional |
| `cache.ttl` | int | Time-to-live in seconds | Optional |
| `network.peer.address` | string | Cache server hostname | Optional |
| `network.peer.port` | int | Cache server port | Optional |

## Queue Monitoring

### Producer (Publishing)

```python
from src.utils.sentry_insights import trace_queue_publish, QueueTracker

# Basic producer
with trace_queue_publish(
    "notifications",
    message_id="msg-123",
    message_body_size=256,
    messaging_system="redis"
) as (span, headers):
    # Include headers in message for trace continuity
    message = {
        "data": payload,
        "headers": headers  # Contains sentry-trace and baggage
    }
    await queue.publish("notifications", message)

# Using QueueTracker
tracker = QueueTracker(messaging_system="redis")

with tracker.publish("events", message_id="evt-1") as (span, headers):
    await publish_message({"data": payload, "headers": headers})
```

### Consumer (Processing)

```python
from src.utils.sentry_insights import trace_queue_process

# Extract trace headers from message
trace_headers = {
    "sentry-trace": message.get("headers", {}).get("sentry-trace"),
    "baggage": message.get("headers", {}).get("baggage"),
}

with trace_queue_process(
    "notifications",
    message_id="msg-123",
    message_body_size=256,
    retry_count=0,
    receive_latency_ms=150.5,
    messaging_system="redis",
    trace_headers=trace_headers  # Links to producer span
) as span:
    await process_notification(message)
```

### Required Span Attributes

| Attribute | Type | Description | Required |
|-----------|------|-------------|----------|
| `messaging.destination.name` | string | Queue/topic name | Yes |
| `messaging.system` | string | Messaging system name | Yes |
| `messaging.message.id` | string | Unique message ID | No |
| `messaging.message.body.size` | int | Message size in bytes | No |
| `messaging.message.retry.count` | int | Processing retry count | No (consumer) |
| `messaging.message.receive.latency` | float | Latency in ms | No (consumer) |

## Prometheus Metrics Integration

The enhanced Prometheus metrics functions now also create Sentry spans:

```python
from src.services.prometheus_metrics import (
    track_database_query,       # Creates db spans
    record_cache_hit,           # Creates cache.get spans (hit)
    record_cache_miss,          # Creates cache.get spans (miss)
    record_cache_set,           # Creates cache.put spans
    record_cache_remove,        # Creates cache.remove spans
    track_queue_publish,        # Creates queue.publish spans
    track_queue_process,        # Creates queue.process spans
)
```

## Decorators

For simple instrumentation without context managers:

```python
from src.utils.sentry_insights import instrument_db_operation, instrument_cache_operation

@instrument_db_operation("users", "select")
async def get_user(user_id: str):
    return await db.fetch_one("SELECT * FROM users WHERE id = ?", user_id)

@instrument_cache_operation("get", key_param="cache_key")
def get_cached_value(cache_key: str):
    return redis.get(cache_key)
```

## Graceful Degradation

All instrumentation functions gracefully handle scenarios where Sentry is unavailable:

- If `sentry-sdk` is not installed, spans are not created
- If Sentry is not initialized (`sentry_sdk.is_initialized() == False`), spans are not created
- Exceptions during span creation are caught and logged

The application continues to function normally without Sentry instrumentation.

## References

- [Sentry Queries Insights](https://docs.sentry.io/product/insights/backend/queries/)
- [Sentry Cache Insights](https://docs.sentry.io/product/insights/backend/caches/)
- [Sentry Queue Monitoring](https://docs.sentry.io/product/insights/backend/queue-monitoring/)
- [Sentry SDK Telemetry Traces Modules - Caches](https://develop.sentry.dev/sdk/telemetry/traces/modules/caches/)
- [Sentry SDK Telemetry Traces Modules - Queues](https://develop.sentry.dev/sdk/telemetry/traces/modules/queues/)
