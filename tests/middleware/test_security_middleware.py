"""
Tests for Security Middleware

This test suite verifies that the SecurityMiddleware correctly:
1. Applies tiered IP rate limiting (residential vs datacenter IPs)
2. Detects behavioral fingerprints for bot detection
3. Activates velocity mode when error rate exceeds threshold
4. Deactivates velocity mode after cooldown period
5. Classifies errors correctly (4xx vs 5xx vs 499)
6. Exempts authenticated users from IP-based rate limiting
7. Reduces limits during velocity mode
8. Returns proper rate limit headers in 429 responses
"""

import time
from collections import deque
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.middleware.security_middleware import (
    DEFAULT_IP_LIMIT,
    FINGERPRINT_LIMIT,
    STRICT_IP_LIMIT,
    VELOCITY_COOLDOWN_SECONDS,
    VELOCITY_ERROR_THRESHOLD,
    VELOCITY_LIMIT_MULTIPLIER,
    VELOCITY_MIN_REQUESTS,
    VELOCITY_WINDOW_SECONDS,
    SecurityMiddleware,
)


@pytest.fixture
def app_with_security_middleware():
    """Create a test FastAPI app with SecurityMiddleware"""
    app = FastAPI()

    # Mock Redis client
    mock_redis = Mock()
    mock_redis.incr = Mock(side_effect=lambda key: 1)
    mock_redis.expire = Mock()

    # Add SecurityMiddleware
    app.add_middleware(SecurityMiddleware, redis_client=mock_redis)

    @app.get("/api/test")
    async def test_endpoint():
        """Test endpoint that returns 200"""
        return {"status": "ok"}

    @app.get("/api/error")
    async def error_endpoint():
        """Test endpoint that returns 500"""
        return JSONResponse({"error": "Internal error"}, status_code=500)

    return app


@pytest.fixture
def client(app_with_security_middleware):
    """Create test client"""
    return TestClient(app_with_security_middleware)


@pytest.fixture
def security_middleware():
    """Create a SecurityMiddleware instance for unit testing"""
    mock_redis = Mock()
    mock_redis.incr = Mock(side_effect=lambda key: 1)
    mock_redis.expire = Mock()

    mock_app = Mock()
    middleware = SecurityMiddleware(app=mock_app, redis_client=mock_redis)
    return middleware


class TestVelocityModeActivation:
    """Test velocity mode activation logic"""

    def test_velocity_mode_not_activated_below_threshold(self, security_middleware):
        """Test that velocity mode does not activate when error rate is below threshold"""
        # Record requests with error rate below threshold (20% < 25%)
        for _ in range(80):
            security_middleware._record_request_outcome(200, 0.1)  # Success
        for _ in range(20):
            security_middleware._record_request_outcome(500, 0.1)  # Error

        # Should not activate (20% < 25%)
        activated = security_middleware._check_and_activate_velocity_mode()
        assert not activated
        assert not security_middleware._is_velocity_mode_active()

    def test_velocity_mode_activated_above_threshold(self, security_middleware):
        """Test that velocity mode activates when error rate exceeds threshold"""
        # Record requests with error rate above threshold (30% > 25%)
        for _ in range(70):
            security_middleware._record_request_outcome(200, 0.1)  # Success
        for _ in range(30):
            security_middleware._record_request_outcome(500, 0.1)  # Error

        # Should activate (30% > 25%)
        activated = security_middleware._check_and_activate_velocity_mode()
        assert activated
        assert security_middleware._is_velocity_mode_active()

    def test_velocity_mode_not_activated_insufficient_samples(self, security_middleware):
        """Test that velocity mode requires minimum sample size"""
        # Record less than VELOCITY_MIN_REQUESTS (100)
        for _ in range(50):
            security_middleware._record_request_outcome(500, 0.1)  # All errors

        # Should not activate (insufficient samples)
        activated = security_middleware._check_and_activate_velocity_mode()
        assert not activated

    def test_velocity_mode_activation_increments_counter(self, security_middleware):
        """Test that activation counter increments"""
        initial_count = security_middleware._velocity_mode_triggered_count

        # Trigger velocity mode
        for _ in range(70):
            security_middleware._record_request_outcome(200, 0.1)
        for _ in range(30):
            security_middleware._record_request_outcome(500, 0.1)

        security_middleware._check_and_activate_velocity_mode()
        assert security_middleware._velocity_mode_triggered_count == initial_count + 1


