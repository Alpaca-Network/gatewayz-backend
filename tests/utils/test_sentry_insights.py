"""
Tests for Sentry Insights instrumentation module.

Tests cover:
- Database Query Insights (trace_database_query, trace_supabase_query)
- Cache Insights (trace_cache_operation, CacheSpanTracker)
- Queue Monitoring (trace_queue_publish, trace_queue_process, QueueTracker)
"""

from unittest.mock import Mock, patch


def create_mock_sentry_span():
    """Create a mock Sentry span with proper context manager support.

    Returns a mock span that can track set_data calls and behaves
    like a real Sentry span context manager.
    """
    mock_span = Mock()
    mock_span.set_data = Mock()
    mock_span.set_status = Mock()
    return mock_span


def setup_mock_sentry(mock_sentry, mock_span=None):
    """Configure a mock sentry_sdk with proper context manager behavior.

    Args:
        mock_sentry: The patched sentry_sdk module
        mock_span: Optional pre-configured span mock

    Returns:
        The configured mock_span
    """
    if mock_span is None:
        mock_span = create_mock_sentry_span()

    mock_sentry.is_initialized.return_value = True
    mock_sentry.start_span.return_value.__enter__ = Mock(return_value=mock_span)
    mock_sentry.start_span.return_value.__exit__ = Mock(return_value=None)

    return mock_span


class TestDatabaseQueryInsights:
    """Tests for database query span instrumentation."""

    def test_trace_database_query_creates_span(self):
        """Test that trace_database_query creates a Sentry span with correct attributes."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import trace_database_query

            with trace_database_query(
                "SELECT * FROM users WHERE id = ?",
                db_system="postgresql",
                table="users",
                operation="select",
            ):
                pass

            # Verify span was created
            mock_sentry.start_span.assert_called_once()
            call_kwargs = mock_sentry.start_span.call_args[1]
            assert call_kwargs["op"] == "db.postgresql"
            # Note: sentry-sdk 2.0.0 uses 'description', newer versions use 'name'
            assert call_kwargs["description"] == "SELECT * FROM users WHERE id = ?"

    def test_trace_database_query_sets_required_attributes(self):
        """Test that db.system attribute is set for Sentry Queries Insights."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import trace_database_query

            with trace_database_query(
                "SELECT * FROM users",
                db_system="postgresql",
                db_name="mydb",
                table="users",
                operation="select",
            ):
                pass

            # Verify db.system was set (required by Sentry)
            mock_span.set_data.assert_any_call("db.system", "postgresql")
            mock_span.set_data.assert_any_call("db.name", "mydb")
            mock_span.set_data.assert_any_call("db.sql.table", "users")

    def test_trace_supabase_query_convenience_wrapper(self):
        """Test that trace_supabase_query correctly wraps trace_database_query."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import trace_supabase_query

            with trace_supabase_query("users", "select", filters={"email": "test@test.com"}):
                pass

            # Verify it was configured for PostgreSQL/Supabase
            mock_span.set_data.assert_any_call("db.system", "postgresql")
            mock_span.set_data.assert_any_call("db.name", "supabase")

    def test_trace_database_query_yields_none_when_sentry_unavailable(self):
        """Test graceful degradation when Sentry is not available."""
        with patch("src.utils.sentry_insights.SENTRY_AVAILABLE", False):
            from src.utils.sentry_insights import trace_database_query

            with trace_database_query("SELECT * FROM users") as span:
                assert span is None


class TestCacheInsights:
    """Tests for cache operation span instrumentation."""

    def test_trace_cache_operation_get_hit(self):
        """Test cache.get span with cache hit."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import trace_cache_operation

            with trace_cache_operation(
                "cache.get",
                "user:123",
                cache_hit=True,
                item_size=256,
            ):
                pass

            # Verify span attributes
            mock_sentry.start_span.assert_called_once()
            call_kwargs = mock_sentry.start_span.call_args[1]
            assert call_kwargs["op"] == "cache.get"
            # Note: sentry-sdk 2.0.0 uses 'description', newer versions use 'name'
            assert call_kwargs["description"] == "user:123"

            # Verify cache.hit and cache.key were set
            mock_span.set_data.assert_any_call("cache.key", ["user:123"])
            mock_span.set_data.assert_any_call("cache.hit", True)
            mock_span.set_data.assert_any_call("cache.item_size", 256)

    def test_trace_cache_operation_get_miss(self):
        """Test cache.get span with cache miss."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import trace_cache_operation

            with trace_cache_operation("cache.get", "user:456", cache_hit=False):
                pass

            mock_span.set_data.assert_any_call("cache.hit", False)

    def test_trace_cache_operation_put_with_ttl(self):
        """Test cache.put span with TTL."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import trace_cache_operation

            with trace_cache_operation(
                "cache.put",
                "session:abc",
                item_size=1024,
                ttl=3600,
            ):
                pass

            call_kwargs = mock_sentry.start_span.call_args[1]
            assert call_kwargs["op"] == "cache.put"

            mock_span.set_data.assert_any_call("cache.item_size", 1024)
            mock_span.set_data.assert_any_call("cache.ttl", 3600)

    def test_trace_cache_operation_normalizes_op(self):
        """Test that operation names are normalized to cache.* format."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import trace_cache_operation

            # Test without cache. prefix
            with trace_cache_operation("get", "key1", cache_hit=True):
                pass

            call_kwargs = mock_sentry.start_span.call_args[1]
            assert call_kwargs["op"] == "cache.get"

    def test_trace_cache_operation_multiple_keys(self):
        """Test cache operation with multiple keys."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import trace_cache_operation

            with trace_cache_operation(
                "cache.get",
                ["user:1", "user:2", "user:3"],
                cache_hit=True,
            ):
                pass

            call_kwargs = mock_sentry.start_span.call_args[1]
            # Note: sentry-sdk 2.0.0 uses 'description', newer versions use 'name'
            assert call_kwargs["description"] == "user:1, user:2, user:3"

            mock_span.set_data.assert_any_call("cache.key", ["user:1", "user:2", "user:3"])

    def test_cache_span_tracker_get(self):
        """Test CacheSpanTracker.get() with automatic hit detection."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import CacheSpanTracker

            tracker = CacheSpanTracker(cache_system="redis", host="localhost", port=6379)

            with tracker.get("test_key") as wrapper:
                # Simulate cache hit
                wrapper.set_result("some_value")

            mock_span.set_data.assert_any_call("cache.hit", True)

    def test_cache_span_tracker_get_miss(self):
        """Test CacheSpanTracker.get() with cache miss."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import CacheSpanTracker

            tracker = CacheSpanTracker(cache_system="redis")

            with tracker.get("missing_key") as wrapper:
                # Simulate cache miss
                wrapper.set_result(None)

            mock_span.set_data.assert_any_call("cache.hit", False)


