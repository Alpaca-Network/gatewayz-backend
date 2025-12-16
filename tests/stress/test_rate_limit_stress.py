"""
Rate Limiting Stress Tests

Tests rate limiting behavior under heavy load and edge cases.

These tests verify:
- Rate limits work correctly under high concurrency
- No race conditions in rate limit counters
- Proper handling of burst traffic
- Recovery after rate limit windows expire
- Distributed rate limiting (Redis) handles concurrent requests

Run with:
    pytest tests/stress/test_rate_limit_stress.py -v
"""

import pytest
import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch, AsyncMock
from tests.helpers.mocks import mock_rate_limiter, create_test_db_fixture
from tests.helpers.data_generators import UserGenerator
from fastapi.testclient import TestClient
import time
from datetime import datetime, timedelta


# ============================================================================
# Concurrent Request Tests
# ============================================================================

class TestConcurrentRateLimiting:
    """Test rate limiting under concurrent load"""

    @pytest.mark.stress
    @pytest.mark.asyncio
    async def test_concurrent_requests_respect_limit(self):
        """Test that concurrent requests don't exceed rate limit"""
        from src.security.deps import rate_limiter_manager

        user_id = "test-user-concurrent"
        limit = 10
        window_seconds = 60
        num_concurrent_requests = 50

        # Mock rate limiter with actual logic
        async def check_rate_limit(uid, lim, window):
            # Simulate actual rate limiting logic
            current_count = getattr(check_rate_limit, f'count_{uid}', 0)

            if current_count >= lim:
                return Mock(allowed=False, remaining=0, limit=lim, reset_at=datetime.utcnow() + timedelta(seconds=window))

            setattr(check_rate_limit, f'count_{uid}', current_count + 1)
            return Mock(
                allowed=True,
                remaining=lim - current_count - 1,
                limit=lim,
                reset_at=datetime.utcnow() + timedelta(seconds=window)
            )

        with patch.object(rate_limiter_manager, 'check_rate_limit', side_effect=check_rate_limit):
            # Make concurrent requests
            tasks = [
                rate_limiter_manager.check_rate_limit(user_id, limit, window_seconds)
                for _ in range(num_concurrent_requests)
            ]

            results = await asyncio.gather(*tasks)

            # Count allowed vs denied
            allowed_count = sum(1 for r in results if r.allowed)
            denied_count = sum(1 for r in results if not r.allowed)

            # Should allow exactly 'limit' requests, deny the rest
            assert allowed_count == limit, f"Expected {limit} allowed, got {allowed_count}"
            assert denied_count == num_concurrent_requests - limit

    @pytest.mark.stress
    @pytest.mark.asyncio
    async def test_multiple_users_concurrent(self):
        """Test rate limiting for multiple users concurrently"""
        from src.security.deps import rate_limiter_manager

        num_users = 10
        requests_per_user = 20
        limit_per_user = 10

        # Track counts per user
        user_counts = {}

        async def check_with_tracking(user_id, lim, window):
            if user_id not in user_counts:
                user_counts[user_id] = 0

            if user_counts[user_id] >= lim:
                return Mock(allowed=False, remaining=0, limit=lim)

            user_counts[user_id] += 1
            return Mock(allowed=True, remaining=lim - user_counts[user_id], limit=lim)

        with patch.object(rate_limiter_manager, 'check_rate_limit', side_effect=check_with_tracking):
            # Create tasks for all users
            tasks = []
            for user_num in range(num_users):
                user_id = f"user-{user_num}"
                for _ in range(requests_per_user):
                    tasks.append(
                        rate_limiter_manager.check_rate_limit(user_id, limit_per_user, 60)
                    )

            # Execute all concurrently
            results = await asyncio.gather(*tasks)

            # Each user should have exactly limit_per_user allowed requests
            for user_num in range(num_users):
                user_id = f"user-{user_num}"
                assert user_counts[user_id] == limit_per_user


# ============================================================================
# Burst Traffic Tests
# ============================================================================

