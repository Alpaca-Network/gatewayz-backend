"""
Tests for src/config/supabase_config.py

Tests the Supabase client initialization and URL validation.
"""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest


class TestGetSupabaseClientValidation:
    """Test SUPABASE_URL validation in get_supabase_client"""

    def test_raises_error_when_supabase_url_not_set(self, monkeypatch):
        """Test that get_supabase_client raises RuntimeError when SUPABASE_URL is not set"""
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.delenv("VERCEL", raising=False)

        # Need to reload modules to pick up env changes
        import src.config.config as config_mod

        importlib.reload(config_mod)

        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client
        supabase_config_mod._supabase_client = None

        with pytest.raises(RuntimeError) as exc_info:
            supabase_config_mod.get_supabase_client()

        error_message = str(exc_info.value)
        assert "SUPABASE_URL" in error_message

    def test_raises_error_when_supabase_url_missing_protocol(self, monkeypatch):
        """Test that get_supabase_client raises RuntimeError when SUPABASE_URL lacks protocol"""
        monkeypatch.setenv("SUPABASE_URL", "test.supabase.co")  # Missing protocol
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        monkeypatch.delenv("VERCEL", raising=False)

        import src.config.config as config_mod

        importlib.reload(config_mod)

        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client
        supabase_config_mod._supabase_client = None

        with pytest.raises(RuntimeError) as exc_info:
            supabase_config_mod.get_supabase_client()

        error_message = str(exc_info.value)
        assert "http://" in error_message or "https://" in error_message

    def test_error_message_includes_example_fix(self, monkeypatch):
        """Test that error message for missing protocol includes example of correct format"""
        monkeypatch.setenv("SUPABASE_URL", "myproject.supabase.co")  # Missing protocol
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        monkeypatch.delenv("VERCEL", raising=False)

        import src.config.config as config_mod

        importlib.reload(config_mod)

        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client
        supabase_config_mod._supabase_client = None

        with pytest.raises(RuntimeError) as exc_info:
            supabase_config_mod.get_supabase_client()

        error_message = str(exc_info.value)
        # Should include the expected format as a hint
        assert "https://myproject.supabase.co" in error_message

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_accepts_valid_https_url(self, mock_httpx_client, mock_create_client, monkeypatch):
        """Test that get_supabase_client accepts valid https:// URL"""
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        monkeypatch.delenv("VERCEL", raising=False)

        import src.config.config as config_mod

        importlib.reload(config_mod)

        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client
        supabase_config_mod._supabase_client = None

        # Mock the Supabase client
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = (
            MagicMock(data=[])
        )
        mock_create_client.return_value = mock_client

        # Should not raise
        client = supabase_config_mod.get_supabase_client()
        assert client is not None
        mock_create_client.assert_called_once()

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_accepts_valid_http_url_for_local_dev(
        self, mock_httpx_client, mock_create_client, monkeypatch
    ):
        """Test that get_supabase_client accepts valid http:// URL for local development"""
        monkeypatch.setenv("SUPABASE_URL", "http://localhost:54321")
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        monkeypatch.delenv("VERCEL", raising=False)

        import src.config.config as config_mod

        importlib.reload(config_mod)

        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client
        supabase_config_mod._supabase_client = None

        # Mock the Supabase client
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = (
            MagicMock(data=[])
        )
        mock_create_client.return_value = mock_client

        # Should not raise
        client = supabase_config_mod.get_supabase_client()
        assert client is not None

    @patch("src.config.supabase_config.create_client")
    @patch("src.config.supabase_config.httpx.Client")
    def test_logs_masked_url_on_initialization(
        self, mock_httpx_client, mock_create_client, monkeypatch, caplog
    ):
        """Test that initialization logs a masked version of the URL"""
        monkeypatch.setenv("SUPABASE_URL", "https://verylongprojectname.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
        monkeypatch.delenv("VERCEL", raising=False)

        import src.config.config as config_mod

        importlib.reload(config_mod)

        import src.config.supabase_config as supabase_config_mod

        # Reset the cached client
        supabase_config_mod._supabase_client = None

        # Mock the Supabase client
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = (
            MagicMock(data=[])
        )
        mock_create_client.return_value = mock_client

        with caplog.at_level("INFO"):
            supabase_config_mod.get_supabase_client()

        # Check that some form of the URL was logged (masked)
        assert any("Initializing Supabase client" in record.message for record in caplog.records)


class TestLazySupabaseClient:
    """Test the _LazySupabaseClient proxy class"""

    def test_lazy_client_repr(self):
        """Test that lazy client has a meaningful repr"""
        import src.config.supabase_config as supabase_config_mod

        lazy_client = supabase_config_mod._LazySupabaseClient()
        assert repr(lazy_client) == "<LazySupabaseClient proxy>"

    def test_lazy_client_raises_attribute_error_for_dunder_attrs(self):
        """Test that lazy client raises AttributeError for dunder attributes"""
        import src.config.supabase_config as supabase_config_mod

        lazy_client = supabase_config_mod._LazySupabaseClient()

        with pytest.raises(AttributeError):
            _ = lazy_client._private_attr

        with pytest.raises(AttributeError):
            _ = lazy_client.__some_dunder__
