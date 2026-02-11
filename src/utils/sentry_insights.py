"""
Sentry Insights Instrumentation Module

This module provides instrumentation utilities for Sentry's backend insights features:
- Database Queries Insights: Monitor database query performance
- Cache Insights: Monitor cache operations (Redis) with hit/miss tracking
- Queue Monitoring: Track message queue producers and consumers

Usage:
    from src.utils.sentry_insights import (
        trace_database_query,
        trace_cache_operation,
        trace_queue_publish,
        trace_queue_process,
    )

    # Database queries
    async with trace_database_query("SELECT * FROM users", db_system="postgresql"):
        result = await db.fetch_all(query)

    # Cache operations
    async with trace_cache_operation("cache.get", "user:123", cache_hit=True):
        value = await redis.get("user:123")

    # Queue operations
    with trace_queue_publish("notifications", message_id="msg-123"):
        await queue.publish(message)

References:
    - https://docs.sentry.io/product/insights/backend/queries/
    - https://docs.sentry.io/product/insights/backend/caches/
    - https://docs.sentry.io/product/insights/backend/queue-monitoring/
    - https://develop.sentry.dev/sdk/telemetry/traces/modules/caches/
    - https://develop.sentry.dev/sdk/telemetry/traces/modules/queues/
"""

import logging
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# Try to import sentry_sdk
try:
    import sentry_sdk

    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False
    logger.warning("Sentry SDK not available - insights instrumentation disabled")


# =============================================================================
# Database Query Insights
# =============================================================================


@contextmanager
def trace_database_query(
    query: str,
    *,
    db_system: str = "postgresql",
    db_name: str | None = None,
    table: str | None = None,
    operation: str | None = None,
):
    """
    Create a Sentry span for database query monitoring.

    This enables Sentry's Queries Insights feature by creating properly
    formatted database spans with the required attributes.

    Args:
        query: The parameterized SQL query string (e.g., "SELECT * FROM users WHERE id = ?")
        db_system: Database system identifier (postgresql, mysql, mongodb, redis, etc.)
        db_name: Optional database name
        table: Optional table name for additional context
        operation: Optional operation type (select, insert, update, delete)

    Yields:
        Sentry span object for the database operation

    Example:
        with trace_database_query(
            "SELECT * FROM users WHERE email = ?",
            db_system="postgresql",
            table="users",
            operation="select"
        ):
            result = db.execute(query, [email])
    """
    if not SENTRY_AVAILABLE or not sentry_sdk.is_initialized():
        yield None
        return

    # Determine span operation based on query or explicit operation
    span_op = f"db.{db_system}"

    with sentry_sdk.start_span(
        op=span_op,
        description=query,
        origin="manual",
    ) as span:
        # Set required db.system attribute for Sentry Queries Insights
        span.set_data("db.system", db_system)

        # Set optional attributes
        if db_name:
            span.set_data("db.name", db_name)

        if table:
            span.set_data("db.sql.table", table)

        if operation:
            span.set_data("db.operation", operation)

        yield span


def trace_supabase_query(
    table: str,
    operation: str,
    *,
    query_description: str | None = None,
    filters: dict[str, Any] | None = None,
):
    """
    Create a Sentry span for Supabase PostgREST queries.

    This is a convenience wrapper for trace_database_query specifically
    designed for Supabase/PostgREST operations.

    Args:
        table: The Supabase table being queried
        operation: The operation type (select, insert, update, delete, upsert)
        query_description: Optional human-readable query description
        filters: Optional dictionary of filter conditions for context

    Returns:
        Context manager for the database span

    Example:
        with trace_supabase_query("users", "select", filters={"email": email}):
            result = supabase.table("users").select("*").eq("email", email).execute()
    """
    # Build a descriptive query string for Sentry
    if query_description:
        query = query_description
    else:
        query = f"{operation.upper()} FROM {table}"
        if filters:
            filter_str = ", ".join(f"{k}=?" for k in filters.keys())
            query = f"{query} WHERE {filter_str}"

    return trace_database_query(
        query,
        db_system="postgresql",
        db_name="supabase",
        table=table,
        operation=operation,
    )


# =============================================================================
# Cache Insights
# =============================================================================


