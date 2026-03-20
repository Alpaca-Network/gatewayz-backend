"""
Tests for provider credit monitoring service.
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.provider_credit_monitor import (
    CREDIT_THRESHOLDS,
    _determine_credit_status,
    check_all_provider_credits,
    check_openrouter_credits,
    clear_credit_cache,
    send_low_credit_alert,
)


class TestCreditStatusDetermination:
    """Test credit status determination logic"""

    def test_healthy_balance(self):
        """Test that high balance returns healthy status"""
        assert _determine_credit_status(100.0) == "healthy"
        assert _determine_credit_status(51.0) == "healthy"

    def test_info_balance(self):
        """Test that moderate balance returns info status"""
        assert _determine_credit_status(50.0) == "info"
        assert _determine_credit_status(30.0) == "info"

    def test_warning_balance(self):
        """Test that low balance returns warning status"""
        assert _determine_credit_status(20.0) == "warning"
        assert _determine_credit_status(10.0) == "warning"

    def test_critical_balance(self):
        """Test that very low balance returns critical status"""
        assert _determine_credit_status(5.0) == "critical"
        assert _determine_credit_status(1.0) == "critical"
        assert _determine_credit_status(0.0) == "critical"


class TestOpenRouterCreditCheck:
    """Test OpenRouter credit balance checking"""

    @pytest.mark.asyncio
    async def test_successful_credit_check(self):
        """Test successful credit balance check"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"limit_remaining": 123.45}}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            mock_response.raise_for_status = MagicMock()

            result = await check_openrouter_credits()

            assert result["provider"] == "openrouter"
            assert result["balance"] == 123.45
            assert result["status"] == "healthy"
            assert not result["cached"]
            assert "error" not in result

    @pytest.mark.asyncio
    async def test_credit_check_with_warning_balance(self):
        """Test credit check with low balance triggering warning"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"limit_remaining": 15.0}}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            mock_response.raise_for_status = MagicMock()

            result = await check_openrouter_credits()

            assert result["balance"] == 15.0
            assert result["status"] == "warning"

    @pytest.mark.asyncio
    async def test_credit_check_with_critical_balance(self):
        """Test credit check with very low balance triggering critical"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"limit_remaining": 2.0}}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            mock_response.raise_for_status = MagicMock()

            result = await check_openrouter_credits()

            assert result["balance"] == 2.0
            assert result["status"] == "critical"

    @pytest.mark.asyncio
    async def test_credit_check_without_api_key(self):
        """Test credit check fails gracefully without API key"""
        with patch("src.services.provider_credit_monitor.Config") as mock_config:
            mock_config.OPENROUTER_API_KEY = None

            result = await check_openrouter_credits()

            assert result["provider"] == "openrouter"
            assert result["balance"] is None
            assert result["status"] == "unknown"
            assert result["error"] == "API key not configured"

    @pytest.mark.asyncio
    async def test_credit_check_http_error(self):
        """Test credit check handles HTTP errors"""
        from httpx import HTTPStatusError, Request, Response

        mock_request = Request("GET", "https://test.com")
        mock_response = Response(500, request=mock_request)

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=HTTPStatusError(
                    "Server error", request=mock_request, response=mock_response
                )
            )

            result = await check_openrouter_credits()

            assert result["provider"] == "openrouter"
            assert result["balance"] is None
            assert result["status"] == "unknown"
            assert "500" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_credit_check_caching(self):
        """Test that credit checks are cached"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"limit_remaining": 50.0}}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            mock_response.raise_for_status = MagicMock()

            # First call should hit the API
            result1 = await check_openrouter_credits()
            assert not result1["cached"]
            assert result1["balance"] == 50.0

            # Second call should use cache
            result2 = await check_openrouter_credits()
            assert result2["cached"]
            assert result2["balance"] == 50.0

            # Should only have called API once
            assert mock_client.return_value.__aenter__.return_value.get.call_count == 1


class TestCheckAllProviderCredits:
    """Test checking all provider credits"""

    @pytest.mark.asyncio
    async def test_check_all_providers(self):
        """Test checking all monitored providers"""
        with patch("src.services.provider_credit_monitor.check_openrouter_credits") as mock_check:
            mock_check.return_value = {
                "provider": "openrouter",
                "balance": 100.0,
                "status": "healthy",
                "checked_at": datetime.now(UTC),
                "cached": False,
            }

            results = await check_all_provider_credits()

            assert "openrouter" in results
            assert results["openrouter"]["balance"] == 100.0
            assert results["openrouter"]["status"] == "healthy"


class TestLowCreditAlerts:
    """Test low credit alerting system"""

    @pytest.mark.asyncio
    async def test_send_critical_alert(self):
        """Test sending critical credit alert"""
        with (
            patch("src.services.provider_credit_monitor.capture_provider_error") as mock_capture,
            patch("src.services.provider_credit_monitor.send_email") as mock_email,
            patch("src.services.provider_credit_monitor.Config") as mock_config,
        ):

            mock_config.ADMIN_EMAIL = "admin@test.com"

            await send_low_credit_alert("openrouter", 2.0, "critical")

            # Should capture error in Sentry
            mock_capture.assert_called_once()
            args, kwargs = mock_capture.call_args
            assert "credit balance is CRITICAL" in str(args[0])
            assert kwargs["provider"] == "openrouter"
            assert kwargs["extra_context"]["balance"] == 2.0
            assert kwargs["extra_context"]["status"] == "critical"

    @pytest.mark.asyncio
    async def test_send_warning_alert(self):
        """Test sending warning credit alert"""
        with patch("src.services.provider_credit_monitor.capture_provider_error") as mock_capture:
            await send_low_credit_alert("openrouter", 15.0, "warning")

            # Should capture in Sentry
            mock_capture.assert_called_once()
            args, kwargs = mock_capture.call_args
            assert "credit balance is WARNING" in str(args[0])


class TestCreditCacheManagement:
    """Test credit cache management"""

    def test_clear_specific_provider_cache(self):
        """Test clearing cache for specific provider"""
        from src.services.provider_credit_monitor import _credit_balance_cache

        # Populate cache
        _credit_balance_cache["openrouter"] = {"balance": 50.0, "checked_at": datetime.now(UTC)}
        _credit_balance_cache["portkey"] = {"balance": 100.0, "checked_at": datetime.now(UTC)}

        # Clear specific provider
        clear_credit_cache("openrouter")

        # Only openrouter should be cleared
        assert "openrouter" not in _credit_balance_cache
        assert "portkey" in _credit_balance_cache

    def test_clear_all_provider_cache(self):
        """Test clearing all provider caches"""
        from src.services.provider_credit_monitor import _credit_balance_cache

        # Populate cache
        _credit_balance_cache["openrouter"] = {"balance": 50.0}
        _credit_balance_cache["portkey"] = {"balance": 100.0}

        # Clear all
        clear_credit_cache()

        # All should be cleared
        assert len(_credit_balance_cache) == 0