class TestQueueMonitoring:
    """Tests for queue producer/consumer span instrumentation."""

    def test_trace_queue_publish_creates_span(self):
        """Test that trace_queue_publish creates a producer span."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)
            mock_sentry.get_traceparent.return_value = "00-trace-id-01"
            mock_sentry.get_baggage.return_value = "sentry-key=value"

            from src.utils.sentry_insights import trace_queue_publish

            with trace_queue_publish(
                "notifications",
                message_id="msg-123",
                message_body_size=256,
                messaging_system="redis",
            ) as (span, headers):
                # Verify trace headers are returned
                assert headers["sentry-trace"] == "00-trace-id-01"
                assert headers["baggage"] == "sentry-key=value"

            call_kwargs = mock_sentry.start_span.call_args[1]
            assert call_kwargs["op"] == "queue.publish"
            # Note: sentry-sdk 2.0.0 uses 'description', newer versions use 'name'
            assert call_kwargs["description"] == "notifications"

            mock_span.set_data.assert_any_call("messaging.destination.name", "notifications")
            mock_span.set_data.assert_any_call("messaging.system", "redis")
            mock_span.set_data.assert_any_call("messaging.message.id", "msg-123")
            mock_span.set_data.assert_any_call("messaging.message.body.size", 256)

    def test_trace_queue_process_creates_span(self):
        """Test that trace_queue_process creates a consumer span."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import trace_queue_process

            with trace_queue_process(
                "notifications",
                message_id="msg-123",
                message_body_size=256,
                retry_count=2,
                receive_latency_ms=150.5,
                messaging_system="redis",
            ):
                pass

            call_kwargs = mock_sentry.start_span.call_args[1]
            assert call_kwargs["op"] == "queue.process"
            # Note: sentry-sdk 2.0.0 uses 'description', newer versions use 'name'
            assert call_kwargs["description"] == "notifications"

            mock_span.set_data.assert_any_call("messaging.destination.name", "notifications")
            mock_span.set_data.assert_any_call("messaging.message.retry.count", 2)
            mock_span.set_data.assert_any_call("messaging.message.receive.latency", 150.5)

    def test_trace_queue_process_with_trace_headers(self):
        """Test that trace_queue_process continues trace from producer."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_sentry.is_initialized.return_value = True
            mock_transaction = Mock()
            mock_span = create_mock_sentry_span()
            mock_sentry.continue_trace.return_value = mock_transaction
            mock_sentry.start_transaction.return_value.__enter__ = Mock(
                return_value=mock_transaction
            )
            mock_sentry.start_transaction.return_value.__exit__ = Mock(return_value=None)
            mock_sentry.start_span.return_value.__enter__ = Mock(return_value=mock_span)
            mock_sentry.start_span.return_value.__exit__ = Mock(return_value=None)

            from src.utils.sentry_insights import trace_queue_process

            trace_headers = {
                "sentry-trace": "00-trace-id-01",
                "baggage": "sentry-key=value",
            }

            with trace_queue_process(
                "notifications",
                message_id="msg-123",
                trace_headers=trace_headers,
            ):
                pass

            # Verify continue_trace was called with headers
            mock_sentry.continue_trace.assert_called_once_with(
                trace_headers,
                op="queue.process",
                name="notifications",
            )

    def test_queue_tracker_publish(self):
        """Test QueueTracker.publish() convenience method."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)
            mock_sentry.get_traceparent.return_value = "trace-header"
            mock_sentry.get_baggage.return_value = "baggage-header"

            from src.utils.sentry_insights import QueueTracker

            tracker = QueueTracker(messaging_system="kafka")

            with tracker.publish("events", message_id="evt-1") as (span, headers):
                assert "sentry-trace" in headers
                assert "baggage" in headers

    def test_queue_tracker_process(self):
        """Test QueueTracker.process() convenience method."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import QueueTracker

            tracker = QueueTracker(messaging_system="kafka")

            with tracker.process("events", message_id="evt-1", retry_count=0):
                pass

            mock_span.set_data.assert_any_call("messaging.system", "kafka")


class TestGracefulDegradation:
    """Tests for graceful degradation when Sentry is unavailable."""

    def test_all_functions_work_without_sentry(self):
        """Test that all instrumentation functions work when Sentry is not available."""
        with patch("src.utils.sentry_insights.SENTRY_AVAILABLE", False):
            from src.utils.sentry_insights import (
                CacheSpanTracker,
                QueueTracker,
                trace_cache_operation,
                trace_database_query,
                trace_queue_process,
                trace_queue_publish,
            )

            # All should yield None or empty dict without errors
            with trace_database_query("SELECT 1") as span:
                assert span is None

            with trace_cache_operation("cache.get", "key", cache_hit=True) as span:
                assert span is None

            with trace_queue_publish("queue") as (span, headers):
                assert span is None
                assert headers == {}

            with trace_queue_process("queue") as span:
                assert span is None

    def test_sentry_not_initialized(self):
        """Test graceful handling when Sentry SDK is installed but not initialized."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_sentry.is_initialized.return_value = False

            from src.utils.sentry_insights import trace_database_query

            with trace_database_query("SELECT 1") as span:
                assert span is None