@contextmanager
def trace_cache_operation(
    operation: str,
    key: str | list[str],
    *,
    cache_hit: bool | None = None,
    item_size: int | None = None,
    ttl: int | None = None,
    cache_system: str = "redis",
    host: str | None = None,
    port: int | None = None,
):
    """
    Create a Sentry span for cache operation monitoring.

    This enables Sentry's Cache Insights feature by creating properly
    formatted cache spans with the required attributes.

    Args:
        operation: Cache operation type (get, put, remove, flush)
            - "get" or "cache.get": Read from cache
            - "put" or "cache.put": Write to cache
            - "remove" or "cache.remove": Delete from cache
            - "flush" or "cache.flush": Clear entire cache
        key: Cache key or list of keys being operated on
        cache_hit: For get operations, whether the cache was hit (True) or missed (False)
        item_size: Size of the cached item in bytes (optional)
        ttl: Time-to-live for the cached item in seconds (optional)
        cache_system: Cache system name (redis, memcached, etc.)
        host: Cache server hostname (optional)
        port: Cache server port (optional)

    Yields:
        Sentry span object for the cache operation

    Example:
        with trace_cache_operation("cache.get", "user:123", cache_hit=True, item_size=256):
            value = redis.get("user:123")
    """
    if not SENTRY_AVAILABLE or not sentry_sdk.is_initialized():
        yield None
        return

    # Normalize operation to Sentry format
    if not operation.startswith("cache."):
        operation = f"cache.{operation}"

    # Normalize key to list for consistent handling
    keys = key if isinstance(key, list) else [key]
    key_description = ", ".join(keys)

    with sentry_sdk.start_span(
        op=operation,
        description=key_description,
        origin="manual",
    ) as span:
        # Set cache.key as array (required by Sentry spec)
        span.set_data("cache.key", keys)

        # Set cache.hit for get operations (required for cache.get)
        if operation == "cache.get" and cache_hit is not None:
            span.set_data("cache.hit", cache_hit)

        # Set optional attributes
        if item_size is not None:
            span.set_data("cache.item_size", item_size)

        if ttl is not None:
            span.set_data("cache.ttl", ttl)

        # Network peer information (optional)
        if host:
            span.set_data("network.peer.address", host)
        if port:
            span.set_data("network.peer.port", port)

        yield span


class CacheSpanTracker:
    """
    Helper class to track cache operations with automatic hit/miss detection.

    This class wraps cache operations and automatically determines hit/miss
    status based on the return value.

    Example:
        tracker = CacheSpanTracker(host="localhost", port=6379)

        # Automatic hit/miss detection based on return value
        with tracker.get("user:123") as span:
            value = redis.get("user:123")
            span.set_result(value)  # Automatically sets cache_hit based on value
    """

    def __init__(
        self,
        cache_system: str = "redis",
        host: str | None = None,
        port: int | None = None,
    ):
        self.cache_system = cache_system
        self.host = host
        self.port = port

    @contextmanager
    def get(self, key: str | list[str]):
        """Track a cache get operation with automatic hit detection."""

        class SpanWrapper:
            def __init__(self, span):
                self.span = span
                self._hit = None

            def set_result(self, value):
                """Set hit/miss based on whether value is None/empty."""
                self._hit = value is not None
                if self.span:
                    self.span.set_data("cache.hit", self._hit)

            def set_hit(self, hit: bool):
                """Explicitly set cache hit status."""
                self._hit = hit
                if self.span:
                    self.span.set_data("cache.hit", hit)

            def set_size(self, size: int):
                """Set item size."""
                if self.span:
                    self.span.set_data("cache.item_size", size)

        with trace_cache_operation(
            "cache.get",
            key,
            cache_system=self.cache_system,
            host=self.host,
            port=self.port,
        ) as span:
            yield SpanWrapper(span)

    @contextmanager
    def put(self, key: str | list[str], ttl: int | None = None, item_size: int | None = None):
        """Track a cache put operation."""
        with trace_cache_operation(
            "cache.put",
            key,
            ttl=ttl,
            item_size=item_size,
            cache_system=self.cache_system,
            host=self.host,
            port=self.port,
        ) as span:
            yield span

    @contextmanager
    def remove(self, key: str | list[str]):
        """Track a cache remove operation."""
        with trace_cache_operation(
            "cache.remove",
            key,
            cache_system=self.cache_system,
            host=self.host,
            port=self.port,
        ) as span:
            yield span

    @contextmanager
    def flush(self):
        """Track a cache flush operation."""
        with trace_cache_operation(
            "cache.flush",
            "*",
            cache_system=self.cache_system,
            host=self.host,
            port=self.port,
        ) as span:
            yield span


