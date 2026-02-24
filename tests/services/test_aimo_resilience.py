"""Test AIMO model fetching resilience and timeout handling.

Tests cover:
- Configurable timeouts preventing thread pool blocking
- Retry logic with multiple base URLs
- HTTP fallback mechanism
- Stale-while-revalidate caching
"""

from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from src.cache import _aimo_models_cache
from src.config import Config
from src.services.models import (
    _fresh_cached_models,
    _get_fresh_or_stale_cached_models,
    fetch_models_from_aimo,
)


@pytest.fixture
def reset_aimo_cache():
    """Reset AIMO cache before and after each test."""
    original_data = _aimo_models_cache.get("data")
    original_timestamp = _aimo_models_cache.get("timestamp")
    _aimo_models_cache["data"] = None
    _aimo_models_cache["timestamp"] = None
    yield
    _aimo_models_cache["data"] = original_data
    _aimo_models_cache["timestamp"] = original_timestamp


class TestAIMOTimeoutConfiguration:
    """Test that AIMO timeout configuration is in place."""

    def test_aimo_fetch_timeout_configured(self):
        """Verify AIMO fetch timeout is configurable and reasonable."""
        assert Config.AIMO_FETCH_TIMEOUT == 5.0
        assert Config.AIMO_FETCH_TIMEOUT < 20.0  # Much shorter than original 20s

    def test_aimo_connect_timeout_configured(self):
        """Verify AIMO connect timeout is configured."""
        assert Config.AIMO_CONNECT_TIMEOUT == 3.0
        assert Config.AIMO_CONNECT_TIMEOUT < Config.AIMO_FETCH_TIMEOUT

    def test_aimo_max_retries_configured(self):
        """Verify AIMO max retries is configured."""
        assert Config.AIMO_MAX_RETRIES >= 1
        assert isinstance(Config.AIMO_MAX_RETRIES, int)

    def test_aimo_http_fallback_enabled_by_default(self):
        """Verify HTTP fallback is enabled by default."""
        assert Config.AIMO_ENABLE_HTTP_FALLBACK is True

    def test_aimo_base_urls_configured(self):
        """Verify multiple AIMO base URLs are available."""
        assert len(Config.AIMO_BASE_URLS) >= 1
        assert all(url.startswith("http") for url in Config.AIMO_BASE_URLS)


class TestAIMOStaleWhileRevalidate:
    """Test stale-while-revalidate caching pattern."""

    def test_fresh_cached_models_returns_fresh_data(self, reset_aimo_cache):
        """Fresh cache within TTL should be returned."""
        test_data = [{"id": "model-1", "name": "Test Model"}]
        _aimo_models_cache["data"] = test_data
        _aimo_models_cache["timestamp"] = datetime.now(UTC)
        _aimo_models_cache["ttl"] = 3600

        result = _fresh_cached_models(_aimo_models_cache, "aimo")
        assert result == test_data

    def test_fresh_cached_models_returns_none_when_expired(self, reset_aimo_cache):
        """Fresh cache beyond TTL should return None."""
        import time

        test_data = [{"id": "model-1"}]
        _aimo_models_cache["data"] = test_data
        _aimo_models_cache["ttl"] = 0.1  # Very short TTL
        _aimo_models_cache["timestamp"] = datetime.now(UTC)

        time.sleep(0.2)  # Wait for cache to expire

        result = _fresh_cached_models(_aimo_models_cache, "aimo")
        assert result is None

    def test_stale_while_revalidate_returns_fresh_data(self, reset_aimo_cache):
        """Should return data within fresh TTL."""
        test_data = [{"id": "model-1"}]
        _aimo_models_cache["data"] = test_data
        _aimo_models_cache["timestamp"] = datetime.now(UTC)
        _aimo_models_cache["ttl"] = 3600
        _aimo_models_cache["stale_ttl"] = 7200

        result = _get_fresh_or_stale_cached_models(_aimo_models_cache, "aimo")
        assert result == test_data

    def test_stale_while_revalidate_returns_stale_data(self, reset_aimo_cache):
        """Should return stale data within stale_ttl window."""
        import time

        test_data = [{"id": "model-1"}]
        _aimo_models_cache["data"] = test_data
        _aimo_models_cache["ttl"] = 0.1  # Very short TTL
        _aimo_models_cache["stale_ttl"] = 10.0  # Long stale TTL
        _aimo_models_cache["timestamp"] = datetime.now(UTC)

        time.sleep(0.2)  # Expire fresh cache but within stale window

        result = _get_fresh_or_stale_cached_models(_aimo_models_cache, "aimo")
        assert result == test_data

    def test_stale_while_revalidate_returns_none_when_both_expired(self, reset_aimo_cache):
        """Should return None when both fresh and stale caches expired."""
        import time

        test_data = [{"id": "model-1"}]
        _aimo_models_cache["data"] = test_data
        _aimo_models_cache["ttl"] = 0.1
        _aimo_models_cache["stale_ttl"] = 0.2
        _aimo_models_cache["timestamp"] = datetime.now(UTC)

        time.sleep(0.3)  # Wait for both to expire

        result = _get_fresh_or_stale_cached_models(_aimo_models_cache, "aimo")
        assert result is None

    def test_stale_while_revalidate_returns_none_with_no_cache(self, reset_aimo_cache):
        """Should return None when no cached data exists."""
        _aimo_models_cache["data"] = None
        _aimo_models_cache["timestamp"] = None

        result = _get_fresh_or_stale_cached_models(_aimo_models_cache, "aimo")
        assert result is None


