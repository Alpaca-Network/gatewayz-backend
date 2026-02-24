"""
Tests for Redis metrics service.

This module tests the RedisMetrics service that provides:
- Request metrics recording
- Latency tracking
- Error tracking
- Provider health scores
- Circuit breaker state sync
"""

import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.services.redis_metrics import RedisMetrics, RequestMetrics


@pytest.fixture
def mock_redis_client():
    """Mock Redis client"""
    client = Mock()

    # Mock pipeline
    pipeline = Mock()
    pipeline.hincrby = Mock(return_value=pipeline)
    pipeline.hincrbyfloat = Mock(return_value=pipeline)
    pipeline.expire = Mock(return_value=pipeline)
    pipeline.zadd = Mock(return_value=pipeline)
    pipeline.zremrangebyscore = Mock(return_value=pipeline)
    pipeline.lpush = Mock(return_value=pipeline)
    pipeline.ltrim = Mock(return_value=pipeline)
    pipeline.execute = AsyncMock(return_value=[])

    client.pipeline = Mock(return_value=pipeline)

    # Mock get operations (with decode_responses=True behavior - string keys/values)
    client.zscore = Mock(return_value=85.0)
    client.zrevrange = Mock(return_value=[("openrouter", 95.0), ("portkey", 85.0)])
    client.lrange = Mock(
        return_value=[
            json.dumps(
                {
                    "model": "gpt-4",
                    "error": "Rate limit exceeded",
                    "timestamp": time.time(),
                    "latency_ms": 1500,
                }
            )
        ]
    )
    client.hgetall = Mock(
        return_value={
            "total_requests": "1000",
            "successful_requests": "950",
            "failed_requests": "50",
            "tokens_input": "50000",
            "tokens_output": "25000",
            "total_cost": "12.5",
        }
    )
    client.zrange = Mock(return_value=["500", "550", "600", "800"])
    client.scan_iter = Mock(
        return_value=iter(["metrics:openrouter:2025-11-27:14", "metrics:openrouter:2025-11-27:13"])
    )
    client.delete = Mock(return_value=1)

    return client


@pytest.fixture
def redis_metrics(mock_redis_client):
    """Create RedisMetrics instance with mocked client"""
    return RedisMetrics(redis_client=mock_redis_client)


class TestRecordRequest:
    """Test request recording functionality"""

    @pytest.mark.asyncio
    async def test_record_successful_request(self, redis_metrics, mock_redis_client):
        """Test recording a successful request"""
        await redis_metrics.record_request(
            provider="openrouter",
            model="gpt-4",
            latency_ms=500,
            success=True,
            cost=0.05,
            tokens_input=100,
            tokens_output=50,
        )

        # Verify pipeline was called
        pipeline = mock_redis_client.pipeline.return_value
        assert pipeline.hincrby.called
        assert pipeline.hincrbyfloat.called
        assert pipeline.zadd.called
        assert pipeline.execute.called

    @pytest.mark.asyncio
    async def test_record_failed_request(self, redis_metrics, mock_redis_client):
        """Test recording a failed request with error"""
        await redis_metrics.record_request(
            provider="openrouter",
            model="gpt-4",
            latency_ms=1500,
            success=False,
            cost=0.0,
            error_message="Rate limit exceeded",
        )

        # Verify error was recorded
        pipeline = mock_redis_client.pipeline.return_value
        assert pipeline.lpush.called
        assert pipeline.ltrim.called

    @pytest.mark.asyncio
    async def test_record_request_disabled(self):
        """Test recording when Redis is disabled"""
        redis_metrics = RedisMetrics(redis_client=None)

        # Should not raise exception
        await redis_metrics.record_request(
            provider="openrouter", model="gpt-4", latency_ms=500, success=True, cost=0.05
        )

    @pytest.mark.asyncio
    async def test_record_request_exception_handling(self, redis_metrics, mock_redis_client):
        """Test exception handling during recording"""
        mock_redis_client.pipeline.return_value.execute.side_effect = Exception("Redis error")

        # Should not raise exception (logged as warning)
        await redis_metrics.record_request(
            provider="openrouter", model="gpt-4", latency_ms=500, success=True, cost=0.05
        )


class TestProviderHealth:
    """Test provider health tracking"""

    @pytest.mark.asyncio
    async def test_get_provider_health(self, redis_metrics, mock_redis_client):
        """Test getting provider health score"""
        score = await redis_metrics.get_provider_health("openrouter")

        assert score == 85.0
        mock_redis_client.zscore.assert_called_once_with("provider_health", "openrouter")

    @pytest.mark.asyncio
    async def test_get_provider_health_no_data(self, redis_metrics, mock_redis_client):
        """Test getting health score when no data exists"""
        mock_redis_client.zscore.return_value = None

        score = await redis_metrics.get_provider_health("new_provider")

        assert score == 100.0  # Default score

    @pytest.mark.asyncio
    async def test_get_all_provider_health(self, redis_metrics, mock_redis_client):
        """Test getting all provider health scores"""
        health_scores = await redis_metrics.get_all_provider_health()

        assert health_scores == {"openrouter": 95.0, "portkey": 85.0}


