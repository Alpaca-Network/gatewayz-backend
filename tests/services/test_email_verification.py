"""
Tests for Email Verification Service using Emailable API.

Run with: pytest tests/services/test_email_verification.py -v
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.email_verification import (
    EmailReason,
    EmailState,
    EmailVerificationResult,
    EmailVerificationService,
)


class TestEmailVerificationResult:
    """Tests for EmailVerificationResult dataclass."""

    def test_disposable_email_is_bot(self):
        """Disposable emails should be marked as bot."""
        result = EmailVerificationResult(
            email="test@tempmail.com",
            state=EmailState.DELIVERABLE,
            reason=EmailReason.ACCEPTED_EMAIL,
            score=80,
            is_disposable=True,
            is_free=False,
            is_role=False,
            domain="tempmail.com",
        )
        assert result.is_bot is True
        assert result.subscription_status == "bot"

    def test_risky_low_score_is_bot(self):
        """Risky emails with low score should be marked as bot."""
        result = EmailVerificationResult(
            email="test@suspicious.com",
            state=EmailState.RISKY,
            reason=EmailReason.LOW_QUALITY,
            score=30,
            is_disposable=False,
            is_free=False,
            is_role=False,
            domain="suspicious.com",
        )
        assert result.is_bot is True
        assert result.subscription_status == "bot"

    def test_risky_high_score_not_bot(self):
        """Risky emails with high score should not be marked as bot."""
        result = EmailVerificationResult(
            email="test@company.com",
            state=EmailState.RISKY,
            reason=EmailReason.ACCEPT_ALL,
            score=70,
            is_disposable=False,
            is_free=False,
            is_role=False,
            domain="company.com",
        )
        assert result.is_bot is False
        assert result.subscription_status == "trial"

    def test_deliverable_email_not_bot(self):
        """Deliverable emails should not be marked as bot."""
        result = EmailVerificationResult(
            email="test@gmail.com",
            state=EmailState.DELIVERABLE,
            reason=EmailReason.ACCEPTED_EMAIL,
            score=95,
            is_disposable=False,
            is_free=True,
            is_role=False,
            domain="gmail.com",
        )
        assert result.is_bot is False
        assert result.subscription_status == "trial"

    def test_unknown_email_not_bot(self):
        """Unknown emails should not be marked as bot (benefit of doubt)."""
        result = EmailVerificationResult(
            email="test@newdomain.com",
            state=EmailState.UNKNOWN,
            reason=EmailReason.TIMEOUT,
            score=50,
            is_disposable=False,
            is_free=False,
            is_role=False,
            domain="newdomain.com",
        )
        assert result.is_bot is False
        assert result.subscription_status == "trial"

    def test_undeliverable_should_block(self):
        """Undeliverable emails should be blocked."""
        result = EmailVerificationResult(
            email="test@invalid.com",
            state=EmailState.UNDELIVERABLE,
            reason=EmailReason.REJECTED_EMAIL,
            score=0,
            is_disposable=False,
            is_free=False,
            is_role=False,
            domain="invalid.com",
        )
        assert result.should_block is True

    def test_invalid_domain_should_block(self):
        """Invalid domain emails should be blocked."""
        result = EmailVerificationResult(
            email="test@notreal.invalid",
            state=EmailState.UNDELIVERABLE,
            reason=EmailReason.INVALID_DOMAIN,
            score=0,
            is_disposable=False,
            is_free=False,
            is_role=False,
            domain="notreal.invalid",
        )
        assert result.should_block is True

    def test_deliverable_should_not_block(self):
        """Deliverable emails should not be blocked."""
        result = EmailVerificationResult(
            email="test@gmail.com",
            state=EmailState.DELIVERABLE,
            reason=EmailReason.ACCEPTED_EMAIL,
            score=95,
            is_disposable=False,
            is_free=True,
            is_role=False,
            domain="gmail.com",
        )
        assert result.should_block is False


class TestEmailVerificationService:
    """Tests for EmailVerificationService."""

    def test_service_disabled_by_default(self):
        """Service should be disabled by default."""
        service = EmailVerificationService()
        assert service.enabled is False

    def test_service_enabled_with_api_key(self):
        """Service should be enabled when API key and enabled flag are set."""
        service = EmailVerificationService(
            api_key="test_key",
            enabled=True,
        )
        assert service.enabled is True
        assert service.api_key == "test_key"

    def test_service_disabled_without_api_key(self):
        """Service should be disabled if enabled but no API key."""
        service = EmailVerificationService(
            api_key=None,
            enabled=True,
        )
        assert service.enabled is False

    @pytest.mark.asyncio
    async def test_verify_returns_unknown_when_disabled(self):
        """When disabled, verify should return unknown result."""
        service = EmailVerificationService(enabled=False)
        result = await service.verify_email("test@example.com")

        assert result.state == EmailState.UNKNOWN
        assert result.reason == EmailReason.NOT_VERIFIED
        assert result.score == 50
        assert result.is_disposable is False

    @pytest.mark.asyncio
    async def test_verify_parses_deliverable_response(self):
        """Service should correctly parse deliverable response."""
        service = EmailVerificationService(api_key="test_key", enabled=True)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "state": "deliverable",
            "reason": "accepted_email",
            "score": 95,
            "disposable": False,
            "free": True,
            "role": False,
            "domain": "gmail.com",
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await service.verify_email("test@gmail.com")

            assert result.state == EmailState.DELIVERABLE
            assert result.reason == EmailReason.ACCEPTED_EMAIL
            assert result.score == 95
            assert result.is_disposable is False
            assert result.is_free is True
            assert result.domain == "gmail.com"

    @pytest.mark.asyncio
    async def test_verify_parses_disposable_response(self):
        """Service should correctly identify disposable emails."""
        service = EmailVerificationService(api_key="test_key", enabled=True)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "state": "deliverable",
            "reason": "accepted_email",
            "score": 80,
            "disposable": True,
            "free": False,
            "role": False,
            "domain": "tempmail.com",
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await service.verify_email("test@tempmail.com")

            assert result.is_disposable is True
            assert result.is_bot is True
            assert result.subscription_status == "bot"

    @pytest.mark.asyncio
    async def test_verify_handles_rate_limit(self):
        """Service should handle rate limit gracefully."""
        service = EmailVerificationService(api_key="test_key", enabled=True)

        mock_response = MagicMock()
        mock_response.status_code = 429

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await service.verify_email("test@example.com")

            assert result.state == EmailState.UNKNOWN
            assert result.reason == EmailReason.THROTTLED

    @pytest.mark.asyncio
    async def test_verify_handles_insufficient_credits(self):
        """Service should handle insufficient credits gracefully."""
        service = EmailVerificationService(api_key="test_key", enabled=True)

        mock_response = MagicMock()
        mock_response.status_code = 402

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await service.verify_email("test@example.com")

            assert result.state == EmailState.UNKNOWN
            assert result.reason == EmailReason.API_ERROR

    @pytest.mark.asyncio
    async def test_verify_handles_timeout(self):
        """Service should handle timeout gracefully."""
        import httpx

        service = EmailVerificationService(api_key="test_key", enabled=True)

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=httpx.TimeoutException("timeout")
            )

            result = await service.verify_email("test@example.com")

            assert result.state == EmailState.UNKNOWN
            assert result.reason == EmailReason.TIMEOUT


class TestEmailVerificationEdgeCases:
    """Tests for edge cases and special scenarios."""

    @pytest.mark.asyncio
    async def test_empty_email(self):
        """Service should handle empty email gracefully."""
        service = EmailVerificationService(enabled=False)
        result = await service.verify_email("")

        assert result.email == ""
        assert result.domain == ""

    @pytest.mark.asyncio
    async def test_email_without_at_symbol(self):
        """Service should handle malformed email gracefully."""
        service = EmailVerificationService(enabled=False)
        result = await service.verify_email("notanemail")

        assert result.email == "notanemail"
        assert result.domain == ""  # No @ means empty domain

    @pytest.mark.asyncio
    async def test_email_case_normalization(self):
        """Service should normalize email to lowercase."""
        service = EmailVerificationService(enabled=False)
        result = await service.verify_email("Test@GMAIL.COM")

        assert result.email == "test@gmail.com"

    @pytest.mark.asyncio
    async def test_email_whitespace_trimming(self):
        """Service should trim whitespace from email."""
        service = EmailVerificationService(enabled=False)
        result = await service.verify_email("  test@gmail.com  ")

        assert result.email == "test@gmail.com"

    def test_did_you_mean_suggestion(self):
        """Result should include did_you_mean suggestion if provided."""
        result = EmailVerificationResult(
            email="test@gmial.com",
            state=EmailState.UNDELIVERABLE,
            reason=EmailReason.INVALID_DOMAIN,
            score=0,
            is_disposable=False,
            is_free=False,
            is_role=False,
            domain="gmial.com",
            did_you_mean="test@gmail.com",
        )
        assert result.did_you_mean == "test@gmail.com"


class TestEmailVerificationCaching:
    """Tests for email verification caching functionality."""

    def test_result_to_cache_dict(self):
        """Result should correctly convert to cache dict."""
        result = EmailVerificationResult(
            email="test@gmail.com",
            state=EmailState.DELIVERABLE,
            reason=EmailReason.ACCEPTED_EMAIL,
            score=95,
            is_disposable=False,
            is_free=True,
            is_role=False,
            domain="gmail.com",
            did_you_mean=None,
        )

        cache_dict = result.to_cache_dict()

        assert cache_dict["email"] == "test@gmail.com"
        assert cache_dict["state"] == "deliverable"
        assert cache_dict["reason"] == "accepted_email"
        assert cache_dict["score"] == 95
        assert cache_dict["is_disposable"] is False
        assert cache_dict["is_free"] is True
        assert cache_dict["is_role"] is False
        assert cache_dict["domain"] == "gmail.com"
        assert cache_dict["did_you_mean"] is None

    def test_result_from_cache_dict(self):
        """Result should correctly restore from cache dict."""
        cache_dict = {
            "email": "test@gmail.com",
            "state": "deliverable",
            "reason": "accepted_email",
            "score": 95,
            "is_disposable": False,
            "is_free": True,
            "is_role": False,
            "domain": "gmail.com",
            "did_you_mean": "test@google.com",
        }

        result = EmailVerificationResult.from_cache_dict(cache_dict)

        assert result.email == "test@gmail.com"
        assert result.state == EmailState.DELIVERABLE
        assert result.reason == EmailReason.ACCEPTED_EMAIL
        assert result.score == 95
        assert result.is_disposable is False
        assert result.is_free is True
        assert result.is_role is False
        assert result.domain == "gmail.com"
        assert result.did_you_mean == "test@google.com"

    def test_cache_roundtrip(self):
        """Result should survive roundtrip through cache dict."""
        original = EmailVerificationResult(
            email="test@tempmail.com",
            state=EmailState.DELIVERABLE,
            reason=EmailReason.ACCEPTED_EMAIL,
            score=80,
            is_disposable=True,
            is_free=False,
            is_role=True,
            domain="tempmail.com",
            did_you_mean=None,
        )

        cache_dict = original.to_cache_dict()
        restored = EmailVerificationResult.from_cache_dict(cache_dict)

        assert restored.email == original.email
        assert restored.state == original.state
        assert restored.reason == original.reason
        assert restored.score == original.score
        assert restored.is_disposable == original.is_disposable
        assert restored.is_free == original.is_free
        assert restored.is_role == original.is_role
        assert restored.domain == original.domain
        assert restored.did_you_mean == original.did_you_mean
        # Verify computed properties still work
        assert restored.is_bot == original.is_bot
        assert restored.should_block == original.should_block

    @pytest.mark.asyncio
    async def test_verify_caches_successful_result(self):
        """Successful verification should be cached."""
        service = EmailVerificationService(api_key="test_key", enabled=True)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "state": "deliverable",
            "reason": "accepted_email",
            "score": 95,
            "disposable": False,
            "free": True,
            "role": False,
            "domain": "gmail.com",
        }

        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # No cached result

        with (
            patch("httpx.AsyncClient") as mock_client,
            patch("src.services.email_verification._get_redis_client", return_value=mock_redis),
        ):
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await service.verify_email("test@gmail.com")

            # Verify result is correct
            assert result.state == EmailState.DELIVERABLE
            # Verify cache was called
            mock_redis.setex.assert_called_once()
            call_args = mock_redis.setex.call_args
            assert "email_verification:test@gmail.com" in call_args[0]

    @pytest.mark.asyncio
    async def test_verify_returns_cached_result(self):
        """Verification should return cached result if available."""
        service = EmailVerificationService(api_key="test_key", enabled=True)

        cached_data = '{"email": "test@gmail.com", "state": "deliverable", "reason": "accepted_email", "score": 95, "is_disposable": false, "is_free": true, "is_role": false, "domain": "gmail.com", "did_you_mean": null}'

        mock_redis = MagicMock()
        mock_redis.get.return_value = cached_data

        with (
            patch("httpx.AsyncClient") as mock_client,
            patch("src.services.email_verification._get_redis_client", return_value=mock_redis),
        ):
            result = await service.verify_email("test@gmail.com")

            # Verify result matches cached data
            assert result.email == "test@gmail.com"
            assert result.state == EmailState.DELIVERABLE
            assert result.score == 95
            # Verify API was NOT called
            mock_client.return_value.__aenter__.return_value.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_does_not_cache_api_errors(self):
        """API errors should not be cached."""
        service = EmailVerificationService(api_key="test_key", enabled=True)

        mock_response = MagicMock()
        mock_response.status_code = 500  # Server error

        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # No cached result

        with (
            patch("httpx.AsyncClient") as mock_client,
            patch("src.services.email_verification._get_redis_client", return_value=mock_redis),
        ):
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await service.verify_email("test@example.com")

            # Verify error result
            assert result.state == EmailState.UNKNOWN
            assert result.reason == EmailReason.API_ERROR
            # Verify cache was NOT written
            mock_redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_does_not_cache_timeouts(self):
        """Timeouts should not be cached."""
        import httpx

        service = EmailVerificationService(api_key="test_key", enabled=True)

        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        with (
            patch("httpx.AsyncClient") as mock_client,
            patch("src.services.email_verification._get_redis_client", return_value=mock_redis),
        ):
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=httpx.TimeoutException("timeout")
            )

            result = await service.verify_email("test@example.com")

            assert result.reason == EmailReason.TIMEOUT
            # Verify cache was NOT written
            mock_redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_works_when_redis_unavailable(self):
        """Verification should work when Redis is unavailable."""
        service = EmailVerificationService(api_key="test_key", enabled=True)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "state": "deliverable",
            "reason": "accepted_email",
            "score": 95,
            "disposable": False,
            "free": True,
            "role": False,
            "domain": "gmail.com",
        }

        with (
            patch("httpx.AsyncClient") as mock_client,
            patch("src.services.email_verification._get_redis_client", return_value=None),
        ):
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await service.verify_email("test@gmail.com")

            # Verification should still work
            assert result.state == EmailState.DELIVERABLE
            assert result.score == 95
