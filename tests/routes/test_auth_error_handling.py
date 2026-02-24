"""
Tests for auth route error handling improvements.

Specifically tests the improved error handling for configuration issues
like missing or malformed SUPABASE_URL.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


class TestAuthConfigurationErrorHandling:
    """Test auth endpoint error handling for configuration issues"""

    def test_returns_503_for_url_protocol_error(self):
        """Test that URL protocol errors return 503 with user-friendly message"""
        from src.routes.auth import privy_auth
        from src.schemas.auth import PrivyAuthRequest, PrivyUserData

        # Create a mock request
        user_data = PrivyUserData(id="test_user_123", created_at=1234567890)
        request = PrivyAuthRequest(user=user_data, token="test_token")

        # Mock the supabase client to raise the protocol error
        with patch("src.routes.auth.supabase_config") as mock_supabase_config:
            mock_supabase_config.get_supabase_client.side_effect = RuntimeError(
                "Request URL is missing an 'http://' or 'https://' protocol"
            )

            with pytest.raises(HTTPException) as exc_info:
                import asyncio

                asyncio.get_event_loop().run_until_complete(privy_auth(request, MagicMock()))

            assert exc_info.value.status_code == 503
            assert "configuration error" in exc_info.value.detail.lower()

    def test_returns_503_for_supabase_url_must_start_with_error(self):
        """Test that SUPABASE_URL validation errors return 503"""
        from src.routes.auth import privy_auth
        from src.schemas.auth import PrivyAuthRequest, PrivyUserData

        user_data = PrivyUserData(id="test_user_456", created_at=1234567890)
        request = PrivyAuthRequest(user=user_data, token="test_token")

        with patch("src.routes.auth.supabase_config") as mock_supabase_config:
            mock_supabase_config.get_supabase_client.side_effect = RuntimeError(
                "SUPABASE_URL must start with 'http://' or 'https://'. "
                "Current value: 'test.supabase.co'. Expected: 'https://test.supabase.co'"
            )

            with pytest.raises(HTTPException) as exc_info:
                import asyncio

                asyncio.get_event_loop().run_until_complete(privy_auth(request, MagicMock()))

            assert exc_info.value.status_code == 503
            assert "configuration error" in exc_info.value.detail.lower()

    def test_returns_503_for_supabase_url_not_set_error(self):
        """Test that missing SUPABASE_URL error returns 503"""
        from src.routes.auth import privy_auth
        from src.schemas.auth import PrivyAuthRequest, PrivyUserData

        user_data = PrivyUserData(id="test_user_789", created_at=1234567890)
        request = PrivyAuthRequest(user=user_data, token="test_token")

        with patch("src.routes.auth.supabase_config") as mock_supabase_config:
            mock_supabase_config.get_supabase_client.side_effect = RuntimeError(
                "SUPABASE_URL environment variable is not set. "
                "Please configure it with your Supabase project URL"
            )

            with pytest.raises(HTTPException) as exc_info:
                import asyncio

                asyncio.get_event_loop().run_until_complete(privy_auth(request, MagicMock()))

            assert exc_info.value.status_code == 503
            assert "configuration error" in exc_info.value.detail.lower()

    def test_returns_500_for_other_errors(self):
        """Test that non-configuration errors return 500"""
        from src.routes.auth import privy_auth
        from src.schemas.auth import PrivyAuthRequest, PrivyUserData

        user_data = PrivyUserData(id="test_user_abc", created_at=1234567890)
        request = PrivyAuthRequest(user=user_data, token="test_token")

        with patch("src.routes.auth.supabase_config") as mock_supabase_config:
            mock_supabase_config.get_supabase_client.side_effect = RuntimeError(
                "Some other database error"
            )

            with pytest.raises(HTTPException) as exc_info:
                import asyncio

                asyncio.get_event_loop().run_until_complete(privy_auth(request, MagicMock()))

            assert exc_info.value.status_code == 500
            assert "Authentication failed" in exc_info.value.detail

    def test_does_not_expose_internal_details_for_config_errors(self):
        """Test that configuration errors don't expose internal URL values to users"""
        from src.routes.auth import privy_auth
        from src.schemas.auth import PrivyAuthRequest, PrivyUserData

        user_data = PrivyUserData(id="test_user_def", created_at=1234567890)
        request = PrivyAuthRequest(user=user_data, token="test_token")

        with patch("src.routes.auth.supabase_config") as mock_supabase_config:
            mock_supabase_config.get_supabase_client.side_effect = RuntimeError(
                "SUPABASE_URL must start with 'http://' or 'https://'. "
                "Current value: 'secret-project.supabase.co'. "
                "Expected: 'https://secret-project.supabase.co'"
            )

            with pytest.raises(HTTPException) as exc_info:
                import asyncio

                asyncio.get_event_loop().run_until_complete(privy_auth(request, MagicMock()))

            # Should not contain the actual URL value in user-facing message
            assert "secret-project" not in exc_info.value.detail
            # Should have generic message instead
            assert "contact support" in exc_info.value.detail.lower()
