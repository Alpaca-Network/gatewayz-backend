"""Tests for authentication timeout fixes.

These tests validate that:
1. Query timeouts are properly handled
2. User lookups use Redis caching
3. Referral processing doesn't block auth response
4. Connection pool is monitored properly
"""

import json
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.services.auth_cache import (
    cache_user_by_privy_id,
    cache_user_by_username,
    get_cached_user_by_privy_id,
    get_cached_user_by_username,
    invalidate_user_cache,
)
from src.services.connection_pool_monitor import (
    ConnectionPoolStats,
    check_pool_health_and_warn,
    get_supabase_pool_stats,
)
from src.services.query_timeout import (
    AUTH_QUERY_TIMEOUT,
    USER_LOOKUP_TIMEOUT,
    QueryTimeoutError,
    execute_with_timeout,
    safe_query_with_timeout,
)


@pytest.mark.unit
class TestQueryTimeout:
    """Test query timeout functionality."""

    def test_execute_with_timeout_success(self):
        """Test successful execution within timeout."""

        def quick_operation():
            return {"result": "success"}

        result = execute_with_timeout(quick_operation, timeout_seconds=5)
        assert result == {"result": "success"}

    @pytest.mark.timeout(10)  # Allow extra time for this test
    def test_execute_with_timeout_exceeds_limit(self):
        """Test that operation exceeding timeout raises error."""
        import time

        def slow_operation():
            time.sleep(2)  # Sleep longer than timeout
            return {"result": "success"}

        with pytest.raises(QueryTimeoutError):
            execute_with_timeout(slow_operation, timeout_seconds=0.5)

    @pytest.mark.timeout(10)  # Allow extra time for this test
    def test_safe_query_with_timeout_fallback(self):
        """Test that fallback value is returned on timeout."""
        import time

        def slow_operation():
            time.sleep(1.5)
            return {"result": "success"}

        mock_client = Mock()
        result = safe_query_with_timeout(
            mock_client,
            "users",
            slow_operation,
            timeout_seconds=0.5,
            operation_name="test query",
            fallback_value=None,
            log_errors=False,
        )

        assert result is None

    def test_safe_query_with_timeout_exception_fallback(self):
        """Test that fallback value is returned on exception."""

        def failing_operation():
            raise ValueError("Database error")

        mock_client = Mock()
        result = safe_query_with_timeout(
            mock_client,
            "users",
            failing_operation,
            timeout_seconds=5,
            operation_name="test query",
            fallback_value={"fallback": True},
            log_errors=False,
        )

        assert result == {"fallback": True}


@pytest.mark.unit
class TestAuthCache:
    """Test authentication caching functionality."""

    @patch("src.services.auth_cache.get_redis_client")
    def test_cache_user_by_privy_id(self, mock_get_redis):
        """Test caching user by Privy ID."""
        mock_redis = Mock()
        mock_get_redis.return_value = mock_redis

        user_data = {"id": 123, "username": "testuser"}
        result = cache_user_by_privy_id("privy_123", user_data)

        assert result is True
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == "auth:privy_id:privy_123"
        assert json.loads(call_args[0][2]) == user_data

    @patch("src.services.auth_cache.get_redis_client")
    def test_get_cached_user_by_privy_id_hit(self, mock_get_redis):
        """Test retrieving cached user by Privy ID (cache hit)."""
        mock_redis = Mock()
        user_data = {"id": 123, "username": "testuser"}
        mock_redis.get.return_value = json.dumps(user_data).encode()
        mock_get_redis.return_value = mock_redis

        result = get_cached_user_by_privy_id("privy_123")

        assert result == user_data
        mock_redis.get.assert_called_once_with("auth:privy_id:privy_123")

    @patch("src.services.auth_cache.get_redis_client")
    def test_get_cached_user_by_privy_id_miss(self, mock_get_redis):
        """Test retrieving cached user by Privy ID (cache miss)."""
        mock_redis = Mock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        result = get_cached_user_by_privy_id("privy_123")

        assert result is None

    @patch("src.services.auth_cache.get_redis_client")
    def test_cache_user_by_username(self, mock_get_redis):
        """Test caching user by username."""
        mock_redis = Mock()
        mock_get_redis.return_value = mock_redis

        user_data = {"id": 123, "username": "testuser"}
        result = cache_user_by_username("testuser", user_data)

        assert result is True
        mock_redis.setex.assert_called_once()

    @patch("src.services.auth_cache.get_redis_client")
    def test_invalidate_user_cache(self, mock_get_redis):
        """Test invalidating cached user data."""
        mock_redis = Mock()
        mock_get_redis.return_value = mock_redis

        result = invalidate_user_cache(privy_id="privy_123", username="testuser")

        assert result is True
        mock_redis.delete.assert_called_once()
        call_args = mock_redis.delete.call_args[0]
        assert "auth:privy_id:privy_123" in call_args
        assert "auth:username:testuser" in call_args

    @patch("src.services.auth_cache.get_redis_client")
    def test_cache_redis_unavailable(self, mock_get_redis):
        """Test cache operations when Redis is unavailable."""
        mock_get_redis.return_value = None

        # Should not raise error, just return False
        result = cache_user_by_privy_id("privy_123", {"id": 123})
        assert result is False

        # Should return None when Redis unavailable
        result = get_cached_user_by_privy_id("privy_123")
        assert result is None