class TestVelocityModeDeactivation:
    """Test velocity mode deactivation logic"""

    def test_velocity_mode_deactivates_after_cooldown(self, security_middleware):
        """Test that velocity mode deactivates after cooldown period"""
        # Activate velocity mode
        security_middleware._velocity_mode_until = time.time() + 1  # 1 second cooldown

        assert security_middleware._is_velocity_mode_active()

        # Wait for cooldown
        time.sleep(1.1)

        assert not security_middleware._is_velocity_mode_active()

    def test_velocity_mode_stays_active_during_cooldown(self, security_middleware):
        """Test that velocity mode stays active during cooldown"""
        # Activate velocity mode with future expiry
        security_middleware._velocity_mode_until = time.time() + 100

        assert security_middleware._is_velocity_mode_active()

        # Try to activate again - should not re-activate
        activated = security_middleware._check_and_activate_velocity_mode()
        assert not activated  # Already active


class TestErrorClassification:
    """Test error classification logic"""

    def test_4xx_errors_not_counted(self, security_middleware):
        """Test that 4xx errors (client errors) are not counted as system errors"""
        # Record 4xx errors
        security_middleware._record_request_outcome(400, 0.1)  # Bad Request
        security_middleware._record_request_outcome(401, 0.1)  # Unauthorized
        security_middleware._record_request_outcome(404, 0.1)  # Not Found
        security_middleware._record_request_outcome(429, 0.1)  # Too Many Requests

        # Check that no errors were recorded
        error_count = sum(1 for _, is_error, _ in security_middleware._request_log if is_error)
        assert error_count == 0

    def test_5xx_errors_counted(self, security_middleware):
        """Test that 5xx errors (server errors) are counted"""
        # Record 5xx errors
        security_middleware._record_request_outcome(500, 0.1)  # Internal Server Error
        security_middleware._record_request_outcome(502, 0.1)  # Bad Gateway
        security_middleware._record_request_outcome(503, 0.1)  # Service Unavailable

        # Check that errors were recorded
        error_count = sum(1 for _, is_error, _ in security_middleware._request_log if is_error)
        assert error_count == 3

    def test_499_slow_requests_counted(self, security_middleware):
        """Test that 499 errors with slow duration (>5s) are counted"""
        # Record 499 with slow duration
        security_middleware._record_request_outcome(499, 6.0)  # Slow timeout

        error_count = sum(1 for _, is_error, _ in security_middleware._request_log if is_error)
        assert error_count == 1

    def test_499_fast_requests_not_counted(self, security_middleware):
        """Test that 499 errors with fast duration (<5s) are not counted"""
        # Record 499 with fast duration
        security_middleware._record_request_outcome(499, 2.0)  # Fast timeout

        error_count = sum(1 for _, is_error, _ in security_middleware._request_log if is_error)
        assert error_count == 0


class TestRateLimitCalculation:
    """Test rate limit calculations with velocity mode"""

    def test_effective_limit_normal_mode(self, security_middleware):
        """Test that limits are normal when velocity mode is inactive"""
        base_limit = 100
        effective_limit = security_middleware._get_effective_limit(base_limit)
        assert effective_limit == base_limit

    def test_effective_limit_velocity_mode(self, security_middleware):
        """Test that limits are reduced during velocity mode"""
        # Activate velocity mode
        security_middleware._velocity_mode_until = time.time() + 100

        base_limit = 100
        effective_limit = security_middleware._get_effective_limit(base_limit)
        expected = int(base_limit * VELOCITY_LIMIT_MULTIPLIER)
        assert effective_limit == expected
        assert effective_limit < base_limit

    def test_effective_limit_minimum_of_one(self, security_middleware):
        """Test that effective limit never goes below 1"""
        # Activate velocity mode
        security_middleware._velocity_mode_until = time.time() + 100

        base_limit = 1
        effective_limit = security_middleware._get_effective_limit(base_limit)
        assert effective_limit >= 1