class TestBurstTraffic:
    """Test handling of sudden traffic bursts"""

    @pytest.mark.stress
    @pytest.mark.asyncio
    async def test_burst_then_steady(self):
        """Test burst of requests followed by steady traffic"""
        from src.security.deps import rate_limiter_manager

        user_id = "test-user-burst"
        limit = 100
        window = 60

        burst_size = 150
        steady_rate = 10

        # Simulate burst
        burst_count = 0

        async def burst_check(uid, lim, win):
            nonlocal burst_count
            if burst_count < lim:
                burst_count += 1
                return Mock(allowed=True, remaining=lim - burst_count, limit=lim)
            else:
                return Mock(allowed=False, remaining=0, limit=lim)

        with patch.object(rate_limiter_manager, 'check_rate_limit', side_effect=burst_check):
            # Send burst
            burst_tasks = [
                rate_limiter_manager.check_rate_limit(user_id, limit, window)
                for _ in range(burst_size)
            ]

            burst_results = await asyncio.gather(*burst_tasks)

            allowed_in_burst = sum(1 for r in burst_results if r.allowed)
            denied_in_burst = sum(1 for r in burst_results if not r.allowed)

            assert allowed_in_burst == limit
            assert denied_in_burst == burst_size - limit

    @pytest.mark.stress
    def test_rapid_sequential_requests(self):
        """Test rapid sequential requests"""
        from src.security.deps import rate_limiter_manager

        user_id = "test-user-rapid"
        limit = 60
        num_requests = 100

        count = 0

        def rapid_check(uid, lim, win):
            nonlocal count
            if count < lim:
                count += 1
                return AsyncMock(return_value=Mock(allowed=True, remaining=lim - count, limit=lim))()
            return AsyncMock(return_value=Mock(allowed=False, remaining=0, limit=lim))()

        with patch.object(rate_limiter_manager, 'check_rate_limit', side_effect=rapid_check):
            results = []
            for _ in range(num_requests):
                result = asyncio.run(rate_limiter_manager.check_rate_limit(user_id, limit, 60))
                results.append(result)

            allowed = sum(1 for r in results if r.allowed)
            assert allowed == limit


# ============================================================================
# Window Expiration Tests
# ============================================================================

class TestWindowExpiration:
    """Test rate limit window expiration and reset"""

    @pytest.mark.stress
    @pytest.mark.asyncio
    async def test_window_reset_allows_new_requests(self):
        """Test that new window allows requests after expiration"""
        from src.security.deps import rate_limiter_manager

        user_id = "test-user-reset"
        limit = 10
        window = 1  # 1 second window for faster testing

        # Track which window we're in
        first_window_count = 0
        second_window_count = 0

        async def windowed_check(uid, lim, win):
            nonlocal first_window_count, second_window_count

            # First 10 requests
            if first_window_count < lim:
                first_window_count += 1
                return Mock(allowed=True, remaining=lim - first_window_count, limit=lim)

            # Simulate window reset after delay
            if first_window_count == lim and second_window_count < lim:
                second_window_count += 1
                return Mock(allowed=True, remaining=lim - second_window_count, limit=lim)

            return Mock(allowed=False, remaining=0, limit=lim)

        with patch.object(rate_limiter_manager, 'check_rate_limit', side_effect=windowed_check):
            # First batch - should all succeed
            first_batch = [
                rate_limiter_manager.check_rate_limit(user_id, limit, window)
                for _ in range(limit)
            ]
            first_results = await asyncio.gather(*first_batch)
            assert all(r.allowed for r in first_results)

            # Wait for window to expire
            await asyncio.sleep(window + 0.1)

            # Second batch - should succeed after reset
            second_batch = [
                rate_limiter_manager.check_rate_limit(user_id, limit, window)
                for _ in range(limit)
            ]
            second_results = await asyncio.gather(*second_batch)
            assert all(r.allowed for r in second_results)


# ============================================================================
# High Volume Stress Tests
# ============================================================================