# =============================================================================
# Queue Monitoring
# =============================================================================


@contextmanager
def trace_queue_publish(
    destination: str,
    *,
    message_id: str | None = None,
    message_body_size: int | None = None,
    messaging_system: str = "custom",
):
    """
    Create a Sentry span for queue producer (publish) operations.

    This enables Sentry's Queue Monitoring feature for producer side.
    The span creates trace headers that should be passed to the consumer
    for distributed tracing.

    Args:
        destination: Queue or topic name where the message is published
        message_id: Unique identifier for the message
        message_body_size: Size of the message body in bytes
        messaging_system: Messaging system name (kafka, aws_sqs, rabbitmq, celery, custom)

    Yields:
        Tuple of (span, trace_headers) where trace_headers is a dict with
        sentry-trace and baggage headers to pass to the consumer

    Example:
        with trace_queue_publish(
            "notifications",
            message_id="msg-123",
            message_body_size=256,
            messaging_system="redis"
        ) as (span, headers):
            message = {"data": "...", "headers": headers}
            await queue.publish("notifications", message)
    """
    if not SENTRY_AVAILABLE or not sentry_sdk.is_initialized():
        # Return empty headers when Sentry is not available
        yield (None, {})
        return

    with sentry_sdk.start_span(
        op="queue.publish",
        description=destination,
        origin="manual",
    ) as span:
        # Set required attributes for queue monitoring
        span.set_data("messaging.destination.name", destination)
        span.set_data("messaging.system", messaging_system)

        if message_id:
            span.set_data("messaging.message.id", message_id)

        if message_body_size is not None:
            span.set_data("messaging.message.body.size", message_body_size)

        # Get trace headers for distributed tracing
        trace_headers = {
            "sentry-trace": sentry_sdk.get_traceparent(),
            "baggage": sentry_sdk.get_baggage(),
        }

        yield (span, trace_headers)


@contextmanager
def trace_queue_process(
    destination: str,
    *,
    message_id: str | None = None,
    message_body_size: int | None = None,
    retry_count: int | None = None,
    receive_latency_ms: float | None = None,
    messaging_system: str = "custom",
    trace_headers: dict[str, str] | None = None,
):
    """
    Create a Sentry span for queue consumer (process) operations.

    This enables Sentry's Queue Monitoring feature for consumer side.
    If trace headers are provided from the producer, this will create
    a linked span for distributed tracing.

    Args:
        destination: Queue or topic name where the message was consumed from
        message_id: Unique identifier for the message
        message_body_size: Size of the message body in bytes
        retry_count: Number of processing attempts for this message
        receive_latency_ms: Milliseconds between publishing and consumer receipt
        messaging_system: Messaging system name (kafka, aws_sqs, rabbitmq, celery, custom)
        trace_headers: Dict with sentry-trace and baggage headers from producer

    Yields:
        Sentry span object for the queue process operation

    Example:
        # Extract trace headers from message
        trace_headers = {
            "sentry-trace": message.headers.get("sentry-trace"),
            "baggage": message.headers.get("baggage"),
        }

        with trace_queue_process(
            "notifications",
            message_id="msg-123",
            trace_headers=trace_headers
        ) as span:
            await process_notification(message)
            span.set_status("ok")  # or "error" on failure
    """
    if not SENTRY_AVAILABLE or not sentry_sdk.is_initialized():
        yield None
        return

    # If we have trace headers, continue the trace from producer
    if trace_headers:
        # Create a transaction that continues from the producer trace
        transaction = sentry_sdk.continue_trace(
            trace_headers,
            op="queue.process",
            name=destination,
        )

        with sentry_sdk.start_transaction(transaction):
            with sentry_sdk.start_span(
                op="queue.process",
                description=destination,
                origin="manual",
            ) as span:
                _set_queue_process_attributes(
                    span,
                    destination=destination,
                    message_id=message_id,
                    message_body_size=message_body_size,
                    retry_count=retry_count,
                    receive_latency_ms=receive_latency_ms,
                    messaging_system=messaging_system,
                )
                yield span
    else:
        # No trace headers, create a standalone span
        with sentry_sdk.start_span(
            op="queue.process",
            description=destination,
            origin="manual",
        ) as span:
            _set_queue_process_attributes(
                span,
                destination=destination,
                message_id=message_id,
                message_body_size=message_body_size,
                retry_count=retry_count,
                receive_latency_ms=receive_latency_ms,
                messaging_system=messaging_system,
            )
            yield span