class TestRecentErrors:
    """Test error tracking"""

    @pytest.mark.asyncio
    async def test_get_recent_errors(self, redis_metrics, mock_redis_client):
        """Test getting recent errors"""
        errors = await redis_metrics.get_recent_errors("openrouter", limit=100)

        assert len(errors) == 1
        assert errors[0]["model"] == "gpt-4"
        assert errors[0]["error"] == "Rate limit exceeded"
        mock_redis_client.lrange.assert_called_once_with("errors:openrouter", 0, 99)

    @pytest.mark.asyncio
    async def test_get_recent_errors_disabled(self):
        """Test getting errors when Redis is disabled"""
        redis_metrics = RedisMetrics(redis_client=None)

        errors = await redis_metrics.get_recent_errors("openrouter")

        assert errors == []


class TestHourlyStats:
    """Test hourly statistics"""

    @pytest.mark.asyncio
    async def test_get_hourly_stats(self, redis_metrics, mock_redis_client):
        """Test getting hourly statistics"""
        stats = await redis_metrics.get_hourly_stats("openrouter", hours=2)

        assert len(stats) > 0
        # Stats should be keyed by hour
        for hour_key, hour_data in stats.items():
            assert "total_requests" in hour_data
            assert "successful_requests" in hour_data
            assert "failed_requests" in hour_data
            assert "total_cost" in hour_data

    @pytest.mark.asyncio
    async def test_get_hourly_stats_disabled(self):
        """Test getting stats when Redis is disabled"""
        redis_metrics = RedisMetrics(redis_client=None)

        stats = await redis_metrics.get_hourly_stats("openrouter", hours=24)

        assert stats == {}


class TestLatencyPercentiles:
    """Test latency percentile calculations"""

    @pytest.mark.asyncio
    async def test_get_latency_percentiles(self, redis_metrics, mock_redis_client):
        """Test calculating latency percentiles"""
        result = await redis_metrics.get_latency_percentiles(
            "openrouter", "gpt-4", percentiles=[50, 95, 99]
        )

        assert "count" in result
        assert "avg" in result
        assert "p50" in result
        assert "p95" in result
        assert "p99" in result
        assert result["count"] == 4  # 4 values in mock

    @pytest.mark.asyncio
    async def test_get_latency_percentiles_no_data(self, redis_metrics, mock_redis_client):
        """Test percentiles when no data exists"""
        mock_redis_client.zrange.return_value = []

        result = await redis_metrics.get_latency_percentiles(
            "openrouter", "gpt-4", percentiles=[50, 95, 99]
        )

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_latency_percentiles_disabled(self):
        """Test percentiles when Redis is disabled"""
        redis_metrics = RedisMetrics(redis_client=None)

        result = await redis_metrics.get_latency_percentiles(
            "openrouter", "gpt-4", percentiles=[50, 95, 99]
        )

        assert result == {}


class TestCircuitBreaker:
    """Test circuit breaker state management"""

    @pytest.mark.asyncio
    async def test_update_circuit_breaker(self, redis_metrics, mock_redis_client):
        """Test updating circuit breaker state"""
        mock_redis_client.setex = Mock()

        await redis_metrics.update_circuit_breaker(
            provider="openrouter", model="gpt-4", state="OPEN", failure_count=5
        )

        mock_redis_client.setex.assert_called_once()
        args = mock_redis_client.setex.call_args
        assert args[0][0] == "circuit:openrouter:gpt-4"
        assert args[0][1] == 300  # TTL
        # Third arg is JSON data
        data = json.loads(args[0][2])
        assert data["state"] == "OPEN"
        assert data["failure_count"] == 5

    @pytest.mark.asyncio
    async def test_update_circuit_breaker_disabled(self):
        """Test updating circuit breaker when Redis is disabled"""
        redis_metrics = RedisMetrics(redis_client=None)

        # Should not raise exception
        await redis_metrics.update_circuit_breaker(
            provider="openrouter", model="gpt-4", state="OPEN", failure_count=5
        )


class TestCleanup:
    """Test old data cleanup"""

    @pytest.mark.asyncio
    async def test_cleanup_old_data(self, redis_metrics, mock_redis_client):
        """Test cleaning up old Redis data"""
        await redis_metrics.cleanup_old_data(hours=2)

        # Should scan for metrics keys
        mock_redis_client.scan_iter.assert_called_once()
        # Should delete old keys
        assert mock_redis_client.delete.called

    @pytest.mark.asyncio
    async def test_cleanup_old_data_disabled(self):
        """Test cleanup when Redis is disabled"""
        redis_metrics = RedisMetrics(redis_client=None)

        # Should not raise exception
        await redis_metrics.cleanup_old_data(hours=2)


class TestRequestMetricsDataclass:
    """Test RequestMetrics dataclass"""

    def test_request_metrics_creation(self):
        """Test creating RequestMetrics instance"""
        metrics = RequestMetrics(
            provider="openrouter",
            model="gpt-4",
            latency_ms=500,
            success=True,
            cost=0.05,
            tokens_input=100,
            tokens_output=50,
            timestamp=time.time(),
            error_message=None,
        )

        assert metrics.provider == "openrouter"
        assert metrics.model == "gpt-4"
        assert metrics.latency_ms == 500
        assert metrics.success is True
        assert metrics.cost == 0.05

    def test_request_metrics_with_error(self):
        """Test creating RequestMetrics with error"""
        metrics = RequestMetrics(
            provider="openrouter",
            model="gpt-4",
            latency_ms=1500,
            success=False,
            cost=0.0,
            tokens_input=0,
            tokens_output=0,
            timestamp=time.time(),
            error_message="Rate limit exceeded",
        )

        assert metrics.success is False
        assert metrics.error_message == "Rate limit exceeded"