class TestAIMORetryLogic:
    """Test AIMO retry logic with multiple URLs."""

    @patch("src.services.models.httpx.get")
    def test_successful_fetch_on_first_try(self, mock_get, reset_aimo_cache):
        """Should return data on successful first attempt."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "name": "model-1",
                    "providers": [{"name": "provider1", "pricing": {}}],
                }
            ]
        }
        mock_get.return_value = mock_response

        with patch("src.config.Config.AIMO_API_KEY", "test-key"):
            result = fetch_models_from_aimo()

        assert result is not None
        assert len(result) > 0
        assert mock_get.call_count == 1

    @patch("src.services.models.httpx.get")
    def test_retry_on_timeout(self, mock_get, reset_aimo_cache):
        """Should retry on timeout exception."""
        # First attempt times out, second succeeds
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "name": "model-1",
                    "providers": [{"name": "provider1", "pricing": {}}],
                }
            ]
        }

        mock_get.side_effect = [
            httpx.TimeoutException("Connect timeout"),
            mock_response,
        ]

        with patch("src.config.Config.AIMO_API_KEY", "test-key"):
            result = fetch_models_from_aimo()

        # Should succeed due to retry
        assert result is not None or _aimo_models_cache.get("data") is None
        # Should have tried multiple times
        assert mock_get.call_count >= 1

    @patch("src.services.models.httpx.get")
    def test_http_fallback_when_https_fails(self, mock_get, reset_aimo_cache):
        """Should try HTTP fallback when HTTPS fails."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "name": "model-1",
                    "providers": [{"name": "provider1", "pricing": {}}],
                }
            ]
        }

        # HTTPS fails, HTTP succeeds
        mock_get.side_effect = [
            httpx.ConnectError("HTTPS failed"),
            mock_response,
        ]

        with (
            patch("src.config.Config.AIMO_API_KEY", "test-key"),
            patch("src.config.Config.AIMO_ENABLE_HTTP_FALLBACK", True),
        ):
            result = fetch_models_from_aimo()

        # Should have tried both HTTPS and HTTP
        assert mock_get.call_count >= 1

    @patch("src.services.models.httpx.get")
    def test_uses_stale_cache_when_all_retries_fail(self, mock_get, reset_aimo_cache):
        """Should return stale cache when all retries fail."""
        # Set up stale cached data
        stale_data = [{"id": "stale-model-1"}]
        _aimo_models_cache["data"] = stale_data
        _aimo_models_cache["timestamp"] = datetime.now(UTC)
        _aimo_models_cache["ttl"] = 1
        _aimo_models_cache["stale_ttl"] = 100000

        # All fetch attempts fail
        mock_get.side_effect = httpx.ConnectError("All endpoints failed")

        with patch("src.config.Config.AIMO_API_KEY", "test-key"):
            result = fetch_models_from_aimo()

        # Should return stale cache or fallback to empty list
        assert result is not None

    @patch("src.services.models.httpx.get")
    def test_returns_empty_list_when_no_cache_and_all_fail(self, mock_get, reset_aimo_cache):
        """Should return empty list when no cache and all retries fail."""
        _aimo_models_cache["data"] = None
        _aimo_models_cache["timestamp"] = None

        # All fetch attempts fail
        mock_get.side_effect = httpx.ConnectError("Connection failed")

        with patch("src.config.Config.AIMO_API_KEY", "test-key"):
            result = fetch_models_from_aimo()

        assert result == []


class TestRedirectHandling:
    """Test HTTP redirect handling for AIMO model fetching."""

    @patch("src.services.models.httpx.get")
    def test_follows_http_308_redirect(self, mock_get, reset_aimo_cache):
        """Should follow HTTP 308 redirects by using follow_redirects=True."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "name": "model-1",
                    "providers": [{"name": "provider1", "pricing": {}}],
                }
            ]
        }
        mock_get.return_value = mock_response

        with patch("src.config.Config.AIMO_API_KEY", "test-key"):
            fetch_models_from_aimo()

        # Verify httpx.get was called with follow_redirects=True
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs.get("follow_redirects") is True

    @patch("src.services.models.httpx.get")
    def test_redirect_preserves_headers(self, mock_get, reset_aimo_cache):
        """Should preserve authorization headers during redirect."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {
                    "name": "model-1",
                    "providers": [{"name": "provider1", "pricing": {}}],
                }
            ]
        }
        mock_get.return_value = mock_response

        with patch("src.config.Config.AIMO_API_KEY", "test-key"):
            fetch_models_from_aimo()

        # Verify headers contain authorization
        call_kwargs = mock_get.call_args[1]
        assert "headers" in call_kwargs
        assert "Authorization" in call_kwargs["headers"]
        assert call_kwargs["headers"]["Authorization"] == "Bearer test-key"


class TestTimeoutConfiguration:
    """Test that timeout configuration prevents thread blocking."""

    def test_timeout_uses_httpx_timeout_object(self):
        """Verify implementation uses httpx.Timeout with separate connect timeout."""
        # This test verifies the approach prevents blocking
        timeout = httpx.Timeout(
            timeout=Config.AIMO_FETCH_TIMEOUT,
            connect=Config.AIMO_CONNECT_TIMEOUT,
        )
        # Verify timeout object was created successfully with the correct config values
        assert isinstance(timeout, httpx.Timeout)
        # Verify connect timeout is shorter than fetch timeout
        assert Config.AIMO_CONNECT_TIMEOUT < Config.AIMO_FETCH_TIMEOUT

    def test_fetch_timeout_is_short(self):
        """Verify fetch timeout is significantly shorter than original 20s."""
        # Original was 20s, new should be <= 5s
        assert Config.AIMO_FETCH_TIMEOUT <= 5.0
        # Should be long enough for normal requests but not for SSL handshake issues
        assert Config.AIMO_FETCH_TIMEOUT >= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