@pytest.mark.unit
class TestConnectionPoolMonitor:
    """Test connection pool monitoring functionality."""

    def test_connection_pool_stats_initialization(self):
        """Test ConnectionPoolStats initialization."""
        stats = ConnectionPoolStats()

        assert stats.total_connections == 0
        assert stats.active_connections == 0
        assert stats.idle_connections == 0
        assert stats.connection_errors == 0

    def test_connection_pool_stats_to_dict(self):
        """Test ConnectionPoolStats to_dict conversion."""
        stats = ConnectionPoolStats()
        stats.active_connections = 5
        stats.max_pool_size = 10

        stats_dict = stats.to_dict()

        assert stats_dict["active_connections"] == 5
        assert stats_dict["max_pool_size"] == 10
        assert stats_dict["utilization_percent"] == 50.0

    def test_connection_pool_stats_is_healthy(self):
        """Test pool health check."""
        stats = ConnectionPoolStats()
        stats.max_pool_size = 10

        # Low utilization should be healthy
        stats.active_connections = 3
        assert stats.is_healthy() is True

        # High utilization should not be healthy
        stats.active_connections = 9
        assert stats.is_healthy(warning_threshold=0.8) is False

    def test_connection_pool_stats_health_status(self):
        """Test pool health status strings."""
        stats = ConnectionPoolStats()
        stats.max_pool_size = 10

        stats.active_connections = 3
        assert stats.get_health_status() == "HEALTHY"

        stats.active_connections = 6
        assert stats.get_health_status() == "NORMAL"

        stats.active_connections = 8
        assert stats.get_health_status() == "WARNING"

        stats.active_connections = 10
        assert stats.get_health_status() == "CRITICAL"

    @patch("src.config.supabase_config.get_supabase_client")
    def test_get_supabase_pool_stats_unavailable(self, mock_get_supabase):
        """Test getting pool stats when unavailable."""
        mock_get_supabase.side_effect = Exception("Client error")

        result = get_supabase_pool_stats()

        assert result is None


@pytest.mark.unit
class TestTimeoutConstants:
    """Test that timeout constants are reasonable."""

    def test_timeout_constants_exist(self):
        """Test that timeout constants are defined."""
        assert AUTH_QUERY_TIMEOUT > 0
        assert USER_LOOKUP_TIMEOUT > 0
        assert USER_LOOKUP_TIMEOUT <= AUTH_QUERY_TIMEOUT

    def test_timeout_values_reasonable(self):
        """Test that timeout values are in reasonable range."""
        # Auth queries should timeout in 8 seconds (strict)
        assert 5 <= AUTH_QUERY_TIMEOUT <= 15

        # User lookups should timeout faster (5 seconds)
        assert 2 <= USER_LOOKUP_TIMEOUT <= 8


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