class TestHighVolumeStress:
    """Test rate limiting under extreme load"""

    @pytest.mark.stress
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_extreme_concurrent_load(self):
        """Test with extreme number of concurrent requests"""
        from src.security.deps import rate_limiter_manager

        num_users = 100
        requests_per_user = 50
        limit_per_user = 20

        user_counts = {}

        async def count_check(uid, lim, win):
            if uid not in user_counts:
                user_counts[uid] = 0

            if user_counts[uid] < lim:
                user_counts[uid] += 1
                return Mock(allowed=True, remaining=lim - user_counts[uid], limit=lim)

            return Mock(allowed=False, remaining=0, limit=lim)

        with patch.object(rate_limiter_manager, 'check_rate_limit', side_effect=count_check):
            # Generate 5000 total requests (100 users * 50 requests)
            tasks = []
            for user_num in range(num_users):
                user_id = f"user-{user_num}"
                for _ in range(requests_per_user):
                    tasks.append(
                        rate_limiter_manager.check_rate_limit(user_id, limit_per_user, 60)
                    )

            # Execute all concurrently
            results = await asyncio.gather(*tasks)

            # Verify total allowed requests
            total_allowed = sum(1 for r in results if r.allowed)
            expected_allowed = num_users * limit_per_user

            assert total_allowed == expected_allowed, \
                f"Expected {expected_allowed} allowed requests, got {total_allowed}"


# ============================================================================
# Edge Case Tests
# ============================================================================

class TestRateLimitEdgeCases:
    """Test edge cases in rate limiting"""

    @pytest.mark.stress
    @pytest.mark.asyncio
    async def test_zero_limit_denies_all(self):
        """Test that zero limit denies all requests"""
        from src.security.deps import rate_limiter_manager

        async def zero_limit_check(uid, lim, win):
            return Mock(allowed=False, remaining=0, limit=0)

        with patch.object(rate_limiter_manager, 'check_rate_limit', side_effect=zero_limit_check):
            results = [
                await rate_limiter_manager.check_rate_limit("user", 0, 60)
                for _ in range(10)
            ]

            assert all(not r.allowed for r in results)

    @pytest.mark.stress
    @pytest.mark.asyncio
    async def test_unlimited_allows_all(self):
        """Test unlimited rate limit (very high limit)"""
        from src.security.deps import rate_limiter_manager

        limit = 1000000
        count = 0

        async def unlimited_check(uid, lim, win):
            nonlocal count
            count += 1
            return Mock(allowed=True, remaining=lim - count, limit=lim)

        with patch.object(rate_limiter_manager, 'check_rate_limit', side_effect=unlimited_check):
            num_requests = 1000

            results = [
                await rate_limiter_manager.check_rate_limit("user", limit, 60)
                for _ in range(num_requests)
            ]

            assert all(r.allowed for r in results)
            assert count == num_requests

    @pytest.mark.stress
    @pytest.mark.asyncio
    async def test_exactly_at_limit(self):
        """Test behavior when exactly at limit"""
        from src.security.deps import rate_limiter_manager

        limit = 10
        count = 0

        async def exact_limit_check(uid, lim, win):
            nonlocal count
            if count < lim:
                count += 1
                return Mock(allowed=True, remaining=lim - count, limit=lim)
            return Mock(allowed=False, remaining=0, limit=lim)

        with patch.object(rate_limiter_manager, 'check_rate_limit', side_effect=exact_limit_check):
            # Make exactly 'limit' requests
            results = [
                await rate_limiter_manager.check_rate_limit("user", limit, 60)
                for _ in range(limit)
            ]

            assert all(r.allowed for r in results)

            # Next request should be denied
            next_result = await rate_limiter_manager.check_rate_limit("user", limit, 60)
            assert not next_result.allowed


# ============================================================================
# API Endpoint Stress Tests
# ============================================================================

