"""
Comprehensive tests for Connection Pool service
"""

from unittest.mock import MagicMock

import pytest

from src.services import connection_pool


@pytest.fixture(autouse=True)
def clear_pools():
    """Ensure each test has a clean slate for connection caches."""
    connection_pool._client_pool.clear()
    connection_pool._async_client_pool.clear()
    yield
    connection_pool._client_pool.clear()
    connection_pool._async_client_pool.clear()


class TestConnectionPool:
    """Test Connection Pool service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        assert connection_pool is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        assert hasattr(connection_pool, "__name__")

    def test_reuses_client_when_api_key_unchanged(self, monkeypatch):
        """The same provider/base/api_key triple should reuse the cached client."""
        http_client = MagicMock(name="http_client")
        monkeypatch.setattr(
            connection_pool,
            "_get_http_client",
            lambda timeout, limits: http_client,
        )

        openai_client = MagicMock(name="openai_client")
        openai_ctor = MagicMock(return_value=openai_client)
        monkeypatch.setattr(connection_pool, "OpenAI", openai_ctor)

        client_one = connection_pool.get_pooled_client(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="key-123",
        )
        client_two = connection_pool.get_pooled_client(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="key-123",
        )

        assert client_one is client_two
        openai_ctor.assert_called_once()

    def test_rebuilds_client_when_api_key_changes(self, monkeypatch):
        """Rotating API keys should evict the old client and close it."""
        http_client = MagicMock(name="http_client")
        monkeypatch.setattr(
            connection_pool,
            "_get_http_client",
            lambda timeout, limits: http_client,
        )

        old_client = MagicMock(name="old_client")
        new_client = MagicMock(name="new_client")
        openai_ctor = MagicMock(side_effect=[old_client, new_client])
        monkeypatch.setattr(connection_pool, "OpenAI", openai_ctor)

        first = connection_pool.get_pooled_client(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="old-key",
        )
        second = connection_pool.get_pooled_client(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="new-key",
        )

        assert first is old_client
        assert second is new_client
        old_client.close.assert_called_once()
        assert len(connection_pool._client_pool) == 1