class TestDecorators:
    """Tests for instrumentation decorators."""

    def test_instrument_db_operation_decorator(self):
        """Test @instrument_db_operation decorator."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import instrument_db_operation

            @instrument_db_operation("users", "select")
            def get_user(user_id: str):
                return {"id": user_id, "name": "Test"}

            result = get_user("123")

            assert result == {"id": "123", "name": "Test"}
            mock_sentry.start_span.assert_called_once()

    def test_instrument_db_operation_async_decorator(self):
        """Test @instrument_db_operation decorator with async function."""
        import asyncio

        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import instrument_db_operation

            @instrument_db_operation("users", "insert")
            async def create_user(username: str):
                return {"username": username}

            result = asyncio.get_event_loop().run_until_complete(create_user("testuser"))

            assert result == {"username": "testuser"}
            mock_sentry.start_span.assert_called_once()

    def test_instrument_cache_operation_decorator(self):
        """Test @instrument_cache_operation decorator."""
        with patch("src.utils.sentry_insights.sentry_sdk") as mock_sentry:
            mock_span = setup_mock_sentry(mock_sentry)

            from src.utils.sentry_insights import instrument_cache_operation

            @instrument_cache_operation("get", key_param="cache_key")
            def get_cached_value(cache_key: str):
                return f"value_for_{cache_key}"

            result = get_cached_value("my_key")

            assert result == "value_for_my_key"
            mock_sentry.start_span.assert_called_once()