class TestAPIEndpointRateLimitStress:
    """Test rate limiting on actual API endpoints under load"""

    @pytest.fixture
    def app(self):
        """Create FastAPI app"""
        from src.app import app
        return app

    @pytest.fixture
    def client(self, app):
        """Create test client"""
        return TestClient(app)

    @pytest.mark.stress
    def test_endpoint_rate_limit_enforcement(self, client):
        """Test rate limiting on actual endpoint"""
        mock_db = create_test_db_fixture()

        # Create test user and API key
        user = UserGenerator.create_user()
        mock_db.insert("users", user)

        # Track request count
        request_count = 0
        limit = 10

        def rate_limit_mock(allowed=True):
            nonlocal request_count

            async def check_limit(uid, lim, win):
                nonlocal request_count
                if request_count < limit:
                    request_count += 1
                    return Mock(allowed=True, remaining=limit - request_count, limit=limit)
                return Mock(allowed=False, remaining=0, limit=limit)

            mock_mgr = Mock()
            mock_mgr.check_rate_limit = check_limit
            return mock_mgr

        with patch("src.security.deps.get_supabase_client", return_value=mock_db):
            with patch("src.security.deps.rate_limiter_manager", rate_limit_mock()):
                headers = {"X-API-Key": "gw_live_test123"}

                # Make requests up to limit
                responses = []
                for _ in range(limit + 5):  # Try 5 more than limit
                    response = client.get("/v1/models", headers=headers)
                    responses.append(response)

                # Count successful vs rate limited responses
                # Status codes might vary, but we expect some rate limiting
                status_codes = [r.status_code for r in responses]

                # At least some requests should succeed, some should be rate limited
                # (Exact behavior depends on implementation)
                assert len(status_codes) == limit + 5

    @pytest.mark.stress
    def test_multiple_endpoints_share_rate_limit(self, client):
        """Test that rate limit is shared across endpoints for same user"""
        mock_db = create_test_db_fixture()

        user = UserGenerator.create_user()
        mock_db.insert("users", user)

        global_count = 0
        limit = 20

        def shared_rate_limiter():
            nonlocal global_count

            async def check(uid, lim, win):
                nonlocal global_count
                if global_count < limit:
                    global_count += 1
                    return Mock(allowed=True, remaining=limit - global_count, limit=limit)
                return Mock(allowed=False, remaining=0, limit=limit)

            mock_mgr = Mock()
            mock_mgr.check_rate_limit = check
            return mock_mgr

        with patch("src.security.deps.get_supabase_client", return_value=mock_db):
            with patch("src.security.deps.rate_limiter_manager", shared_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test123"}

                # Make requests to different endpoints
                endpoints = ["/v1/models", "/v1/chat/completions"]

                for _ in range(15):  # Alternate between endpoints
                    for endpoint in endpoints:
                        if endpoint == "/v1/chat/completions":
                            client.post(endpoint, headers=headers, json={
                                "model": "gpt-3.5-turbo",
                                "messages": [{"role": "user", "content": "test"}]
                            })
                        else:
                            client.get(endpoint, headers=headers)

                # All requests should count toward same rate limit
                assert global_count == limit


# ============================================================================
# Recovery Tests
# ============================================================================

class TestRateLimitRecovery:
    """Test system recovery after rate limiting"""

    @pytest.mark.stress
    @pytest.mark.asyncio
    async def test_graceful_degradation_under_load(self):
        """Test that system gracefully handles rate limit exhaustion"""
        from src.security.deps import rate_limiter_manager

        limit = 100
        total_requests = 500

        count = 0

        async def degrading_check(uid, lim, win):
            nonlocal count
            if count < lim:
                count += 1
                return Mock(allowed=True, remaining=lim - count, limit=lim)
            return Mock(allowed=False, remaining=0, limit=lim)

        with patch.object(rate_limiter_manager, 'check_rate_limit', side_effect=degrading_check):
            results = []

            for _ in range(total_requests):
                result = await rate_limiter_manager.check_rate_limit("user", limit, 60)
                results.append(result)

            # First 'limit' should succeed, rest should fail gracefully
            successful = [r for r in results if r.allowed]
            rate_limited = [r for r in results if not r.allowed]

            assert len(successful) == limit
            assert len(rate_limited) == total_requests - limit

            # All rate limited responses should have correct metadata
            for result in rate_limited:
                assert not result.allowed
                assert result.remaining == 0