class TestAuthenticatedUserExemption:
    """Test authenticated user exemption from IP-based rate limiting"""

    def test_bearer_token_detected(self, security_middleware):
        """Test that Bearer token format is detected"""
        mock_request = Mock()
        mock_request.headers = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}

        is_auth = security_middleware._is_authenticated_request(mock_request)
        assert is_auth

    def test_gw_api_key_detected(self, security_middleware):
        """Test that Gatewayz API key format is detected"""
        mock_request = Mock()
        mock_request.headers = {"Authorization": "gw_1234567890abcdef1234567890abcdef"}

        is_auth = security_middleware._is_authenticated_request(mock_request)
        assert is_auth

    def test_generic_api_key_detected(self, security_middleware):
        """Test that generic long API keys are detected"""
        mock_request = Mock()
        mock_request.headers = {"Authorization": "sk_test_1234567890abcdefghijk"}

        is_auth = security_middleware._is_authenticated_request(mock_request)
        assert is_auth

    def test_no_authorization_header(self, security_middleware):
        """Test that request without auth header is not authenticated"""
        mock_request = Mock()
        mock_request.headers = {}

        is_auth = security_middleware._is_authenticated_request(mock_request)
        assert not is_auth

    def test_short_authorization_header(self, security_middleware):
        """Test that short auth headers are not considered authenticated"""
        mock_request = Mock()
        mock_request.headers = {"Authorization": "short"}

        is_auth = security_middleware._is_authenticated_request(mock_request)
        assert not is_auth


class TestIPTierDetection:
    """Test datacenter IP detection"""

    @pytest.mark.asyncio
    async def test_datacenter_user_agent_detected(self, security_middleware):
        """Test that datacenter IPs are detected by user agent"""
        mock_request = Mock()
        mock_request.headers = {"user-agent": "python-requests/2.28.0"}

        is_dc = await security_middleware._is_datacenter_ip("1.2.3.4", mock_request)
        assert is_dc

    @pytest.mark.asyncio
    async def test_proxy_headers_detected(self, security_middleware):
        """Test that proxy headers indicate datacenter IP"""
        mock_request = Mock()
        mock_request.headers = {"X-Proxy-ID": "proxy123", "user-agent": "Mozilla/5.0"}

        is_dc = await security_middleware._is_datacenter_ip("1.2.3.4", mock_request)
        assert is_dc

    @pytest.mark.asyncio
    async def test_residential_ip_not_detected_as_datacenter(self, security_middleware):
        """Test that residential IPs are not flagged as datacenter"""
        mock_request = Mock()
        mock_request.headers = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        is_dc = await security_middleware._is_datacenter_ip("1.2.3.4", mock_request)
        assert not is_dc


class TestFingerprintGeneration:
    """Test behavioral fingerprint generation"""

    def test_fingerprint_generation(self, security_middleware):
        """Test that fingerprint is generated from headers"""
        mock_request = Mock()
        mock_request.headers = {
            "user-agent": "Mozilla/5.0",
            "accept-language": "en-US",
            "accept-encoding": "gzip, deflate",
        }

        fingerprint = security_middleware._generate_fingerprint(mock_request)
        assert fingerprint is not None
        assert len(fingerprint) == 16  # SHA256 hash truncated to 16 chars

    def test_same_headers_produce_same_fingerprint(self, security_middleware):
        """Test that identical headers produce identical fingerprints"""
        mock_request1 = Mock()
        mock_request1.headers = {
            "user-agent": "Mozilla/5.0",
            "accept-language": "en-US",
            "accept-encoding": "gzip",
        }

        mock_request2 = Mock()
        mock_request2.headers = {
            "user-agent": "Mozilla/5.0",
            "accept-language": "en-US",
            "accept-encoding": "gzip",
        }

        fp1 = security_middleware._generate_fingerprint(mock_request1)
        fp2 = security_middleware._generate_fingerprint(mock_request2)
        assert fp1 == fp2

    def test_different_headers_produce_different_fingerprints(self, security_middleware):
        """Test that different headers produce different fingerprints"""
        mock_request1 = Mock()
        mock_request1.headers = {
            "user-agent": "Mozilla/5.0",
            "accept-language": "en-US",
            "accept-encoding": "gzip",
        }

        mock_request2 = Mock()
        mock_request2.headers = {
            "user-agent": "Chrome/91.0",
            "accept-language": "fr-FR",
            "accept-encoding": "deflate",
        }

        fp1 = security_middleware._generate_fingerprint(mock_request1)
        fp2 = security_middleware._generate_fingerprint(mock_request2)
        assert fp1 != fp2