def _set_queue_process_attributes(
    span,
    destination: str,
    message_id: str | None,
    message_body_size: int | None,
    retry_count: int | None,
    receive_latency_ms: float | None,
    messaging_system: str,
):
    """Helper to set queue process span attributes."""
    if span is None:
        return

    # Required attributes
    span.set_data("messaging.destination.name", destination)
    span.set_data("messaging.system", messaging_system)

    # Optional attributes
    if message_id:
        span.set_data("messaging.message.id", message_id)

    if message_body_size is not None:
        span.set_data("messaging.message.body.size", message_body_size)

    if retry_count is not None:
        span.set_data("messaging.message.retry.count", retry_count)

    if receive_latency_ms is not None:
        span.set_data("messaging.message.receive.latency", receive_latency_ms)


class QueueTracker:
    """
    Helper class to track queue operations with automatic span creation.

    This class provides a convenient interface for instrumenting
    producer and consumer operations for Sentry Queue Monitoring.

    Example:
        # Create tracker for a specific queue system
        tracker = QueueTracker(messaging_system="redis")

        # Producer side
        async with tracker.publish("notifications", message_id="123") as (span, headers):
            await redis.lpush("notifications", json.dumps({
                "data": payload,
                "headers": headers
            }))

        # Consumer side
        async with tracker.process(
            "notifications",
            message_id="123",
            trace_headers=message.get("headers")
        ) as span:
            await handle_notification(message)
    """

    def __init__(self, messaging_system: str = "custom"):
        self.messaging_system = messaging_system

    @contextmanager
    def publish(
        self,
        destination: str,
        *,
        message_id: str | None = None,
        message_body_size: int | None = None,
    ):
        """Publish a message to a queue with tracing."""
        with trace_queue_publish(
            destination,
            message_id=message_id,
            message_body_size=message_body_size,
            messaging_system=self.messaging_system,
        ) as result:
            yield result

    @contextmanager
    def process(
        self,
        destination: str,
        *,
        message_id: str | None = None,
        message_body_size: int | None = None,
        retry_count: int | None = None,
        receive_latency_ms: float | None = None,
        trace_headers: dict[str, str] | None = None,
    ):
        """Process a message from a queue with tracing."""
        with trace_queue_process(
            destination,
            message_id=message_id,
            message_body_size=message_body_size,
            retry_count=retry_count,
            receive_latency_ms=receive_latency_ms,
            messaging_system=self.messaging_system,
            trace_headers=trace_headers,
        ) as span:
            yield span


# =============================================================================
# Convenience Decorators
# =============================================================================


def instrument_db_operation(
    table: str,
    operation: str = "select",
    db_system: str = "postgresql",
):
    """
    Decorator to instrument a function as a database operation.

    Args:
        table: Database table name
        operation: Operation type (select, insert, update, delete)
        db_system: Database system identifier

    Example:
        @instrument_db_operation("users", "select")
        async def get_user_by_id(user_id: str):
            return await db.fetch_one("SELECT * FROM users WHERE id = ?", user_id)
    """
    import functools

    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            query = f"{operation.upper()} FROM {table}"
            with trace_database_query(query, db_system=db_system, table=table, operation=operation):
                return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            query = f"{operation.upper()} FROM {table}"
            with trace_database_query(query, db_system=db_system, table=table, operation=operation):
                return func(*args, **kwargs)

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def instrument_cache_operation(
    operation: str = "get",
    key_param: str = "key",
    cache_system: str = "redis",
):
    """
    Decorator to instrument a function as a cache operation.

    Args:
        operation: Cache operation type (get, put, remove)
        key_param: Name of the parameter containing the cache key
        cache_system: Cache system identifier

    Example:
        @instrument_cache_operation("get", key_param="cache_key")
        async def get_cached_value(cache_key: str):
            return await redis.get(cache_key)
    """
    import functools

    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Extract cache key from parameters
            import inspect

            sig = inspect.signature(func)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            cache_key = bound.arguments.get(key_param, "unknown")

            with trace_cache_operation(operation, cache_key, cache_system=cache_system):
                return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            import inspect

            sig = inspect.signature(func)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            cache_key = bound.arguments.get(key_param, "unknown")

            with trace_cache_operation(operation, cache_key, cache_system=cache_system):
                return func(*args, **kwargs)

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