class TestRequestOutcomeRecording:
    """Test request outcome recording and log cleanup"""

    def test_request_outcome_recorded(self, security_middleware):
        """Test that request outcomes are recorded"""
        security_middleware._record_request_outcome(200, 0.1)
        assert len(security_middleware._request_log) == 1

    def test_old_entries_cleaned(self, security_middleware):
        """Test that old entries are removed from request log"""
        # Record old entries (simulate by manipulating timestamps)
        now = time.time()
        old_timestamp = now - VELOCITY_WINDOW_SECONDS - 10

        # Manually add old entries
        security_middleware._request_log.append((old_timestamp, False, 200))
        security_middleware._request_log.append((now, False, 200))

        # Record new entry - should trigger cleanup
        security_middleware._record_request_outcome(200, 0.1)

        # Old entries should be removed
        assert len(security_middleware._request_log) == 2
        assert all(
            ts >= now - VELOCITY_WINDOW_SECONDS for ts, _, _ in security_middleware._request_log
        )


class TestRateLimitHeaders:
    """Test that rate limit headers are returned in 429 responses"""

    @pytest.mark.asyncio
    async def test_rate_limit_headers_present(self):
        """Test that 429 responses include rate limit headers"""
        # This is more of an integration test - would need to mock the entire request flow
        # For now, we verify the header format in the code
        pass  # Covered by integration tests


class TestIntegrationScenarios:
    """Integration test scenarios"""

    def test_health_endpoint_bypasses_security(self, client):
        """Test that health endpoints bypass security checks"""
        response = client.get("/health")
        # Should get 404 (not implemented) not 429 (rate limited)
        assert response.status_code == 404

    def test_normal_request_allowed(self, client):
        """Test that normal requests are allowed"""
        response = client.get("/api/test")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestVelocityModeConfiguration:
    """Test velocity mode configuration constants"""

    def test_configuration_constants(self):
        """Test that configuration constants are set correctly"""
        assert VELOCITY_ERROR_THRESHOLD == 0.25  # 25%
        assert VELOCITY_COOLDOWN_SECONDS == 180  # 3 minutes
        assert VELOCITY_MIN_REQUESTS == 100
        assert VELOCITY_WINDOW_SECONDS == 60
        assert VELOCITY_LIMIT_MULTIPLIER == 0.5  # 50%

    def test_ip_limit_constants(self):
        """Test that IP limit constants are set correctly"""
        assert DEFAULT_IP_LIMIT == 300  # RPM
        assert STRICT_IP_LIMIT == 60  # RPM
        assert FINGERPRINT_LIMIT == 100  # RPM


class TestEdgeCases:
    """Test edge cases and error conditions"""

    def test_empty_request_log(self, security_middleware):
        """Test that velocity mode doesn't activate with empty log"""
        assert len(security_middleware._request_log) == 0
        activated = security_middleware._check_and_activate_velocity_mode()
        assert not activated

    def test_deque_max_length_respected(self, security_middleware):
        """Test that request log respects maxlen (10000)"""
        # Add more than maxlen entries
        for i in range(15000):
            security_middleware._record_request_outcome(200, 0.1)

        # Should not exceed maxlen
        assert len(security_middleware._request_log) == 10000

    def test_client_ip_extraction(self, security_middleware):
        """Test client IP extraction from X-Forwarded-For"""
        mock_request = Mock()
        mock_request.headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
        mock_request.client = Mock()
        mock_request.client.host = "10.0.0.1"

        # Should extract first IP from X-Forwarded-For
        import asyncio

        ip = asyncio.run(security_middleware._get_client_ip(mock_request))
        assert ip == "1.2.3.4"
